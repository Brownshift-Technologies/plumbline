import { useState, type FormEvent } from "react";
import { Field } from "../../components/Field";
import { Button } from "../../components/Button";
import { Icon } from "../../components/Icon";
import { EmptyState } from "../../components/EmptyState";
import { SkeletonBlock } from "../../components/Skeleton";
import { useToast } from "../../components/Toast";
import { api } from "../../lib/api";
import { useAsync } from "../../lib/useAsync";
import type { AsyncState } from "../../lib/useAsync";
import { isDemoWrite, demoWriteMessage } from "../../lib/demo";
import type { CurrentUser, Member, Role } from "../../lib/types";

const ROLE_LABEL: Record<Role, string> = { owner: "Owner", approver: "Approver", reader: "Read only" };
const ROLES: Role[] = ["owner", "approver", "reader"];

function InviteForm({ onInvited }: { onInvited: () => void }) {
  const { show } = useToast();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("reader");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSending(true);
    try {
      const res = await api.post("/members/invite", { email, role });
      show(isDemoWrite(res) ? demoWriteMessage(res, `${email} would be invited`) : `Invited ${email}.`);
      setEmail("");
      setOpen(false);
      onInvited();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't send that invite.");
    } finally {
      setSending(false);
    }
  }

  if (!open) {
    return (
      <Button variant="pri" size="sm" onClick={() => setOpen(true)}>
        <Icon name="i-plus" size="xs" /> Invite
      </Button>
    );
  }

  return (
    <form onSubmit={onSubmit} style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
      {error && (
        <p role="alert" style={{ color: "var(--fail)", fontSize: 13, position: "absolute", marginTop: -26 }}>
          {error}
        </p>
      )}
      <Field label="Email to invite" type="email" value={email} onChange={(e) => setEmail(e.target.value)} wrapperStyle={{ margin: 0 }} />
      <div className="field" style={{ margin: 0 }}>
        <label htmlFor="invite-role">Role</label>
        <select id="invite-role" value={role} onChange={(e) => setRole(e.target.value as Role)} style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 7 }}>
          {ROLES.map((r) => (
            <option key={r} value={r}>{ROLE_LABEL[r]}</option>
          ))}
        </select>
      </div>
      <Button type="submit" variant="pri" size="sm" disabled={sending}>
        {sending ? "Sending…" : "Send invite"}
      </Button>
      <Button type="button" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
    </form>
  );
}

export function MembersPane({ user }: { user: AsyncState<CurrentUser> }) {
  const { show } = useToast();
  const members = useAsync<Member[]>(() => api.get<Member[]>("/members"), []);
  const rows = members.status === "success" ? members.data ?? [] : [];
  const canManage = user.data?.role === "owner";

  async function changeRole(m: Member, role: Role) {
    try {
      const res = await api.patch(`/members/${m.id}`, { role });
      show(isDemoWrite(res) ? demoWriteMessage(res, `${m.name}'s role would change to ${ROLE_LABEL[role]}`) : `${m.name}'s role changed to ${ROLE_LABEL[role]}.`);
      members.reload();
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't change that role.");
    }
  }

  async function remove(m: Member) {
    try {
      const res = await api.del(`/members/${m.id}`);
      show(isDemoWrite(res) ? demoWriteMessage(res, `${m.name} would be removed`) : `${m.name} removed.`);
      members.reload();
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't remove that member.");
    }
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "20px 0 14px" }}>
        <div>
          <h3 style={{ fontSize: 15, fontWeight: 600 }}>{rows.length} member{rows.length === 1 ? "" : "s"}</h3>
        </div>
        <span className="sp" />
        <InviteForm onInvited={members.reload} />
      </div>

      {members.status === "loading" && (
        <div className="panel" aria-busy="true">
          <span className="visually-hidden" role="status">Loading members…</span>
          <table>
            <tbody>
              {[0, 1, 2].map((i) => (
                <tr key={i} data-skeleton-row="">
                  <td><SkeletonBlock width="55%" /></td>
                  <td style={{ width: 150 }}><SkeletonBlock width="70%" /></td>
                  <td style={{ width: 150 }}><SkeletonBlock width="60%" /></td>
                  <td style={{ width: 90 }}><SkeletonBlock width={60} height={26} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {members.status === "error" && (
        <EmptyState variant="error" title="Couldn't load members" description={members.error} actions={<Button onClick={members.reload}>Retry</Button>} />
      )}
      {members.status === "success" && rows.length === 0 && (
        <EmptyState variant="empty" title="Just you, for now" description="Invite your team to review findings and approve patches together." />
      )}
      {members.status === "success" && rows.length > 0 && (
        <div className="panel">
          <table>
            <thead>
              <tr>
                <th>Member</th>
                <th style={{ width: 150 }}>Role</th>
                <th style={{ width: 150 }}>Last active</th>
                <th style={{ width: 90 }} />
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => {
                const isSelf = m.user_id === user.data?.id;
                // Two DISTINCT reasons, one per control -- a shared reason
                // string previously let "You cannot change your own role"
                // show up on the Remove button for a self row, which is
                // simply the wrong explanation for that control.
                const roleDisabledReason = !canManage
                  ? "Only an owner can change roles."
                  : isSelf
                    ? "You cannot change your own role."
                    : undefined;
                const removeDisabledReason = !canManage
                  ? "Only an owner can remove a member."
                  : isSelf
                    ? "You cannot remove yourself."
                    : undefined;
                const roleHintId = `role-reason-${m.id}`;
                const removeHintId = `remove-reason-${m.id}`;
                return (
                  <tr key={m.id}>
                    <td>
                      <span className="who">
                        <span className="ava">{m.name.slice(0, 2).toUpperCase()}</span>
                        <span>
                          <b style={{ fontWeight: 600 }}>{m.name}</b>
                          <div style={{ fontSize: 12.5, color: "var(--faint)" }}>{m.email}</div>
                        </span>
                      </span>
                    </td>
                    <td>
                      {/* Always a real, operable control -- disabled and
                          explained rather than swapped out for a static
                          pill, so the permission model stays visible and
                          keyboard-reachable even when it can't be used. */}
                      <select
                        aria-label={`Role for ${m.name}`}
                        value={m.role}
                        onChange={(e) => changeRole(m, e.target.value as Role)}
                        disabled={Boolean(roleDisabledReason)}
                        title={roleDisabledReason}
                        aria-describedby={roleDisabledReason ? roleHintId : undefined}
                        style={{ padding: "5px 8px", border: "1px solid var(--line)", borderRadius: 6 }}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>{ROLE_LABEL[r]}</option>
                        ))}
                      </select>
                      {roleDisabledReason && (
                        <span id={roleHintId} className="visually-hidden">{roleDisabledReason}</span>
                      )}
                    </td>
                    <td style={{ color: "var(--muted)" }}>{m.last_active ? new Date(m.last_active * 1000).toLocaleDateString() : "—"}</td>
                    <td>
                      <Button
                        size="sm"
                        variant="dang"
                        onClick={() => remove(m)}
                        disabled={Boolean(removeDisabledReason)}
                        title={removeDisabledReason}
                        aria-describedby={removeDisabledReason ? removeHintId : undefined}
                      >
                        Remove
                      </Button>
                      {removeDisabledReason && (
                        <span id={removeHintId} className="visually-hidden">{removeDisabledReason}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

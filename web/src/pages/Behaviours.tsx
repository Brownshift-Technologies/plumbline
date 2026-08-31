import { useState, type FormEvent } from "react";
import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { Field } from "../components/Field";
import { EmptyState } from "../components/EmptyState";
import { Table, type TableColumn } from "../components/Table";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { useCurrentUser } from "../lib/useCurrentUser";
import { isDemoWrite, demoWriteMessage } from "../lib/demo";
import type { Behaviour } from "../lib/types";

interface DraftBehaviour {
  id?: string;
  text: string;
  route: string;
  tags: string;
}

const EMPTY_DRAFT: DraftBehaviour = { text: "", route: "", tags: "" };

function BehaviourForm({
  draft,
  onChange,
  onSubmit,
  onCancel,
  error,
  saving,
}: {
  draft: DraftBehaviour;
  onChange: (d: DraftBehaviour) => void;
  onSubmit: (e: FormEvent) => void;
  onCancel: () => void;
  error: string | null;
  saving: boolean;
}) {
  return (
    <form onSubmit={onSubmit} style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
      {error && (
        <p role="alert" style={{ color: "var(--fail)", fontSize: 13.5, marginBottom: 10 }}>
          {error}
        </p>
      )}
      <Field
        label="Behaviour"
        value={draft.text}
        onChange={(e) => onChange({ ...draft, text: e.target.value })}
        placeholder="A customer who retries a slow payment should only be charged once"
      />
      <Field
        label="Route"
        value={draft.route}
        onChange={(e) => onChange({ ...draft, route: e.target.value })}
        placeholder="/checkout/payment"
      />
      <Field
        label="Tags (comma separated)"
        value={draft.tags}
        onChange={(e) => onChange({ ...draft, tags: e.target.value })}
        placeholder="payments, retries"
      />
      <div style={{ display: "flex", gap: 8 }}>
        <Button type="submit" variant="pri" disabled={saving}>
          {saving ? "Saving…" : draft.id ? "Save changes" : "Create behaviour"}
        </Button>
        <Button type="button" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function Behaviours() {
  const { show } = useToast();
  const { data: user } = useCurrentUser();
  const [tag, setTag] = useState("");
  const [owner, setOwner] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState<DraftBehaviour>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const list = useAsync<Behaviour[]>(() => {
    const params = new URLSearchParams();
    if (tag.trim()) params.set("tag", tag.trim());
    if (owner.trim()) params.set("owner", owner.trim());
    const qs = params.toString();
    return api.get<Behaviour[]>(`/behaviours${qs ? `?${qs}` : ""}`);
  }, [tag, owner]);

  const filtersActive = Boolean(tag || owner);
  const rows = list.status === "success" ? list.data ?? [] : [];

  const needsTotal = list.status === "success" && rows.length === 0 && filtersActive;
  const totalQuery = useAsync<Behaviour[]>(
    () => (needsTotal ? api.get<Behaviour[]>("/behaviours") : Promise.resolve([])),
    [needsTotal],
  );

  function openCreate() {
    setDraft(EMPTY_DRAFT);
    setFormError(null);
    setFormOpen(true);
  }

  function openEdit(b: Behaviour) {
    setDraft({ id: b.id, text: b.text, route: b.route, tags: b.tags.join(", ") });
    setFormError(null);
    setFormOpen(true);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!draft.text.trim() || !draft.route.trim()) {
      setFormError("A behaviour needs both text and a route.");
      return;
    }
    setSaving(true);
    const body = {
      text: draft.text.trim(),
      route: draft.route.trim(),
      tags: draft.tags.split(",").map((t) => t.trim()).filter(Boolean),
    };
    try {
      const res = draft.id
        ? await api.patch(`/behaviours/${draft.id}`, body)
        : await api.post("/behaviours", body);
      if (isDemoWrite(res)) {
        show(demoWriteMessage(draft.id ? "this behaviour would be updated" : "this behaviour would be created"));
      } else {
        show(draft.id ? "Behaviour updated." : "Behaviour created.");
      }
      setFormOpen(false);
      list.reload();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Couldn't save this behaviour.");
    } finally {
      setSaving(false);
    }
  }

  async function onDelete(b: Behaviour) {
    try {
      const res = await api.del(`/behaviours/${b.id}`);
      show(isDemoWrite(res) ? demoWriteMessage("this behaviour would be deleted") : "Behaviour deleted.");
      list.reload();
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't delete this behaviour.");
    }
  }

  const canDelete = user?.role === "owner";

  const columns: TableColumn<Behaviour>[] = [
    { key: "text", header: "Behaviour", label: "Behaviour", primary: true, render: (b) => b.text },
    { key: "route", header: "Route", label: "Route", width: "160px", render: (b) => <span className="mono" style={{ color: "var(--muted)" }}>{b.route}</span> },
    { key: "tags", header: "Tags", label: "Tags", width: "180px", render: (b) => b.tags.map((t) => <Pill key={t} kind="grey" dot={false}>{t}</Pill>) },
    { key: "owner", header: "Owner", label: "Owner", width: "120px", render: (b) => b.owner || "—" },
    {
      key: "actions",
      header: "",
      label: "Actions",
      width: "110px",
      render: (b) => (
        <div style={{ display: "flex", gap: 6 }}>
          <Button size="sm" onClick={() => openEdit(b)}>Edit</Button>
          <Button
            size="sm"
            variant="dang"
            onClick={() => onDelete(b)}
            disabled={!canDelete}
            title={canDelete ? undefined : "Only an owner can delete a behaviour."}
            aria-describedby={canDelete ? undefined : `delete-reason-${b.id}`}
          >
            Delete
          </Button>
          {!canDelete && (
            <span id={`delete-reason-${b.id}`} className="visually-hidden">
              Only an owner can delete a behaviour.
            </span>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Behaviours</h1>
          <p>Everything Plumbline checks, written in plain English.</p>
        </div>
        <span className="sp" />
        <Button variant="pri" size="sm" onClick={openCreate}>
          <Icon name="i-plus" size="xs" /> New behaviour
        </Button>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", padding: "10px 0" }}>
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="behaviour-tag">Tag</label>
          <input id="behaviour-tag" type="text" value={tag} onChange={(e) => setTag(e.target.value)} placeholder="payments" />
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="behaviour-owner">Owner</label>
          <input id="behaviour-owner" type="text" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="you@company.com" />
        </div>
      </div>

      <Panel style={{ marginTop: 8 }}>
        {formOpen && (
          <BehaviourForm
            draft={draft}
            onChange={setDraft}
            onSubmit={onSubmit}
            onCancel={() => setFormOpen(false)}
            error={formError}
            saving={saving}
          />
        )}
        {list.status === "loading" && (
          <Table columns={columns} rows={[]} getRowKey={(b) => b.id} skeletonRows={5} caption="Loading behaviours" />
        )}
        {list.status === "error" && (
          <EmptyState
            variant="error"
            title="Couldn't load behaviours"
            description={list.error}
            actions={<Button onClick={list.reload}>Retry</Button>}
          />
        )}
        {list.status === "success" && rows.length === 0 && (
          <EmptyState
            title={filtersActive ? "No filters match" : "No behaviours yet"}
            description={
              filtersActive ? (
                totalQuery.status === "success" ? (
                  <>
                    {totalQuery.data?.length ?? 0} behaviours exist for this repository, but none are tagged{" "}
                    {tag ? <span className="mono">{tag}</span> : "that"} and owned by {owner || "you"}.
                  </>
                ) : (
                  "Checking how many behaviours exist for this repository…"
                )
              ) : (
                "Describe a behaviour on Home, or import existing Playwright specs, to get started."
              )
            }
            actions={
              filtersActive ? (
                <>
                  <Button
                    onClick={() => {
                      setTag("");
                      setOwner("");
                    }}
                  >
                    Clear filters
                  </Button>
                  <Button variant="pri" onClick={openCreate}>
                    <Icon name="i-plus" size="xs" /> New behaviour
                  </Button>
                </>
              ) : (
                <Button variant="pri" onClick={openCreate}>
                  <Icon name="i-plus" size="xs" /> New behaviour
                </Button>
              )
            }
          />
        )}
        {list.status === "success" && rows.length > 0 && (
          <Table columns={columns} rows={rows} getRowKey={(b) => b.id} />
        )}
      </Panel>
    </div>
  );
}

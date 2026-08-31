import { useState, type FormEvent } from "react";
import { Field } from "../../components/Field";
import { Button } from "../../components/Button";
import { EmptyState } from "../../components/EmptyState";
import { SkeletonBlock } from "../../components/Skeleton";
import { useToast } from "../../components/Toast";
import { ApiError, api } from "../../lib/api";
import { useAsync } from "../../lib/useAsync";
import type { AsyncState } from "../../lib/useAsync";
import { isDemoWrite, demoWriteMessage } from "../../lib/demo";
import type { CurrentUser } from "../../lib/types";

interface WorkspaceSettings {
  target_url: string;
}

/**
 * Tier 2 (2026-08-30 contract, item 6): the application under test.
 *
 * Validated server-side (`app/workspace_routes.py`'s `PUT /api/workspace/
 * target-url`, itself built on `agents.cartographer.validate_target_url`)
 * -- this pane's own job is only ever to surface whatever the server
 * says inline, never to duplicate that judgement client-side. A run with
 * this unset fails loudly on its own (`agents/cartographer.py`); this
 * pane is where a customer avoids ever finding that out from a failed
 * run instead of from a form.
 */
export function WorkspacePane({ user }: { user: AsyncState<CurrentUser> }) {
  const { show } = useToast();
  const workspace = useAsync<WorkspaceSettings>(() => api.get<WorkspaceSettings>("/workspace"), []);
  const canManage = user.data?.role === "owner";

  const [value, setValue] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const current = workspace.status === "success" ? workspace.data?.target_url ?? "" : "";
  const draft = value ?? current;
  const dirty = workspace.status === "success" && draft !== current;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const res = await api.put<WorkspaceSettings & { demo?: boolean; persisted?: boolean }>(
        "/workspace/target-url",
        { target_url: draft },
      );
      show(isDemoWrite(res) ? demoWriteMessage(res, "your target URL would change") : "Target URL saved.");
      setValue(null);
      workspace.reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setError(err.message);
      } else {
        show(err instanceof Error ? err.message : "Couldn't save the target URL.");
      }
    } finally {
      setSaving(false);
    }
  }

  if (workspace.status === "loading") {
    return (
      <div aria-busy="true">
        <span className="visually-hidden" role="status">Loading workspace settings…</span>
        <div className="setrow">
          <div><SkeletonBlock width="30%" height={16} /></div>
          <div style={{ maxWidth: 370 }}><SkeletonBlock width="100%" height={38} /></div>
        </div>
      </div>
    );
  }
  if (workspace.status === "error") {
    return (
      <EmptyState variant="error" title="Couldn't load workspace settings" description={workspace.error}
        actions={<Button onClick={workspace.reload}>Retry</Button>} />
    );
  }

  return (
    <div>
      <div className="setrow">
        <div>
          <h3>Target URL</h3>
          <p>
            The application the fleet tests. Every run crawls from here -- leave it unset and a run fails
            immediately, with a step explaining why, rather than reporting a clean pass on nothing.
          </p>
        </div>
        <form onSubmit={onSubmit} style={{ maxWidth: 370, width: "100%" }}>
          <Field
            label="Target URL"
            type="url"
            placeholder="https://app.example.com"
            value={draft}
            onChange={(e) => setValue(e.target.value)}
            hint={error ?? undefined}
            invalid={Boolean(error)}
            disabled={!canManage}
            title={canManage ? undefined : "Only an owner can change the target URL."}
            wrapperStyle={{ margin: 0 }}
          />
          <div style={{ marginTop: 11, display: "flex", gap: 8 }}>
            <Button type="submit" variant="pri" size="sm" disabled={!canManage || saving || !dirty}>
              {saving ? "Saving…" : "Save"}
            </Button>
            {dirty && (
              <Button type="button" size="sm" onClick={() => { setValue(null); setError(null); }}>
                Cancel
              </Button>
            )}
          </div>
          {!canManage && (
            <span className="visually-hidden">Only an owner can change the target URL.</span>
          )}
        </form>
      </div>
    </div>
  );
}

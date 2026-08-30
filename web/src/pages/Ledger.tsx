import { useState } from "react";
import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { EmptyState } from "../components/EmptyState";
import { Table, type TableColumn } from "../components/Table";
import { api, API_BASE, ApiError } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import type { LedgerEntry, LedgerListResponse, LedgerVerifyResponse } from "../lib/types";

function VerifyChain() {
  const [state, setState] = useState<"idle" | "checking" | "intact" | "tampered" | "error">("idle");
  const [checked, setChecked] = useState(0);
  const [error, setError] = useState<string | null>(null);

  async function verify() {
    setState("checking");
    setError(null);
    try {
      const res = await api.get<LedgerVerifyResponse>("/ledger/verify");
      setChecked(res.checked);
      setState(res.intact ? "intact" : "tampered");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the verification endpoint.");
      setState("error");
    }
  }

  return (
    <div
      className="panel"
      style={{ marginTop: 18, padding: "16px 17px", display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}
    >
      <div style={{ flex: 1, minWidth: 240 }}>
        <b style={{ fontSize: 14.5 }}>Verify the chain</b>
        <p style={{ marginTop: 4, fontSize: 13.5, color: "var(--muted)", lineHeight: 1.55 }}>
          Re-signs every entry and checks it against the next. Anyone can run this -- it does not trust the ledger,
          it proves it.
        </p>
      </div>
      <Button variant="pri" onClick={verify} disabled={state === "checking"}>
        {state === "checking" ? "Verifying…" : "Verify chain"}
      </Button>
      {state === "intact" && (
        <Pill kind="pass">
          Chain intact · {checked} entries checked
        </Pill>
      )}
      {state === "tampered" && (
        <Pill kind="fail">Chain tampered -- do not trust this ledger</Pill>
      )}
      {state === "error" && (
        <span role="alert" style={{ fontSize: 13.5, color: "var(--fail)" }}>
          {error}
        </span>
      )}
    </div>
  );
}

export function Ledger() {
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const cursor = cursorStack[cursorStack.length - 1];

  const ledger = useAsync<LedgerListResponse>(() => {
    const params = new URLSearchParams();
    params.set("limit", "20");
    if (cursor) params.set("cursor", cursor);
    return api.get<LedgerListResponse>(`/ledger?${params.toString()}`);
  }, [cursor]);

  const rows = ledger.status === "success" ? ledger.data?.entries ?? [] : [];

  const columns: TableColumn<LedgerEntry>[] = [
    { key: "when", header: "When", label: "When", primary: true, width: "170px", render: (e) => <span style={{ color: "var(--muted)" }}>{new Date(e.at * 1000).toLocaleString()}</span> },
    { key: "who", header: "Who", label: "Who", width: "140px", render: (e) => e.actor },
    { key: "what", header: "What", label: "What", render: (e) => e.action },
    { key: "signature", header: "Signature", label: "Signature", width: "180px", render: (e) => <span className="mono" style={{ color: "var(--faint)" }}>{e.signature.slice(0, 4)}…{e.signature.slice(-4)}</span> },
  ];

  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Audit ledger</h1>
          <p>Append-only. Nothing here can be edited or deleted, including by an owner.</p>
        </div>
        <span className="sp" />
        <a className="btn sm" href={`${API_BASE}/ledger.csv`}>
          <Icon name="i-import" size="xs" /> Export
        </a>
      </div>

      <VerifyChain />

      <Panel style={{ marginTop: 18 }}>
        {ledger.status === "loading" && <EmptyState variant="loading" title="Loading the ledger…" />}
        {ledger.status === "error" && (
          <EmptyState
            variant="error"
            title="Couldn't load the ledger"
            description={ledger.error}
            actions={<Button onClick={ledger.reload}>Retry</Button>}
          />
        )}
        {ledger.status === "success" && rows.length === 0 && (
          <EmptyState variant="empty" icon="i-ledger" title="Nothing recorded yet" description="Every approval, rejection and policy change will show up here." />
        )}
        {ledger.status === "success" && rows.length > 0 && (
          <>
            <Table columns={columns} rows={rows} getRowKey={(e) => e.signature} />
            <div className="pager">
              Showing {rows.length} entries
              <span className="sp" />
              <button className="pbtn" onClick={() => setCursorStack((s) => (s.length > 1 ? s.slice(0, -1) : s))} disabled={cursorStack.length <= 1} aria-label="Previous page">
                <Icon name="i-chev-l" size="xs" />
              </button>
              <button
                className="pbtn"
                onClick={() => ledger.data?.next_cursor && setCursorStack((s) => [...s, ledger.data!.next_cursor])}
                disabled={!ledger.data?.next_cursor}
                aria-label="Next page"
              >
                <Icon name="i-chev-r" size="xs" />
              </button>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}

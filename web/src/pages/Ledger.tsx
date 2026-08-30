import { Panel } from "../components/Panel";
import { Button } from "../components/Button";
import { useToast } from "../components/Toast";
import { Table, type TableColumn } from "../components/Table";

interface Entry {
  when: string;
  who: string;
  what: string;
  signature: string;
}

const ENTRIES: Entry[] = [
  { when: "Today 14:06:29", who: "Surgeon", what: "Opened pull request #2211 with a 7-line patch", signature: "a41f…9c02" },
  { when: "Today 14:05:30", who: "Triager", what: "Reproduced the payment failure 5 times", signature: "7b8e…31da" },
  { when: "Today 09:14:02", who: "Roger K.", what: "Changed gate rule for payments/* to require an owner", signature: "de19…44b1" },
];

const columns: TableColumn<Entry>[] = [
  { key: "when", header: "When", label: "When", primary: true, width: "150px", render: (e) => <span style={{ color: "var(--muted)" }}>{e.when}</span> },
  { key: "who", header: "Who", label: "Who", width: "140px", render: (e) => e.who },
  { key: "what", header: "What", label: "What", render: (e) => e.what },
  { key: "signature", header: "Signature", label: "Signature", width: "180px", render: (e) => <span className="mono" style={{ color: "var(--faint)" }}>{e.signature}</span> },
];

export function Ledger() {
  const { show } = useToast();

  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Audit ledger</h1>
          <p>Append-only. Nothing here can be edited or deleted, including by an owner.</p>
        </div>
        <span className="sp" />
        <Button size="sm" onClick={() => show(`Exporting ${ENTRIES.length} entries as CSV`)}>
          Export
        </Button>
      </div>
      <Panel style={{ marginTop: 18 }}>
        <Table columns={columns} rows={ENTRIES} getRowKey={(e) => e.signature} />
      </Panel>
    </div>
  );
}

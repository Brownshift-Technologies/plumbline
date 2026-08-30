export type DiffLineKind = "hunk" | "ctx" | "add" | "del";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

/**
 * Parses a unified diff (`@@ -a,b +c,d @@`, `+`/`-`/` ` prefixed lines) into
 * the line records `<Diff/>` renders. Anything that isn't a diff line
 * (a blank separator, a `---`/`+++` file header) is dropped rather than
 * guessed at.
 */
export function parseUnifiedDiff(patch: string): DiffLine[] {
  const lines: DiffLine[] = [];
  for (const raw of patch.split("\n")) {
    if (raw.startsWith("@@")) {
      lines.push({ kind: "hunk", text: raw });
    } else if (raw.startsWith("+++") || raw.startsWith("---")) {
      continue;
    } else if (raw.startsWith("+")) {
      lines.push({ kind: "add", text: `+${raw.slice(1)}` });
    } else if (raw.startsWith("-")) {
      lines.push({ kind: "del", text: `-${raw.slice(1)}` });
    } else if (raw.length > 0) {
      lines.push({ kind: "ctx", text: raw });
    }
  }
  return lines;
}

export interface DiffProps {
  /** Pre-parsed lines, when the caller already has them. */
  lines?: DiffLine[];
  /** A raw unified diff string, parsed with `parseUnifiedDiff`. */
  patch?: string;
}

export function Diff({ lines, patch }: DiffProps) {
  const rows = lines ?? (patch ? parseUnifiedDiff(patch) : []);
  return (
    <div className="diff" role="group" aria-label="Patch diff">
      {rows.map((line, i) => (
        <div key={i} className={line.kind}>
          {line.text}
        </div>
      ))}
    </div>
  );
}

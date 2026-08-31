import { api } from "../../lib/api";
import { useAsync } from "../../lib/useAsync";
import { Pill } from "../../components/Pill";
import { SkeletonLines } from "../../components/Skeleton";
import { EmptyState } from "../../components/EmptyState";
import { Button } from "../../components/Button";

/**
 * Point your own coding agent at Plumbline.
 *
 * The MCP work shipped with no presence in the product at all: eight tools,
 * a per-role manifest filter and an injection scanner over discovered tool
 * descriptions, discoverable only by reading the source. A capability nobody
 * can find is close to one that does not exist.
 *
 * The table lists every tool, including ones this role may NOT call, marked
 * as such. Filtering them away would be worse: "your key cannot approve a
 * patch" is information, whereas an approve tool silently missing from a
 * list reads as a bug.
 */

interface McpTool {
  name: string;
  summary: string;
  roles: string[];
  allowed: boolean;
}

interface McpInfo {
  endpoint: string;
  transport: string[];
  auth: string;
  your_role: string;
  tools: McpTool[];
}

export function McpPane() {
  const info = useAsync<McpInfo>(() => api.get<McpInfo>("/mcp/info"), []);

  if (info.status === "loading") {
    return (
      <div style={{ padding: "20px 0" }} aria-busy="true">
        <span className="visually-hidden" role="status">Loading MCP details…</span>
        <SkeletonLines count={5} />
      </div>
    );
  }
  if (info.status === "error" || !info.data) {
    return (
      <EmptyState
        variant="error"
        title="Couldn't load the MCP details"
        description={info.error}
        actions={<Button onClick={info.reload}>Retry</Button>}
      />
    );
  }

  const { endpoint, transport, auth, your_role: role, tools } = info.data;
  const allowed = tools.filter((t) => t.allowed).length;

  return (
    <div>
      <div className="setrow">
        <div>
          <h3>Endpoint</h3>
          <p>
            Point any MCP client at this and it can start runs and read findings
            without opening the dashboard.
          </p>
        </div>
        <div>
          <code className="mono" style={{ fontSize: 13.5, wordBreak: "break-all" }}>{endpoint}</code>
          <p style={{ marginTop: 8, fontSize: 13, color: "var(--muted)" }}>
            {transport.join(" · ")} · {auth}
          </p>
        </div>
      </div>

      <div className="setrow">
        <div>
          <h3>Your key's role</h3>
          <p>
            The manifest is filtered by role, so a client only sees the tools it
            may actually call.
          </p>
        </div>
        <div>
          <Pill kind={role === "owner" ? "pass" : "warn"}>{role}</Pill>
          <p style={{ marginTop: 8, fontSize: 13, color: "var(--muted)" }}>
            {allowed} of {tools.length} tools available to this role.
          </p>
        </div>
      </div>

      <div className="setrow">
        <div>
          <h3>Tools</h3>
          <p>
            Every call still passes through the Gateway, so an MCP tool gets the
            same scope check, ledger entry and redaction as any other.
          </p>
        </div>
        <div className="panel" style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>Tool</th>
                <th>What it does</th>
                <th style={{ width: 120 }}>Roles</th>
                <th style={{ width: 90 }}>Yours</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((t) => (
                <tr key={t.name} style={{ opacity: t.allowed ? 1 : 0.55 }}>
                  <td className="mono" style={{ fontSize: 12.5, whiteSpace: "nowrap" }}>{t.name}</td>
                  <td style={{ fontSize: 13.5, color: "var(--ink2)" }}>{t.summary}</td>
                  <td style={{ fontSize: 12.5, color: "var(--muted)" }}>{t.roles.join(", ")}</td>
                  <td>
                    {t.allowed
                      ? <Pill kind="pass">Allowed</Pill>
                      : <Pill kind="fail">Not yours</Pill>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

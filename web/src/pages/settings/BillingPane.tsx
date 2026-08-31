import { Button } from "../../components/Button";
import { EmptyState } from "../../components/EmptyState";
import { SkeletonBlock, SkeletonLines } from "../../components/Skeleton";
import { useToast } from "../../components/Toast";
import { api, ApiError } from "../../lib/api";
import { useAsync } from "../../lib/useAsync";
import type { AsyncState } from "../../lib/useAsync";
import { isDemoWrite, demoWriteMessage } from "../../lib/demo";
import type { BillingInfo, BillingInvoice, CurrentUser } from "../../lib/types";

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  return (
    <>
      <div className="plan-row" style={{ fontSize: 14, marginTop: 16 }}>
        {label} <span className="v n">{used} / {limit}</span>
      </div>
      <div className="meter">
        <i style={{ width: `${pct}%` }} />
      </div>
    </>
  );
}

function InvoiceHistory() {
  const invoices = useAsync<BillingInvoice[]>(() => api.get<BillingInvoice[]>("/billing/invoices"), []);
  if (invoices.status === "loading") {
    return (
      <div style={{ padding: "14px 16px" }} aria-busy="true">
        <span className="visually-hidden" role="status">Loading invoices…</span>
        <SkeletonLines count={3} widths={["70%", "55%", "60%"]} />
      </div>
    );
  }
  // Not every workspace's billing history is wired up yet; a failure here
  // reads as "no invoices on file" rather than an alarming, page-level error
  // -- this section is a footnote to the plan/usage/payment-method rows
  // above it, not something worth blocking the whole pane on.
  if (invoices.status === "error") {
    return <p style={{ fontSize: 13.5, color: "var(--muted)" }}>No invoice history is available yet.</p>;
  }
  const rows = invoices.data ?? [];
  if (rows.length === 0) return <p style={{ fontSize: 13.5, color: "var(--muted)" }}>No invoices yet.</p>;
  return (
    <table>
      <tbody>
        {rows.map((inv) => (
          <tr key={inv.id}>
            <td style={{ color: "var(--muted)" }}>{new Date(inv.at * 1000).toLocaleDateString()}</td>
            <td className="n">${(inv.amount / 100).toFixed(2)}</td>
            <td>{inv.status}</td>
            <td style={{ width: 80 }}>{inv.url && <a className="lnk" href={inv.url}>Download</a>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function BillingPane({ user }: { user: AsyncState<CurrentUser> }) {
  const { show } = useToast();
  const billing = useAsync<BillingInfo>(() => api.get<BillingInfo>("/billing"), []);
  const canManage = user.data?.role === "owner";

  async function onChangePlan() {
    try {
      const res = await api.post("/billing/plan", {});
      show(isDemoWrite(res) ? demoWriteMessage(res, "your plan would change") : "Plan change request sent.");
      billing.reload();
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't change your plan.");
    }
  }

  async function onUpdatePayment() {
    try {
      const res = await api.post<{ url: string; demo?: boolean; persisted?: boolean }>("/billing/portal");
      if (isDemoWrite(res)) {
        show(demoWriteMessage(res, "you'd be sent to the billing portal"));
        return;
      }
      if (res.url) window.location.href = res.url;
    } catch (err) {
      show(err instanceof ApiError && err.status === 404 ? "The billing portal isn't available yet." : err instanceof Error ? err.message : "Couldn't open the billing portal.");
    }
  }

  if (billing.status === "loading") {
    return (
      <div aria-busy="true">
        <span className="visually-hidden" role="status">Loading billing…</span>
        <div className="setrow">
          <div><SkeletonBlock width="30%" height={16} /></div>
          <div><SkeletonBlock width="40%" height={28} /></div>
        </div>
        <div className="setrow">
          <div><SkeletonBlock width="40%" height={16} /></div>
          <div style={{ maxWidth: 370 }}><SkeletonLines count={2} /></div>
        </div>
      </div>
    );
  }
  if (billing.status === "error") {
    return <EmptyState variant="error" title="Couldn't load billing" description={billing.error} actions={<Button onClick={billing.reload}>Retry</Button>} />;
  }
  const data = billing.data;
  if (!data) return null;

  return (
    <div>
      <div className="setrow">
        <div>
          <h4>Plan</h4>
          <p>
            {data.plan}, billed {data.interval}. Renews {new Date(data.renews_at * 1000).toLocaleDateString()}.
          </p>
        </div>
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 9 }}>
            <span style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-.02em" }}>${data.price}</span>
            <span style={{ fontSize: 14, color: "var(--muted)" }}>per {data.interval}</span>
          </div>
          <p style={{ marginTop: 7, fontSize: 13.5, color: "var(--muted)" }}>
            {data.run_limit} runs, {data.seat_limit} seats, unlimited behaviours.
          </p>
          <div style={{ marginTop: 13, display: "flex", gap: 8 }}>
            <Button
              onClick={onChangePlan}
              disabled={!canManage}
              title={canManage ? undefined : "Only an owner can change the plan."}
              aria-describedby={canManage ? undefined : "billing-manage-reason"}
            >
              Change plan
            </Button>
          </div>
        </div>
      </div>
      <div className="setrow">
        <div>
          <h4>Usage this month</h4>
        </div>
        <div style={{ maxWidth: 370 }}>
          <Meter label="Runs" used={data.runs_used} limit={data.run_limit} />
          <Meter label="Seats" used={data.seats_used} limit={data.seat_limit} />
        </div>
      </div>
      <div className="setrow">
        <div>
          <h4>Payment method</h4>
          <p>Charged automatically on renewal.</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <span className="mono" style={{ color: "var(--muted)" }}>{data.payment_method || "No payment method on file"}</span>
          <Button
            size="sm"
            onClick={onUpdatePayment}
            disabled={!canManage}
            title={canManage ? undefined : "Only an owner can update billing."}
            aria-describedby={canManage ? undefined : "billing-manage-reason"}
          >
            Update
          </Button>
          {!canManage && <span id="billing-manage-reason" className="visually-hidden">Only an owner can manage billing.</span>}
        </div>
      </div>
      <div className="setrow">
        <div>
          <h4>Invoice history</h4>
        </div>
        <div className="panel">
          <InvoiceHistory />
        </div>
      </div>
    </div>
  );
}

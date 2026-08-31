/**
 * A demo session gets its own real, writable sandbox workspace now --
 * behaviours, the gated patch, gate rules, pausing the fleet, a simulated
 * run, and more all genuinely persist there, and the normal success toast
 * is the honest thing to show for them.
 *
 * A SMALL remaining set of routes still refuses: anything that would
 * reach outside that sandbox -- a real GitHub repository, a real
 * environment, outbound webhook delivery, an API key or account setting
 * that would work past the demo, billing. Those still answer
 * `{"demo": true, "persisted": false, "reason": "..."}` (see
 * `app/deps.py`'s `demo_refusal`), and `reason` is a full sentence saying
 * WHY -- not just "nothing was saved", which would read as a lie the
 * moment most other actions in the same demo genuinely work.
 *
 * Every mutation handler in the app must still check this before
 * announcing success: `isDemoWrite` only ever fires on that small
 * remaining refused set now, not on every write.
 */
export interface DemoWriteResponse {
  demo?: boolean;
  persisted?: boolean;
  reason?: string;
}

/**
 * Deliberately a plain `boolean`, not a `res is DemoWriteResponse` type
 * predicate: `DemoWriteResponse`'s fields are all optional, so whenever a
 * caller's own response type already happens to declare an optional
 * `demo`/`persisted` field (several do, to document the contract inline),
 * that type is structurally a subtype of `DemoWriteResponse` -- and
 * TypeScript's negative narrowing for a matching predicate then collapses
 * the "false" branch to `never`. A plain boolean sidesteps that: neither
 * branch gets narrowed, which costs nothing here since every call site
 * only needs the yes/no, never `res.demo` itself inside the `true` branch.
 */
export function isDemoWrite(res: unknown): boolean {
  return Boolean(res && typeof res === "object" && (res as DemoWriteResponse).demo === true);
}

/**
 * The refusal toast for the small set of actions that still reach outside
 * the demo sandbox. Prefers the backend's own `reason` (a full sentence
 * saying WHY this one specific action isn't available) when the response
 * carries one -- every route `app/deps.py`'s `demo_refusal` covers does --
 * falling back to the old generic wording only for a response that
 * predates it.
 */
export function demoWriteMessage(res: unknown, whatWouldHaveHappened: string): string {
  const reason = res && typeof res === "object" ? (res as DemoWriteResponse).reason : undefined;
  return reason ?? `In the demo, ${whatWouldHaveHappened}. Nothing was saved.`;
}

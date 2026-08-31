/**
 * Every write-capable route answers a demo session with
 * `{"demo": true, "persisted": false}` (see `app/deps.py`) instead of
 * performing the write. A screen that shows the same success toast a real
 * session gets is lying at the exact moment the product's stakes are
 * highest -- an approved payments patch that was never actually approved.
 *
 * Every mutation handler in the app must check this before announcing
 * success, and say what *would* have happened instead.
 */
export interface DemoWriteResponse {
  demo?: boolean;
  persisted?: boolean;
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

/** Builds the "nothing was saved" toast, naming the specific action that didn't happen. */
export function demoWriteMessage(whatWouldHaveHappened: string): string {
  return `In the demo, ${whatWouldHaveHappened}. Nothing was saved.`;
}

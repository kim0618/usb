import { afterEach, beforeEach, expect } from "vitest";
import "@testing-library/jest-dom/vitest";

/** No test may reach the network, and that has to be a property of the runner.
 *
 *  jsdom implements `fetch`, so an unstubbed call in a component test is a real request. The
 *  terminal's API base defaults to `http://127.0.0.1:8100`, which on a machine being used to look
 *  at this screen is a *running paper terminal*: one order-ticket test invoked `cryptoApi.order`
 *  for real and swallowed the result with `.catch(() => {})`, and the suite opened a 0.008 BTC
 *  position. It was found by reading a ledger with five events in it and no explanation, which is
 *  the kind of evidence that only exists by luck.
 *
 *  So every test starts with a `fetch` that cannot leave the process. A test that needs responses
 *  stubs `fetch` itself - `vi.stubGlobal("fetch", ...)` replaces this one, and
 *  `unstubAllGlobals`/`restoreAllMocks` puts it back. A test that does not expect any request
 *  gets a rejected promise, which is exactly what it used to get from a machine with nothing
 *  listening, so nothing that passes today starts failing for a new reason.
 *
 *  `blockedRequests` is the receipt. `expectNoBackendWrites` reads it from the sending end, which
 *  is the only end available when the point is that no server was involved at all.
 */
export interface BlockedRequest { method: string; url: string }

export const blockedRequests: BlockedRequest[] = [];

const blocked = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const method = (
    init?.method
    ?? (typeof Request !== "undefined" && input instanceof Request ? input.method : "GET")
  ).toUpperCase();
  const url = typeof input === "string" ? input
    : input instanceof URL ? input.toString()
      : (input as Request).url;
  blockedRequests.push({ method, url });
  return Promise.reject(
    new TypeError(`TEST_FETCH_BLOCKED: ${method} ${url} - stub fetch in the test that needs it`));
};

/** jsdom has no `matchMedia`, and lightweight-charts reaches for it through `fancy-canvas` to
 *  watch the device pixel ratio. Chart tests have been installing this one at a time ever since
 *  (`crypto-c1-signal.test.tsx` carries the same shim with the same explanation); the chart now
 *  reliably reaches `createChart` in tests, so it belongs here once instead.
 *
 *  `matches: false` is chosen because it is what the application already assumes: both viewport
 *  hooks (`useIsWide`, `useIsNarrow`) bail out when `matchMedia` is absent and leave their state
 *  `false`, so a stub that never matches renders the same tree the suite has always rendered.
 *  Tests that drive a width replace it with `vi.stubGlobal("matchMedia", ...)`. */
const neverMatches = (query: string) => ({
  matches: false, media: query, onchange: null,
  addEventListener: () => undefined, removeEventListener: () => undefined,
  addListener: () => undefined, removeListener: () => undefined,
  dispatchEvent: () => false,
});

beforeEach(() => {
  blockedRequests.length = 0;
  if (!window.matchMedia) {
    window.matchMedia = neverMatches as unknown as typeof window.matchMedia;
  }
  globalThis.fetch = blocked as typeof globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = blocked as typeof globalThis.fetch;
});

/** Nothing that would change state on a backend was even attempted. */
export function expectNoBackendWrites() {
  expect(blockedRequests.filter(r => r.method !== "GET" && r.method !== "HEAD")).toEqual([]);
}

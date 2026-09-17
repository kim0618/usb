/** The one mock marker the screens use.
 *
 *  Small and repeated at most once per screen area: a demo state must be unmistakable
 *  without a warning bar taking over the page. Removing the demo state means flipping
 *  STRATEGY_B_MOCK / DASHBOARD_MOCK, which stops every caller from rendering this. */
export function MockBadge({ label = "MOCK" }: { label?: string }) {
  return <span data-mock-badge className="inline-flex whitespace-nowrap rounded-full border border-warning px-2 py-0.5 text-[10px] font-bold tracking-wide text-warning">{label}</span>;
}

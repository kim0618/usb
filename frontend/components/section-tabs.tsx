"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApi } from "@/hooks/use-api";
import { operatingTabs, strategiesApi } from "@/lib/strategies";

export type SectionTab = {
  href: string;
  label: string;
};

export const analysisTabs: SectionTab[] = [
  { href: "/candidates", label: "후보 종목" },
  { href: "/research", label: "GPT 분석" },
  { href: "/adoption", label: "채택 후보" },
];

/** Operating screens are derived from the strategy registry (see lib/strategies.ts), so a strategy
 *  appears here by being enabled in the backend rather than by being written into this file. */

/** The strategy group is a single screen (`/daily`): the sessions, then one strategy's record.
 *
 *  There is no tab bar for it any more. The analysis screens it used to point at - the metric
 *  comparison (`/strategy-compare`), Strategy H's valuation cohort (`/strategy-h`), the research
 *  history (`/strategy-history`) and A's empty exit-rule experiment (`/shadow`) - keep their routes
 *  and their data, but they answer research questions rather than operating ones and are not part
 *  of the screen you land on. `strategyTabs` stays exported and empty so `StrategyTabs` renders
 *  nothing rather than disappearing from the markup those screens still mount.
 */
export const strategyTabs: SectionTab[] = [];

export const systemTabs: SectionTab[] = [
  { href: "/runtime", label: "시스템 상태" },
  { href: "/settings", label: "설정" },
];

export function isRouteActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SectionTabs({ label, tabs }: { label: string; tabs: SectionTab[] }) {
  const pathname = usePathname();

  return (
    <nav aria-label={label} className="mb-5 flex gap-2 overflow-x-auto border-b border-line pb-3">
      {tabs.map(tab => {
        const active = isRouteActive(pathname, tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={`inline-flex h-9 shrink-0 items-center justify-center rounded-lg border px-3.5 text-sm font-semibold transition-colors focus-visible:ring-2 focus-visible:ring-primary ${active ? "border-primary bg-primary-soft text-primary" : "border-line bg-surface text-foreground-secondary hover:border-primary hover:bg-primary-soft hover:text-primary"}`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}

export function AnalysisTabs() {
  return <SectionTabs label="분석 화면" tabs={analysisTabs} />;
}

/** The operating tab bar. While the registry is loading the landmark stays (so the page does not
 *  jump) but no tab is invented; if the registry cannot be read, no tab is shown and the screen's
 *  own error state speaks. */
export function TradingTabs() {
  const registry = useApi(strategiesApi.list, 300_000);
  return <SectionTabs label="트레이딩 화면" tabs={registry.data ? operatingTabs(registry.data) : []} />;
}

export function StrategyTabs() {
  return <SectionTabs label="전략 화면" tabs={strategyTabs} />;
}

export function SystemTabs() {
  return <SectionTabs label="시스템 화면" tabs={systemTabs} />;
}

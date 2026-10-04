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

/** Result screens. The comparison is the group's landing screen and answers ALL / A / E / H on its
 *  own; Strategy H keeps a second screen because its per-issuer evidence does not fit a
 *  trade-shaped comparison; closed research (B, C, D) is reachable only through the research history.
 *
 *  The daily view leads because it answers the operating question - how much this session made or
 *  lost. The comparison tables, the frozen gate and the portfolio simulation are analysis and sit
 *  behind "상세 분석" rather than in front of it.
 *
 *  A's exit-rule experiment (`/shadow`) is deliberately not a tab. Its variants are also labelled
 *  A to E, which collides with Strategy A and Strategy E on this very bar, and the server's
 *  `shadow_trades` table holds 0 rows: the live paper runtime never writes one. The route, its
 *  components and its data stay where they are and the research history links to it. */
export const strategyTabs: SectionTab[] = [
  { href: "/daily", label: "일별 손익" },
  { href: "/strategy-compare", label: "상세 분석" },
  { href: "/strategy-h", label: "H 포워드 섀도" },
  { href: "/strategy-history", label: "연구 이력" },
];

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

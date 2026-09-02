"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export type SectionTab = {
  href: string;
  label: string;
};

export const analysisTabs: SectionTab[] = [
  { href: "/candidates", label: "후보 종목" },
  { href: "/research", label: "GPT 분석" },
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
    <nav aria-label={label} className="mb-5 flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map(tab => {
        const active = isRouteActive(pathname, tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={`shrink-0 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 ${active ? "border-cyan-400 text-cyan-300" : "border-transparent text-slate-400 hover:border-slate-600 hover:text-white"}`}
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

export function SystemTabs() {
  return <SectionTabs label="시스템 화면" tabs={systemTabs} />;
}

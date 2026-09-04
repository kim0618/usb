"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { etTime, kstTime } from "@/lib/format";
import { useApi } from "@/hooks/use-api";
import { RuntimeBanner, StatusBadge } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { formatBrokerMode, formatMarketSession, formatRuntimeMode } from "@/lib/display";

type NavigationItem = { href: string; icon: string; label: string; activePaths: string[] };
export const navigationItems: NavigationItem[] = [
  { href: "/trading", icon: "↗", label: "트레이딩", activePaths: ["/trading"] },
  { href: "/candidates", icon: "◎", label: "종목 분석", activePaths: ["/candidates", "/research", "/adoption"] },
  { href: "/shadow", icon: "≋", label: "전략 성과", activePaths: ["/shadow"] },
  { href: "/runtime", icon: "⚠", label: "시스템", activePaths: ["/runtime", "/settings"] },
];

export function isNavigationActive(pathname: string, item: NavigationItem) {
  return item.activePaths.some(activePath => activePath === "/" ? pathname === "/" : pathname === activePath || pathname.startsWith(`${activePath}/`));
}

export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname(); const [open,setOpen] = useState(false); const state = useApi(api.dashboard, 20000); const runtime = state.data?.runtime.mode || "NORMAL";
  return <div className="min-h-screen bg-background">
    <RuntimeBanner mode={runtime}/>
    <aside className={`fixed inset-y-0 left-0 z-40 w-60 border-r border-line bg-sidebar transition-transform lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
      <Link href="/trading" aria-label="USB Trading System" className="flex h-16 items-center gap-2.5 border-b border-line px-5">
        <Image src="/brand/usb-symbol.svg" alt="" aria-hidden="true" width={28} height={28} className="shrink-0"/>
        <span className="text-[17px] font-extrabold leading-none tracking-[0.12em] text-foreground">USB</span>
      </Link>
      <nav aria-label="주요 메뉴" className="space-y-1 p-3">{navigationItems.map(item => { const active=isNavigationActive(path,item); return <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined} onClick={() => setOpen(false)} className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${active ? "bg-primary-soft font-semibold text-primary" : "text-foreground-secondary hover:bg-surface-hover hover:text-foreground"}`}><span className={`w-5 text-center ${active ? "text-primary" : ""}`}>{item.icon}</span>{item.label}{item.label === "시스템" && (state.data?.runtime.unresolved_failure_count || 0) > 0 && <span className="ml-auto rounded-full bg-danger-soft px-2 text-xs text-danger">{state.data?.runtime.unresolved_failure_count}</span>}</Link>})}</nav>
      <div className="absolute bottom-0 w-full border-t border-line p-4 text-xs"><div className="mb-3 flex items-center justify-between"><span className="text-muted">Backend 연결</span><span className={state.error ? "text-danger" : "text-success"}>● {state.error ? "연결 안 됨" : "연결됨"}</span></div><div className="flex items-center justify-between"><span className="text-muted">브로커</span><StatusBadge value={state.data?.trading.broker_mode || "UNKNOWN"} label={formatBrokerMode(state.data?.trading.broker_mode || "UNKNOWN")}/></div></div>
    </aside>
    <div className="lg:pl-60"><header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-line bg-header px-4 md:px-7"><div className="flex items-center gap-3"><button className="btn-muted lg:hidden" onClick={() => setOpen(!open)} aria-label="메뉴">☰</button><div><p className="label">미국주식 트레이딩 시스템</p><p className="text-sm font-medium text-foreground" title={state.data ? etTime(state.data.system_time) : undefined}>{state.data ? kstTime(state.data.system_time) : "연결 중…"}</p></div></div><div className="flex items-center gap-3"><div className="hidden items-center gap-3 sm:flex"><span className="text-xs text-muted">시장</span><StatusBadge value={state.data?.market.session || "UNKNOWN"} label={formatMarketSession(state.data?.market.session || "UNKNOWN")}/><span className="text-xs text-muted">시스템</span><StatusBadge value={runtime} label={formatRuntimeMode(runtime)}/></div><ThemeToggle/></div></header><main className="p-4 md:p-7 xl:p-9">{children}</main></div>
  </div>;
}

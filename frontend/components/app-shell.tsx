"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { etTime, kstDate, kstTime, tradingDate } from "@/lib/format";
import { useApi } from "@/hooks/use-api";
import { RuntimeBanner, StatusBadge } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { formatBrokerMode, formatMarketSession, formatRuntimeMode } from "@/lib/display";
import { marketSessionSchedule } from "@/lib/market-session";

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
  const market = state.data?.market;
  const sessions = market ? marketSessionSchedule(market.trading_date, market.market_open, market.market_close) : [];
  const contextTradingDate = state.data?.scanner.trading_date;
  const paperStartedAt = state.data?.trading.paper_started_at;
  return <div className="min-h-screen bg-background">
    <RuntimeBanner mode={runtime}/>
    <aside className={`fixed inset-y-0 left-0 z-40 w-60 border-r border-line bg-sidebar transition-transform lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
      <Link href="/trading" aria-label="USB Trading System" className="flex h-16 items-center gap-2.5 border-b border-line px-5">
        <Image src="/brand/usb-symbol.svg" alt="" aria-hidden="true" width={28} height={28} className="shrink-0"/>
        <span className="text-[17px] font-extrabold leading-none tracking-[0.12em] text-foreground">USB</span>
      </Link>
      <nav aria-label="주요 메뉴" className="space-y-1 p-3">{navigationItems.map(item => { const active=isNavigationActive(path,item); return <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined} onClick={() => setOpen(false)} className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${active ? "bg-primary-soft font-semibold text-primary" : "text-foreground-secondary hover:bg-surface-hover hover:text-foreground"}`}><span className={`w-5 text-center ${active ? "text-primary" : ""}`}>{item.icon}</span>{item.label}{item.label === "시스템" && (state.data?.runtime.unresolved_failure_count || 0) > 0 && <span className="ml-auto rounded-full bg-danger-soft px-2 text-xs text-danger">{state.data?.runtime.unresolved_failure_count}</span>}</Link>})}</nav>
    </aside>
    <div className="lg:pl-60"><header className="sticky top-0 z-30 border-b border-line bg-header">
      <div className="flex min-h-16 flex-wrap items-center gap-2 px-4 py-2 xl:flex-nowrap xl:px-4 2xl:gap-3 2xl:px-7">
        <button className="btn-muted shrink-0 lg:hidden" onClick={() => setOpen(!open)} aria-label="메뉴">☰</button>
        <p className="shrink-0 whitespace-nowrap text-xs text-muted"><span>현재시각 :</span> <span className="font-medium tabular-nums text-foreground" title={state.data ? etTime(state.data.system_time) : undefined}>{state.data ? kstTime(state.data.system_time) : "연결 중…"}</span></p>
        <span className="hidden h-4 w-px shrink-0 bg-line 2xl:block" aria-hidden="true"/>
        <p className="shrink-0 whitespace-nowrap text-xs text-muted"><span>기준거래일 :</span> <span className="font-medium tabular-nums text-foreground-secondary">{tradingDate(contextTradingDate)}</span></p>
        {paperStartedAt && <><span className="hidden h-4 w-px shrink-0 bg-line 2xl:block" aria-hidden="true"/><p className="shrink-0 whitespace-nowrap text-xs text-muted"><span>가상매매 시작 :</span> <span className="font-medium tabular-nums text-foreground-secondary" title={kstTime(paperStartedAt)}>{kstDate(paperStartedAt)}</span></p></>}
        <div className="grid w-full shrink-0 grid-cols-3 gap-1.5 sm:flex sm:w-auto" aria-label="미국 시장 세션">{sessions.map(session => { const active = market?.is_trading_day && market.session === session.key; return <div key={session.key} className={`min-w-0 rounded-lg border px-1.5 py-0.5 text-center 2xl:px-2 ${active ? "border-primary bg-primary-soft text-primary" : "border-line text-muted"}`}><p className="truncate text-[10px] font-semibold">{session.label}{active && <span className="ml-1">· 현재</span>}</p><p className="truncate text-[9px] tabular-nums 2xl:text-[10px]">{session.kstRange} KST</p></div>; })}</div>
        <div className="ml-auto flex shrink-0 flex-wrap items-center justify-end gap-1.5"><span className="text-[11px] text-muted">시장</span><StatusBadge value={market?.session || "UNKNOWN"} label={market && !market.is_trading_day ? "휴장" : formatMarketSession(market?.session || "UNKNOWN")}/><span className="text-[11px] text-muted">시스템</span><StatusBadge value={runtime} label={formatRuntimeMode(runtime)}/>{state.data?.trading.broker_mode && <StatusBadge value={state.data.trading.broker_mode} label={formatBrokerMode(state.data.trading.broker_mode)}/>}<ThemeToggle/></div>
      </div>
    </header><main className="p-4 md:p-7 xl:p-9">{children}</main></div>
  </div>;
}

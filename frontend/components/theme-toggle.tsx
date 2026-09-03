"use client";

import { useEffect, useState } from "react";

export type Theme = "dark" | "light";
export const THEME_STORAGE_KEY = "usb-theme";

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_STORAGE_KEY, theme);
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    const initial = stored === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = initial;
    setTheme(initial);
  }, []);

  const nextTheme: Theme = theme === "dark" ? "light" : "dark";
  const label = nextTheme === "light" ? "라이트 모드로 전환" : "다크 모드로 전환";

  return <button type="button" className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface-alt text-foreground-secondary transition-colors hover:bg-surface-hover hover:text-primary" aria-label={label} title={label} onClick={() => { applyTheme(nextTheme); setTheme(nextTheme); }}>
    {theme === "dark" ? <MoonIcon/> : <SunIcon/>}
  </button>;
}

function MoonIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8"><path strokeLinecap="round" strokeLinejoin="round" d="M20.3 15.3A8.5 8.5 0 0 1 8.7 3.7 8.5 8.5 0 1 0 20.3 15.3Z"/></svg>;
}

function SunIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="3.5"/><path strokeLinecap="round" d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42"/></svg>;
}

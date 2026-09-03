import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { ToastProvider } from "@/components/toast";

export const metadata: Metadata = {
  title: "USB Operations",
  description: "Pre-Kiwoom trading operations dashboard",
  icons: { icon: "/brand/usb-symbol.svg" },
};
const themeScript = `(function(){try{var t=localStorage.getItem("usb-theme");document.documentElement.dataset.theme=t==="light"?"light":"dark"}catch(e){document.documentElement.dataset.theme="dark"}})()`;
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="ko" data-theme="dark" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{ __html: themeScript }}/></head><body><ToastProvider><AppShell>{children}</AppShell></ToastProvider></body></html>; }

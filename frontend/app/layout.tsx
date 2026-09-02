import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { ToastProvider } from "@/components/toast";

export const metadata: Metadata = { title: "USB Operations", description: "Pre-Kiwoom trading operations dashboard" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="ko"><body><ToastProvider><AppShell>{children}</AppShell></ToastProvider></body></html>; }

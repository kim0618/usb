"use client";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

const ToastContext = createContext<(message: string, error?: boolean) => void>(() => undefined);
export function ToastProvider({ children }: { children: ReactNode }) { const [toast, setToast] = useState<{ message: string; error: boolean } | null>(null); const show = useCallback((message: string, error = false) => { setToast({message,error}); window.setTimeout(() => setToast(null), 3500); }, []); return <ToastContext.Provider value={show}>{children}{toast && <div role="status" className={`fixed bottom-5 right-5 z-[70] max-w-sm rounded-lg border px-4 py-3 text-sm shadow-2xl ${toast.error ? "border-red-700 bg-red-950 text-red-200" : "border-emerald-700 bg-emerald-950 text-emerald-200"}`}>{toast.message}</div>}</ToastContext.Provider>; }
export const useToast = () => useContext(ToastContext);

"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";

export function useApi<T>(loader: () => Promise<T>, intervalMs?: number) {
  const loaderRef = useRef(loader); loaderRef.current = loader;
  const [data, setData] = useState<T | null>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => { try { setError(null); setData(await loaderRef.current()); } catch (e) { setError(e instanceof ApiError ? e.message : "Unexpected request error"); } finally { setLoading(false); } }, []);
  useEffect(() => { void refresh(); if (!intervalMs) return; const id = window.setInterval(refresh, intervalMs); return () => window.clearInterval(id); }, [refresh, intervalMs]);
  return { data, loading, error, refresh, setData };
}

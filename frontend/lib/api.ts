import type { Capabilities, Dashboard, Failure, ReplayReport, RuntimeStatus, Settings } from "@/types/api";

export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) { super(message); this.name = "ApiError"; }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers }, cache: "no-store" });
  } catch (error) {
    throw new ApiError(0, "NETWORK_ERROR", "Backend API에 연결할 수 없습니다.", error);
  }
  const data = await response.json().catch(() => null) as { error?: { code?: string; message?: string; details?: unknown } } | null;
  if (!response.ok) throw new ApiError(response.status, data?.error?.code || "HTTP_ERROR", data?.error?.message || `HTTP ${response.status}`, data?.error?.details);
  return data as T;
}

export const api = {
  dashboard: () => apiFetch<Dashboard>("/api/v1/dashboard"),
  replay: () => apiFetch<ReplayReport>("/api/v1/replay-smoke/latest"),
  runtime: () => apiFetch<RuntimeStatus>("/api/v1/runtime"),
  failures: () => apiFetch<Failure[]>("/api/v1/runtime/failures?limit=100"),
  resolveFailure: (id: number) => apiFetch(`/api/v1/runtime/failures/${id}/resolve`, { method: "POST" }),
  runtimeAction: (action: "safe-mode" | "halt", reason: string) => apiFetch(`/api/v1/runtime/${action}`, { method: "POST", body: JSON.stringify({ reason }) }),
  recover: () => apiFetch("/api/v1/runtime/recover", { method: "POST", body: JSON.stringify({ acknowledged: true }) }),
  reconcile: () => apiFetch<{ matched: boolean; checked_at: string; mismatches: Array<{ type: string; symbol: string; details: string }> }>("/api/v1/runtime/reconcile", { method: "POST" }),
  killSwitch: (reason: string) => apiFetch("/api/v1/runtime/kill-switch", { method: "POST", body: JSON.stringify({ confirm: true, reason }) }),
  settings: () => apiFetch<Settings>("/api/v1/settings"),
  capabilities: () => apiFetch<Capabilities>("/api/v1/capabilities"),
};

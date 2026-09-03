import type { Capabilities, Dashboard, Failure, Fill, Order, ReplayReport, ResearchAnalysis, ResearchDetail, RuntimeStatus, ScannerSnapshot, Settings, ShadowSummary, Trade, TradingOverview } from "@/types/api";

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
  scanner: () => apiFetch<ScannerSnapshot>("/api/v1/scanner/latest"),
  scannerRun: (id: number) => apiFetch<ScannerSnapshot>(`/api/v1/scanner/runs/${id}`),
  prompt: (symbol?: string) => apiFetch<{ prompt: string; prompt_version: string }>(`/api/v1/research/prompt${symbol ? `/${encodeURIComponent(symbol)}` : ""}`),
  research: () => apiFetch<ResearchAnalysis>("/api/v1/research/latest"),
  researchDetail: (id: number, symbol: string) => apiFetch<ResearchDetail>(`/api/v1/research/${id}/candidates/${encodeURIComponent(symbol)}`),
  importResearch: (raw_json: string) => apiFetch<{ analysis_id: number; candidate_count: number }>("/api/v1/research/import", { method: "POST", body: JSON.stringify({ raw_json }) }),
  decide: (id: number, symbol: string, decision: "APPROVE" | "REJECT", note?: string) => apiFetch<{ approved_count: number }>(`/api/v1/research/${id}/decisions/${encodeURIComponent(symbol)}`, { method: "PUT", body: JSON.stringify({ decision, note: note || null }) }),
  trading: () => apiFetch<TradingOverview>("/api/v1/trading"),
  orders: () => apiFetch<Order[]>("/api/v1/trading/orders"),
  fills: () => apiFetch<Fill[]>("/api/v1/trading/fills"),
  trades: () => apiFetch<Trade[]>("/api/v1/trading/trades"),
  shadow: (range?: { startDate: string; endDate: string }) => {
    const query = range ? `?start_date=${encodeURIComponent(range.startDate)}&end_date=${encodeURIComponent(range.endDate)}` : "";
    return apiFetch<ShadowSummary>(`/api/v1/shadow/summary${query}`);
  },
  shadowTrades: (query = "") => apiFetch<Trade[]>(`/api/v1/shadow/trades${query}`),
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

import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError } from "./api";

afterEach(() => vi.restoreAllMocks());
describe("apiFetch", () => {
  it("returns a successful resource", async () => { vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ mode: "NORMAL" }), { status: 200 }))); await expect(apiFetch("/test")).resolves.toEqual({ mode: "NORMAL" }); });
  it("parses the common API error", async () => { vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "STATE_CONFLICT", message: "blocked" } }), { status: 409 }))); await expect(apiFetch("/test")).rejects.toMatchObject({ status: 409, code: "STATE_CONFLICT", message: "blocked" } satisfies Partial<ApiError>); });
  it("uses a safe network error", async () => { vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("socket details"))); await expect(apiFetch("/test")).rejects.toMatchObject({ status: 0, code: "NETWORK_ERROR", message: "Backend API에 연결할 수 없습니다." } satisfies Partial<ApiError>); });
});

import { describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, parseError, reasonText } from "@/lib/api";

describe("parseError (03 §1 오류 형식)", () => {
  it("{error:{code,message,detail}} → code·reason·detail", () => {
    const e = parseError(422, {
      error: {
        code: "RISK_REJECTED",
        message: "종목 비중 상한 25% 도달",
        detail: { risk: { allowed: false, reason: "종목 비중 상한 25% 도달" } },
      },
    });
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(422);
    expect(e.code).toBe("RISK_REJECTED");
    expect(e.reason).toBe("종목 비중 상한 25% 도달");
    expect(e.detail).toEqual({ risk: { allowed: false, reason: "종목 비중 상한 25% 도달" } });
  });
  it("message가 비면 detail.risk.reason을 쓴다", () => {
    const e = parseError(409, {
      error: { code: "HALTED", message: "", detail: { risk: { reason: "halted: reconcile" } } },
    });
    expect(e.reason).toBe("halted: reconcile");
  });
  it("형식이 다르면 HTTP 상태로", () => {
    const e = parseError(500, "Internal Server Error");
    expect(e.code).toBe("HTTP_500");
    expect(e.reason).toBe("요청 실패 (HTTP 500)");
  });
  it("detail이 없으면 빈 객체", () => {
    expect(parseError(404, { error: { code: "NOT_FOUND", message: "없음" } }).detail).toEqual({});
  });
});

describe("reasonText", () => {
  it("리스크 거부·할트는 사유 앞에 구분을 붙인다", () => {
    expect(reasonText(parseError(422, { error: { code: "RISK_REJECTED", message: "월 한도" } }))).toBe(
      "리스크 게이트 거부 · 월 한도",
    );
    expect(reasonText(parseError(409, { error: { code: "HALTED", message: "halted: x" } }))).toBe(
      "신규 진입 중단 · halted: x",
    );
  });
  it("그 밖은 사유 그대로, Error가 아니면 기본 문구", () => {
    expect(reasonText(parseError(409, { error: { code: "NO_PRICE", message: "시세 없음" } }))).toBe(
      "시세 없음",
    );
    expect(reasonText(new Error("boom"))).toBe("boom");
    expect(reasonText("x")).toBe("알 수 없는 오류");
  });
});

describe("apiFetch", () => {
  it("같은 출처 프록시 /api/qp로 보내고 JSON을 돌려준다", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    const out = await apiFetch<{ ok: number }>("/portfolio", {}, { fetchImpl });
    expect(out).toEqual({ ok: 1 });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/qp/portfolio");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.stringify(init.headers ?? {})).not.toMatch(/authorization/i);
  });
  it("본문은 JSON으로", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({}), { status: 201 }));
    await apiFetch("/orders", { method: "POST", json: { side: "buy" } }, { fetchImpl });
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"side":"buy"}');
    expect((init.headers as Record<string, string>)["content-type"]).toBe("application/json");
  });
  it("오류 응답은 ApiError로 던진다", async () => {
    const body = { error: { code: "RISK_REJECTED", message: "거래당 손실 1%", detail: {} } };
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify(body), { status: 422 }));
    await expect(apiFetch("/orders", {}, { fetchImpl })).rejects.toMatchObject({
      status: 422,
      code: "RISK_REJECTED",
      reason: "거래당 손실 1%",
    });
  });
  it("401이면 onUnauthorized를 부른다", async () => {
    const onUnauthorized = vi.fn();
    const body = { error: { code: "UNAUTHORIZED", message: "토큰 없음" } };
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify(body), { status: 401 }));
    await expect(apiFetch("/x", {}, { fetchImpl, onUnauthorized })).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });
  it("경로는 /로 시작해야 한다", async () => {
    await expect(apiFetch("portfolio", {}, { fetchImpl: vi.fn() })).rejects.toThrow();
  });
});

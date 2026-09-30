import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyMessage, createLiveStore, initialLiveState, LiveSocket, PING_MS } from "@/lib/ws";

class FakeSocket {
  static instances: FakeSocket[] = [];
  static OPEN = 1;
  readyState = 0;
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(s: string) {
    this.sent.push(JSON.parse(s));
  }
  close(code = 1000) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  // 테스트 조작
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  push(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
  drop(code: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

function make(opts: { onUnauthorized?: () => void } = {}) {
  const store = createLiveStore();
  const getAuth = vi.fn(async () => ({ token: "tok-abc", url: "ws://api.test/api/v1/ws" }));
  const sock = new LiveSocket({
    getAuth,
    store,
    WebSocketImpl: FakeSocket as unknown as typeof WebSocket,
    onUnauthorized: opts.onUnauthorized,
  });
  return { store, getAuth, sock };
}

const last = () => FakeSocket.instances[FakeSocket.instances.length - 1]!;

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("LiveSocket 연결·구독 (03 §3)", () => {
  it("첫 메시지는 {auth, subscribe} — 채널은 정렬", async () => {
    const { sock, store } = make();
    sock.acquire(["judgments", "fills"]);
    await sock.start();
    const ws = last();
    expect(ws.url).toBe("ws://api.test/api/v1/ws");
    expect(store.getState().status).toBe("connecting");
    ws.open();
    expect(ws.sent[0]).toEqual({ auth: "tok-abc", subscribe: ["fills", "judgments"] });
    expect(store.getState().status).toBe("open");
    sock.stop();
  });

  it("열린 뒤 새 채널은 subscribe, 마지막 참조가 풀리면 unsubscribe", async () => {
    const { sock } = make();
    await sock.start();
    const ws = last();
    ws.open();
    const release = sock.acquire(["ticks:upbit:KRW-BTC"]);
    const release2 = sock.acquire(["ticks:upbit:KRW-BTC"]);
    expect(ws.sent.slice(1)).toEqual([{ subscribe: ["ticks:upbit:KRW-BTC"] }]);
    release();
    expect(ws.sent).toHaveLength(2);
    release2();
    expect(ws.sent[2]).toEqual({ unsubscribe: ["ticks:upbit:KRW-BTC"] });
    sock.stop();
  });

  it("ping 유지: 30초 무응답으로 끊기기 전에 {ping:1}", async () => {
    const { sock } = make();
    await sock.start();
    const ws = last();
    ws.open();
    expect(PING_MS).toBeLessThan(30_000);
    vi.advanceTimersByTime(PING_MS);
    expect(ws.sent.at(-1)).toEqual({ ping: 1 });
    vi.advanceTimersByTime(PING_MS);
    expect(ws.sent.filter((m) => JSON.stringify(m) === '{"ping":1}')).toHaveLength(2);
    sock.stop();
    const n = ws.sent.length;
    vi.advanceTimersByTime(PING_MS * 3);
    expect(ws.sent).toHaveLength(n);
  });

  it("비정상 종료는 지수 백오프로 재접속 (1s → 2s → 4s, 최대 30s), 메시지를 받으면 초기화", async () => {
    const { sock, store, getAuth } = make();
    await sock.start();
    last().open();
    last().drop(1006);
    expect(store.getState().status).toBe("closed");
    expect(sock.nextDelay()).toBe(1000);
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeSocket.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeSocket.instances).toHaveLength(2);
    last().drop(1006);
    await vi.advanceTimersByTimeAsync(2000);
    expect(FakeSocket.instances).toHaveLength(3);
    last().drop(1006);
    expect(sock.nextDelay()).toBe(4000);
    await vi.advanceTimersByTimeAsync(4000);
    expect(FakeSocket.instances).toHaveLength(4);
    last().open();
    last().push({ ch: "system", ts: "t", data: { subscribed: [] } });
    last().drop(1006);
    expect(sock.nextDelay()).toBe(1000);
    expect(getAuth).toHaveBeenCalledTimes(4);
    sock.stop();
  });

  it("백오프 상한 30s", () => {
    const { sock } = make();
    for (let i = 0; i < 10; i++) sock.bumpBackoff();
    expect(sock.nextDelay()).toBe(30_000);
  });

  it("4401이면 재접속하지 않고 onUnauthorized", async () => {
    const onUnauthorized = vi.fn();
    const { sock, store } = make({ onUnauthorized });
    await sock.start();
    last().open();
    last().drop(4401);
    expect(store.getState().status).toBe("unauthorized");
    expect(onUnauthorized).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("stop() 뒤에는 재접속하지 않는다", async () => {
    const { sock } = make();
    await sock.start();
    last().open();
    sock.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("받은 메시지를 스토어에 적용한다", async () => {
    const { sock, store } = make();
    await sock.start();
    last().open();
    last().push({ ch: "portfolio", ts: "2026-09-30T00:00:05+00:00", data: { market: "upbit", equity: 1_000 } });
    expect(store.getState().equity.upbit).toEqual({ equity: 1000, ts: "2026-09-30T00:00:05+00:00" });
    last().onmessage?.({ data: "not json" });
    expect(store.getState().status).toBe("open");
    sock.stop();
  });
});

describe("applyMessage", () => {
  const s0 = initialLiveState();

  it("portfolio → 시장별 평가액 + seq", () => {
    const s = applyMessage(s0, { ch: "portfolio", ts: "t1", data: { market: "krx", equity: 5 } });
    expect(s.equity).toEqual({ krx: { equity: 5, ts: "t1" } });
    expect(s.seq.portfolio).toBe(1);
  });

  it("judgments → 앞에 붙이고 20건까지, 같은 id는 교체", () => {
    let s = s0;
    for (let i = 1; i <= 25; i++) {
      s = { ...s, ...applyMessage(s, { ch: "judgments", ts: `t${i}`, data: { id: i, confidence: 0.5 } }) };
    }
    expect(s.judgments).toHaveLength(20);
    expect(s.judgments[0]).toMatchObject({ id: 25, ts: "t25" });
    s = { ...s, ...applyMessage(s, { ch: "judgments", ts: "tx", data: { id: 25, confidence: 0.9 } }) };
    expect(s.judgments).toHaveLength(20);
    expect(s.judgments[0]).toMatchObject({ id: 25, confidence: 0.9 });
    expect(s.seq.judgments).toBe(26);
  });

  it("strategy.status → 이름별", () => {
    const s = applyMessage(s0, {
      ch: "strategy.status",
      ts: "t",
      data: { name: "vol_breakout", enabled: true, position: {}, next_action: { at: "a", what: "b" } },
    });
    expect(s.strategyStatus.vol_breakout).toMatchObject({ enabled: true, next_action: { what: "b" } });
    expect(s.seq.strategy).toBe(1);
  });

  it("ticks:{m}:{s} → 마지막 가격", () => {
    const s = applyMessage(s0, {
      ch: "ticks:upbit:KRW-BTC",
      ts: "t",
      data: { price: 61_000_000, volume: 0.1, side: "buy" },
    });
    expect(s.ticks["upbit:KRW-BTC"]).toEqual({ price: 61_000_000, volume: 0.1, side: "buy", ts: "t" });
  });

  it("orderbook·fills", () => {
    const ob = { asks: [[2, 1]], bids: [[1, 1]] };
    let s = { ...s0, ...applyMessage(s0, { ch: "orderbook:upbit:KRW-ETH", ts: "t", data: ob }) };
    expect(s.orderbooks["upbit:KRW-ETH"]).toEqual(ob);
    s = { ...s, ...applyMessage(s, { ch: "fills", ts: "t", data: { order_id: "o1", symbol: "KRW-ETH" } }) };
    expect(s.fills[0]).toMatchObject({ order_id: "o1" });
    expect(s.seq.fills).toBe(1);
  });

  it("pong·system은 상태를 바꾸지 않고 error는 lastError", () => {
    expect(applyMessage(s0, { ch: "pong", ts: "t", data: { pong: 1 } })).toEqual({});
    expect(applyMessage(s0, { ch: "system", ts: "t", data: {} })).toEqual({});
    expect(applyMessage(s0, { ch: "error", ts: "t", data: { message: "x" } })).toEqual({ lastError: "x" });
  });

  it("모양이 틀린 메시지는 무시", () => {
    expect(applyMessage(s0, { ch: "portfolio", ts: "t", data: null as unknown as Record<string, unknown> })).toEqual({});
    expect(applyMessage(s0, { ch: "ticks:upbit:KRW-BTC", ts: "t", data: { price: "x" } })).toEqual({});
  });
});

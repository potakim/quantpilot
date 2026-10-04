// 실시간 스트림 (03 §3, ADR 0018 §1): WS `/api/v1/ws` → zustand 스토어.
// - 첫 메시지 `{"auth": JWT, "subscribe": [...]}`. 토큰은 connect() 지역 변수로만 두고 스토어·로그에 싣지 않는다.
// - 서버는 30초 무응답이면 끊으므로 PING_MS마다 `{"ping":1}`을 보낸다.
// - 비정상 종료는 지수 백오프(1s → 30s)로 재접속, 4401(인증 실패)은 재접속하지 않고 로그인으로 보낸다.
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";

export const PING_MS = 20_000;
export const BACKOFF_BASE_MS = 1_000;
export const BACKOFF_MAX_MS = 30_000;
export const CLOSE_UNAUTHORIZED = 4401;
const MAX_JUDGMENTS = 20;
const MAX_FILLS = 50;

export type LiveStatus = "idle" | "connecting" | "open" | "closed" | "unauthorized";

export interface LiveMessage {
  ch: string;
  ts: string | null;
  data: Record<string, unknown>;
}

export interface Tick {
  price: number;
  volume: number | null;
  side: string | null;
  ts: string | null;
}

export interface JudgmentPush {
  id?: number | null;
  symbol?: string | null;
  strategy?: string | null;
  confidence?: number;
  gate?: string;
  blocks?: string[];
  verdicts?: { model: string; approve: boolean }[];
  ts: string | null;
  [k: string]: unknown;
}

export interface StrategyStatusPush {
  name: string;
  enabled?: boolean;
  position?: unknown;
  next_action?: { at?: string; what?: string } | null;
  ts: string | null;
}

/** 백테스트 진행 (WS `backtest:{id}`, ADR 0033) */
export interface BacktestProgress {
  progress: number;
  stage: string;
  done: boolean;
  status: string | null;
  error: string | null;
}

export interface LiveState {
  status: LiveStatus;
  equity: Record<string, { equity: number; ts: string | null }>;
  judgments: JudgmentPush[];
  strategyStatus: Record<string, StrategyStatusPush>;
  ticks: Record<string, Tick>;
  orderbooks: Record<string, Record<string, unknown>>;
  fills: Record<string, unknown>[];
  backtests: Record<string, BacktestProgress>;
  seq: { portfolio: number; judgments: number; strategy: number; fills: number; risk: number; orders: number };
  lastError: string | null;
}

export function initialLiveState(): LiveState {
  return {
    status: "idle",
    equity: {},
    judgments: [],
    strategyStatus: {},
    ticks: {},
    orderbooks: {},
    fills: [],
    backtests: {},
    seq: { portfolio: 0, judgments: 0, strategy: 0, fills: 0, risk: 0, orders: 0 },
    lastError: null,
  };
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** 서버 메시지 1건 → 바뀐 상태 조각 (순수 함수). 모르는 채널·틀린 모양은 `{}`. */
export function applyMessage(s: LiveState, msg: LiveMessage): Partial<LiveState> {
  const { ch, ts } = msg;
  const data = msg.data;
  if (ch === "error") {
    return { lastError: isRecord(data) && typeof data.message === "string" ? data.message : "오류" };
  }
  if (!isRecord(data)) return {};
  if (ch === "portfolio") {
    const equity = num(data.equity);
    if (typeof data.market !== "string" || equity === null) return {};
    return {
      equity: { ...s.equity, [data.market]: { equity, ts } },
      seq: { ...s.seq, portfolio: s.seq.portfolio + 1 },
    };
  }
  if (ch === "judgments") {
    const item: JudgmentPush = { ...data, ts };
    const rest = item.id == null ? s.judgments : s.judgments.filter((j) => j.id !== item.id);
    return {
      judgments: [item, ...rest].slice(0, MAX_JUDGMENTS),
      seq: { ...s.seq, judgments: s.seq.judgments + 1 },
    };
  }
  if (ch === "strategy.status") {
    if (typeof data.name !== "string") return {};
    const item = { ...data, name: data.name, ts } as StrategyStatusPush;
    return {
      strategyStatus: { ...s.strategyStatus, [data.name]: item },
      seq: { ...s.seq, strategy: s.seq.strategy + 1 },
    };
  }
  if (ch === "fills") {
    return { fills: [{ ...data, ts: data.ts ?? ts }, ...s.fills].slice(0, MAX_FILLS), seq: { ...s.seq, fills: s.seq.fills + 1 } };
  }
  if (ch === "risk") return { seq: { ...s.seq, risk: s.seq.risk + 1 } };
  if (ch === "orders") return { seq: { ...s.seq, orders: s.seq.orders + 1 } };
  if (ch.startsWith("ticks:")) {
    const price = num(data.price);
    if (price === null) return {};
    const key = ch.slice("ticks:".length);
    const tick: Tick = {
      price,
      volume: num(data.volume),
      side: typeof data.side === "string" ? data.side : null,
      ts,
    };
    return { ticks: { ...s.ticks, [key]: tick } };
  }
  if (ch.startsWith("backtest:")) {
    const progress = num(data.progress);
    if (progress === null) return {};
    const item: BacktestProgress = {
      progress,
      stage: typeof data.stage === "string" ? data.stage : "",
      done: data.done === true,
      status: typeof data.status === "string" ? data.status : null,
      error: typeof data.error === "string" ? data.error : null,
    };
    return { backtests: { ...s.backtests, [ch.slice("backtest:".length)]: item } };
  }
  if (ch.startsWith("orderbook:")) {
    return { orderbooks: { ...s.orderbooks, [ch.slice("orderbook:".length)]: data } };
  }
  return {};
}

export type LiveStore = StoreApi<LiveState>;

export function createLiveStore(): LiveStore {
  return createStore<LiveState>()(() => initialLiveState());
}

export interface WsAuth {
  token: string;
  url: string;
}

export interface LiveSocketOptions {
  getAuth: () => Promise<WsAuth>;
  store: LiveStore;
  WebSocketImpl?: typeof WebSocket;
  onUnauthorized?: () => void;
}

const OPEN = 1;

/** WS 연결 하나: 채널 참조 카운트, ping, 재접속. */
export class LiveSocket {
  private refs = new Map<string, number>();
  private ws: WebSocket | null = null;
  private ping: ReturnType<typeof setInterval> | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private pendingDelay: number | null = null;
  private attempt = 0;
  private stopped = true;

  constructor(private readonly opts: LiveSocketOptions) {}

  private get Impl(): typeof WebSocket {
    return this.opts.WebSocketImpl ?? WebSocket;
  }

  private setStatus(status: LiveStatus): void {
    this.opts.store.setState({ status });
  }

  private send(msg: unknown): void {
    if (this.ws && this.ws.readyState === OPEN) this.ws.send(JSON.stringify(msg));
  }

  /** 채널을 쓴다고 알린다. 돌려받은 함수를 부르면 참조를 푼다. */
  acquire(channels: string[]): () => void {
    const added: string[] = [];
    for (const ch of channels) {
      const n = this.refs.get(ch) ?? 0;
      this.refs.set(ch, n + 1);
      if (n === 0) added.push(ch);
    }
    if (added.length) this.send({ subscribe: added.sort() });
    let released = false;
    return () => {
      if (released) return;
      released = true;
      const removed: string[] = [];
      for (const ch of channels) {
        const n = (this.refs.get(ch) ?? 1) - 1;
        if (n <= 0) {
          this.refs.delete(ch);
          removed.push(ch);
        } else this.refs.set(ch, n);
      }
      if (removed.length) this.send({ unsubscribe: removed.sort() });
    };
  }

  /** 다음 재접속까지 기다릴 시간 (예약돼 있으면 그 값). */
  nextDelay(): number {
    if (this.pendingDelay !== null) return this.pendingDelay;
    return Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** this.attempt);
  }

  bumpBackoff(): void {
    this.attempt += 1;
  }

  async start(): Promise<void> {
    this.stopped = false;
    await this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.retry) clearTimeout(this.retry);
    this.retry = null;
    this.pendingDelay = null;
    this.stopPing();
    const ws = this.ws;
    this.ws = null;
    ws?.close(1000);
    this.setStatus("idle");
  }

  private stopPing(): void {
    if (this.ping) clearInterval(this.ping);
    this.ping = null;
  }

  private schedule(): void {
    if (this.stopped) return;
    const delay = this.nextDelay();
    this.attempt += 1;
    this.pendingDelay = delay;
    this.retry = setTimeout(() => {
      this.retry = null;
      this.pendingDelay = null;
      void this.connect();
    }, delay);
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    this.setStatus("connecting");
    let auth: WsAuth;
    try {
      auth = await this.opts.getAuth();
    } catch {
      this.setStatus("closed");
      this.schedule();
      return;
    }
    if (this.stopped) return;
    const ws = new this.Impl(auth.url);
    this.ws = ws;
    ws.onopen = () => {
      this.setStatus("open");
      ws.send(JSON.stringify({ auth: auth.token, subscribe: [...this.refs.keys()].sort() }));
      this.stopPing();
      this.ping = setInterval(() => this.send({ ping: 1 }), PING_MS);
    };
    ws.onmessage = (ev: MessageEvent) => {
      let msg: unknown;
      try {
        msg = JSON.parse(String(ev.data));
      } catch {
        return;
      }
      if (!isRecord(msg) || typeof msg.ch !== "string") return;
      this.attempt = 0;
      const m: LiveMessage = {
        ch: msg.ch,
        ts: typeof msg.ts === "string" ? msg.ts : null,
        data: msg.data as Record<string, unknown>,
      };
      this.opts.store.setState((s) => applyMessage(s, m));
    };
    ws.onclose = (ev: CloseEvent) => {
      if (this.ws === ws) this.ws = null;
      this.stopPing();
      if (this.stopped) return;
      if (ev.code === CLOSE_UNAUTHORIZED) {
        this.stopped = true;
        this.setStatus("unauthorized");
        this.opts.onUnauthorized?.();
        return;
      }
      this.setStatus("closed");
      this.schedule();
    };
  }
}

/** 앱 전체가 쓰는 스토어·연결 (providers.tsx가 start/stop). */
export const liveStore = createLiveStore();

export function useLive<T>(selector: (s: LiveState) => T): T {
  return useStore(liveStore, selector);
}

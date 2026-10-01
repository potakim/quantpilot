// API v1 응답 모양 (quantpilot/api/routes/*.py 실제 구현 기준, 03 문서).

export type MarketId = "upbit" | "krx" | "us";

export interface Health {
  ok: boolean;
  version: string;
  paper: boolean;
  env: string;
  engine_alive: boolean;
  ws_connected: { upbit: boolean; kis: boolean };
  halted: Record<string, string | null>;
  error?: string;
}

export interface PositionView {
  market: string | null;
  symbol: string;
  strategy: string | null;
  qty: number;
  avg_price: number;
  stop: number | null;
  opened_at: string | null;
  price: number | null;
  unrealized: number | null;
}

export interface Portfolio {
  total_equity_krw: number;
  total_includes_us: boolean;
  fx: { usdkrw: number | null; source: string | null };
  by_market: Record<string, { cash: number; equity: number; positions: PositionView[] }>;
  month_pnl: Record<string, number | null>;
  month_limit: number;
  halted: Record<string, string | null>;
}

export interface StrategyView {
  name: string;
  market: string;
  timeframe: string;
  horizon: string;
  symbols: string[];
  params: Record<string, unknown>;
  enabled: boolean;
  allocation: number;
  paper: boolean;
  status: { position: Record<string, number>; month_pnl: number | null; mdd_30d: number | null };
  gate: { g1?: { pass: boolean; reason?: string | null } };
}

export interface Page<T> {
  items: T[];
  next_cursor: string | number | null;
}

export interface Verdict {
  id?: number;
  model: string;
  approve: boolean;
  reason?: string;
  latency_ms?: number | null;
  cost_usd?: number | null;
}

export interface JudgmentRow {
  id: number;
  signal_id: number;
  ts: string;
  provider: string;
  answers: Record<string, unknown>;
  confidence: number;
  gate: string;
  blocks: string[];
  latency_ms: number | null;
  cost_usd: number | null;
  realized_ret_24h: number | null;
  direction_hit: boolean | null;
  market: string;
  symbol: string;
  strategy: string;
  kind: string;
  outcome: string;
  outcome_reason: string | null;
  verdicts: Verdict[];
}

export interface JudgmentDetail extends JudgmentRow {
  state: Record<string, unknown> & { text?: string };
  signal: Record<string, unknown>;
  orders: OrderRow[];
  fills: FillRow[];
}

export interface Calibration {
  brier: number | null;
  ece: number | null;
  n: number;
  buckets: { range: string; n: number; hit_rate: number | null; avg_conf: number | null }[];
  monotonic?: boolean;
}

export interface AbReport {
  on: { ret: number | null; mdd: number | null; n_trades?: number };
  off: { ret: number | null; mdd: number | null; n_trades?: number };
  g2_pass: boolean;
  g2?: string;
  g2_reason?: string;
}

export interface Gates {
  g1: { pass: boolean };
  g2: { pass: boolean; days?: string; reason?: string };
  g3: { pass: boolean };
  g4: { pass: boolean };
}

export interface FillRow {
  id: number;
  order_id: string;
  ts: string;
  market: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  fee: number;
  tax: number;
  strategy: string;
  reason: string | null;
  paper: boolean;
  shadow?: boolean;
}

export interface OrderRow {
  id: string;
  ts: string;
  market: string;
  symbol: string;
  side: string;
  type: string;
  qty: number;
  limit_price: number | null;
  status: string;
  strategy: string;
  shadow?: boolean;
}

export interface Orderbook {
  asks?: [number, number][];
  bids?: [number, number][];
}

export interface Quote {
  market: string;
  symbol: string;
  price: number;
  orderbook: Orderbook | null;
  strategy: Record<string, unknown> | null;
}

export interface RiskRulesView {
  max_loss_per_trade: number;
  monthly_loss_limit: number;
  max_symbol_weight: number;
  max_intraday_weight: number;
  max_orders_per_symbol_per_sec: number;
  max_consecutive_api_errors: number;
  locked: true;
}

export interface SettingsView {
  settings: Record<string, unknown>;
  risk_rules: RiskRulesView;
  paper: boolean;
}

export interface RiskEventRow {
  id: number;
  ts: string;
  kind: string;
  detail: Record<string, unknown>;
  resolved_at: string | null;
}

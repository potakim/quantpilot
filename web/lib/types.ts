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
  /** 실시간 엔진이 도는 시장 (ADR 0031). 나머지는 "2단계 예정" */
  live_markets?: string[];
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

export interface TodayPnl {
  amount: number;
  pct: number;
}

export interface Portfolio {
  total_equity_krw: number;
  today_pnl_krw: number | null;
  total_includes_us: boolean;
  fx: { usdkrw: number | null; source: string | null };
  /** active=false: 실시간 엔진도 저장된 계좌도 없는 시장 — 합계에서 빠진다 (ADR 0031) */
  by_market: Record<
    string,
    { active?: boolean; cash: number; equity: number; positions: PositionView[]; today_pnl: TodayPnl | null }
  >;
  month_pnl: Record<string, number | null>;
  month_limit: number;
  halted: Record<string, string | null>;
}

/** 전략 파라미터 정의 (strategies/base.py ParamSpec). 숫자는 min·max·step, 참·거짓은 choices */
export interface ParamSpecView {
  name: string;
  default: unknown;
  min: number | null;
  max: number | null;
  step: number | null;
  choices: unknown[];
  description: string;
}

/** G1 검증 근거 (ops/g1.py, `qp gate g1 --write`). 지표 키는 전략마다 다르다 (cagr·mdd·mdd_monthly…) */
export interface G1Evidence {
  /** 파라미터 시도 횟수 (AttemptTracker, 7회 넘으면 과최적화 경고) */
  distinct_attempts?: number;
  within_20pct?: boolean | null;
  basis?: string;
  period?: string;
  metrics?: Record<string, number>;
  reference?: Record<string, number>;
  source?: string;
  checked_at?: string;
}

export interface StrategyView {
  name: string;
  market: string;
  timeframe: string;
  horizon: string;
  symbols: string[];
  params: Record<string, unknown>;
  schema?: ParamSpecView[];
  /** 그 시장의 비용 모델 — 백테스터·PaperBroker와 같은 값 (ADR 0033) */
  cost_model?: CostModelView;
  enabled: boolean;
  allocation: number;
  paper: boolean;
  status: { position: Record<string, number>; month_pnl: number | null; mdd_30d: number | null };
  gate: { g1?: { pass: boolean; reason?: string | null; evidence?: G1Evidence } };
}

export interface Page<T> {
  items: T[];
  next_cursor: string | number | null;
}

/** GET /judgments — 기간 안의 규칙 미충족 건수를 함께 준다. 엔진 기록 전이면 null (ADR 0027) */
export interface JudgmentPage extends Page<JudgmentRow> {
  rule_unmet: number | null;
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
  fee_rate: number;
  tax_rate_sell: number;
}

export interface EquityPoint {
  ts: string;
  v: number;
}

/** GET /portfolio/equity (ADR 0020 §2). */
export interface EquityCurve {
  market: string;
  days: number;
  points: EquityPoint[];
  benchmark: EquityPoint[] | null;
  source: string;
}

/** GET /schedule 항목 (ADR 0020 §4). */
export interface ScheduleItem {
  name: string;
  /** 잡 이름의 한국어 문구 (ADR 0031) */
  title?: string;
  market: string | null;
  next_action: { at: string; what: string };
  done: boolean;
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

/** backtest/costs.py CostModel (편도 비율) */
export interface CostModelView {
  fee_rate: number;
  slippage_rate: number;
  sell_tax_rate: number;
  tick_size?: number;
}

export interface EquityPt {
  ts: string;
  v: number;
}

/** GET /backtests/{id}의 구간별 성과 한 행 (ADR 0033) */
export interface BacktestPeriod {
  key: string;
  label: string;
  start: string;
  end: string;
  cagr: number;
  bench_cagr: number | null;
  mdd: number;
  bench_mdd: number | null;
  excess: number | null;
}

/** GET /backtests/{id}. status가 done이 아니면 곡선·벤치마크가 없다 */
export interface BacktestRun {
  id: number;
  strategy: string;
  source: string;
  params: Record<string, unknown>;
  symbols: string[];
  created_at: string | null;
  period_start: string | null;
  period_end: string | null;
  status: string;
  progress: number | null;
  error: string | null;
  metrics: Partial<BacktestMetrics>;
  cost_model: CostModelView | Record<string, never>;
  holdout_cutoff: string | null;
  unlocked_holdout: boolean;
  attempt_no: number;
  attempts?: { distinct_attempts: number; warn_after: number; overfit_warning: boolean };
  warnings?: string[];
  equity?: EquityPt[];
  drawdown?: EquityPt[];
  data_end?: string | null;
  benchmark?: { symbol: string; label: string; points: EquityPt[]; drawdown: EquityPt[] } | null;
  periods?: BacktestPeriod[];
}

/** backtest/metrics.py Metrics.to_dict */
export interface BacktestMetrics {
  start: string;
  end: string;
  years: number;
  total_return: number;
  cagr: number;
  max_drawdown: number;
  sharpe: number;
  volatility: number;
  n_trades: number;
  win_rate: number;
  avg_trade_return: number;
  turnover_per_year: number;
  total_costs: number;
}

/** GET /backtests 목록 한 행 (DB 행 그대로) */
export interface BacktestListItem {
  id: number;
  ts: string;
  strategy: string;
  source: string;
  params: Record<string, unknown>;
  period_start: string | null;
  period_end: string | null;
  metrics: Partial<BacktestMetrics>;
  attempt_no: number;
  unlocked_holdout: boolean;
}

/** 지금 유효한 AI 판단 설정 (ADR 0032). active = 엔진이 실제로 쓰는 모델 (재시작 때 바뀐다) */
export interface JudgeView {
  provider: string;
  llm_models: string[];
  hold_below: number;
  full_above: number;
  keys: Record<string, boolean>;
  active: { provider: string; llm_models: string[]; ts?: string } | null;
  /** Gemini 뉴스 요약 (news.enabled, ADR 0035). false면 다음 정시 수집부터 제목 요약 */
  news_summary: boolean;
}

export interface SettingsView {
  settings: Record<string, unknown>;
  risk_rules: RiskRulesView;
  paper: boolean;
  judge?: JudgeView;
}

/** GET /costs/ai — 이번 달 AI 호출 수·비용 (판단 모델 judge:*, 리뷰어 llm:*) */
export interface CostsAi {
  month: string;
  by_provider: Record<string, { calls: number; usd: number }>;
  total_usd: number;
}

export interface RiskEventRow {
  id: number;
  ts: string;
  kind: string;
  detail: Record<string, unknown>;
  resolved_at: string | null;
}

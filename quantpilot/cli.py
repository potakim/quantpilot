"""명령줄: qp backtest / qp fetch / qp judge / qp serve"""
from __future__ import annotations

import argparse
import json
import sys

from quantpilot.backtest import AttemptTracker, Backtester, preset
from quantpilot.config import settings
from quantpilot.core.models import Market
from quantpilot.data import CandleCache, load, synthetic
from quantpilot.judgment import AlwaysApprove, State, StubJudge, StubLLM, decide
from quantpilot.strategies import REGISTRY, create


def _parse_params(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def cmd_backtest(a: argparse.Namespace) -> int:
    strat = create(a.strategy, **_parse_params(a.param))
    symbols = tuple(a.symbols or strat.symbols)
    strat.symbols = symbols
    tf = "5m" if strat.timeframe == "5m" else "1d"
    if a.source == "synthetic":
        data = ({s: synthetic.intraday_5m(abs(hash(s)) % 997, days=60) for s in symbols} if tf == "5m"
                else synthetic.universe(symbols, periods=2000, start="2017-01-01"))
    else:
        cache = CandleCache(settings.cache_dir)
        kw = {"start": a.start} if a.start and a.source != "upbit" else {}
        data = {s: load(a.source, s, tf, cache=cache, refresh=a.refresh, **kw) for s in symbols}
    cash = a.cash or (settings.initial_cash_usd if strat.market == Market.US else settings.initial_cash_krw)
    hold = a.holdout if a.holdout is not None else (0 if tf == "5m" else settings.holdout_months)
    bt = Backtester(preset(strat.market), cash, holdout_months=hold, unlock_holdout=a.unlock_holdout)
    res = bt.run(strat, data, attempts=AttemptTracker(settings.attempts_file))
    s = res.summary()
    print(f"\n{s['strategy']}  {s['start']} ~ {s['end']}  ({s['years']}y)  비용모델 {preset(strat.market)}")
    print(f"  총수익 {s['total_return']:+.2%}  CAGR {s['cagr']:+.2%}  MDD {s['max_drawdown']:.2%}  "
          f"Sharpe {s['sharpe']:.2f}  거래 {s['n_trades']}  승률 {s['win_rate']:.1%}  "
          f"연회전율 {s['turnover_per_year']:.1f}x  비용합계 {s['total_costs']:,.0f}")
    if s.get("holdout_cutoff"):
        print(f"  홀드아웃: {s['holdout_cutoff']} 이후 {hold}개월 잠김 (--unlock-holdout 으로 1회 해제)")
    if s.get("attempts"):
        print(f"  파라미터 시도: {s['attempts']['distinct_attempts']}/{s['attempts']['warn_after']}")
    for w in s["warnings"]:
        print("  !", w)
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_fetch(a: argparse.Namespace) -> int:
    cache = CandleCache(settings.cache_dir)
    for s in a.symbols:
        kw = {"count": a.count} if a.source == "upbit" else {"start": a.start}
        df = load(a.source, s, a.tf, cache=cache, refresh=True, **kw)
        print(f"{a.source} {s} {a.tf}: {len(df)} bars  {df.index[0].date()} ~ {df.index[-1].date()}  "
              f"→ {cache.path(a.source, s, a.tf)}")
    return 0


def cmd_judge(a: argparse.Namespace) -> int:
    st = State(market=a.market, symbol=a.symbol, strategy=a.strategy, signal=a.signal,
               features={"ma_score": a.ma_score, "vol_pctl_20d": a.vol_pctl}, news_summary=a.news)
    jr = StubJudge().judge(st)
    llms = [AlwaysApprove()] if a.no_gating else [StubLLM("claude-stub"), StubLLM("gemini-stub")]
    verdicts = [m.review(st, jr) for m in llms]
    d = decide(jr, verdicts, hold_below=settings.gate_hold_below, full_above=settings.gate_full_above)
    print(st.render())
    print("---")
    print(json.dumps(jr.answers, ensure_ascii=False, indent=2))
    print(f"confidence {jr.confidence}  gate {d.gate.value}  size x{d.size_multiplier}  blocks {d.blocks}")
    for v in verdicts:
        print(f"  {v.model}: {'승인' if v.approve else '보류'} — {v.reason}")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("quantpilot.api.app:app", host=a.host, port=a.port, reload=a.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="qp", description="QuantPilot CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="백테스트 실행")
    b.add_argument("strategy", choices=sorted(REGISTRY))
    b.add_argument("--source", default="synthetic", choices=["synthetic", "upbit", "yfinance", "fdr"])
    b.add_argument("--symbols", nargs="*")
    b.add_argument("--param", "-p", action="append", help="k=0.5 형식 (JSON 값)")
    b.add_argument("--start")
    b.add_argument("--cash", type=float)
    b.add_argument("--holdout", type=int)
    b.add_argument("--unlock-holdout", action="store_true")
    b.add_argument("--refresh", action="store_true")
    b.add_argument("--json", action="store_true")
    b.set_defaults(fn=cmd_backtest)

    f = sub.add_parser("fetch", help="캔들 데이터 내려받아 캐시")
    f.add_argument("source", choices=["upbit", "yfinance", "fdr"])
    f.add_argument("symbols", nargs="+")
    f.add_argument("--tf", default="1d")
    f.add_argument("--count", type=int, default=2000)
    f.add_argument("--start", default="2015-01-01")
    f.set_defaults(fn=cmd_fetch)

    j = sub.add_parser("judge", help="AI 판단 파이프라인 미리보기 (스텁)")
    j.add_argument("--market", default="upbit")
    j.add_argument("--symbol", default="KRW-ETH")
    j.add_argument("--strategy", default="vol_breakout")
    j.add_argument("--signal", default="breakout k=0.5")
    j.add_argument("--ma-score", type=float, default=0.75)
    j.add_argument("--vol-pctl", type=float, default=78)
    j.add_argument("--news", default="")
    j.add_argument("--no-gating", action="store_true")
    j.set_defaults(fn=cmd_judge)

    s = sub.add_parser("serve", help="FastAPI 서버")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

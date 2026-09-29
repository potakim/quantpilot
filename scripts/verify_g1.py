"""관문 G1 확인: 실데이터 백테스트가 공개 수치 ±20% 이내인지.

사용: python scripts/verify_g1.py   (먼저 qp fetch 로 데이터를 받아둘 것)
"""
from quantpilot.backtest import Backtester, preset
from quantpilot.config import settings
from quantpilot.data import CandleCache, load
from quantpilot.strategies import create

# 공개 수치 (기획서 참고 자료). 기간·유니버스가 완전히 같지 않으므로 '같은 자릿수'인지 보는 용도.
PUBLIC = {
    "gem": {"source": "yfinance", "cagr": 0.1518, "mdd": -0.217, "note": "Petit 2026, 1971~2026 (ETF 데이터는 2008~)"},
    "vol_breakout": {"source": "upbit", "cagr": 0.174, "mdd": -0.065,
                     "note": "강환국 BTC 2013.10~2018.3, 0.5% 타겟+5일선 (업비트 데이터는 2017.10~)",
                     "params": {"target_vol": 0.005, "ma_windows": (5,)}},
}

cache = CandleCache(settings.cache_dir)
for name, ref in PUBLIC.items():
    strat = create(name, **ref.get("params", {}))
    try:
        data = {s: load(ref["source"], s, "1d", cache=cache) for s in strat.symbols}
    except Exception as e:
        print(f"{name}: 데이터 없음 ({e}) → qp fetch 먼저")
        continue
    res = Backtester(preset(strat.market), holdout_months=12).run(strat, data)
    m = res.metrics
    ok = abs(m.cagr - ref["cagr"]) <= 0.2 * abs(ref["cagr"]) and abs(m.max_drawdown - ref["mdd"]) <= 0.2 * abs(ref["mdd"])
    print(f"{name}: CAGR {m.cagr:+.2%} (공개 {ref['cagr']:+.2%})  MDD {m.max_drawdown:.2%} (공개 {ref['mdd']:.2%})  "
          f"{m.start}~{m.end}  → {'G1 PASS' if ok else 'G1 미달 (기간·유니버스 차이 검토)'}\n   {ref['note']}")

"use client";

// 캔들 차트 (Trade.dc.html): TradingView Lightweight Charts. 캔들 빨강/파랑, 20개 이동평균 --warn,
// 변동성 돌파 목표가 점선 --ai + 라벨, 진입 마커(초록 삼각), 현재가 라벨 --up. 시각 표시는 KST.
import { useEffect, useRef } from "react";
import type { IChartApi, IPriceLine, ISeriesApi, ISeriesMarkersPluginApi, Time, UTCTimestamp } from "lightweight-charts";
import { applyTick, movingAverage, type Bar } from "@/lib/chart-data";
import { kstTime } from "@/lib/format";

const C = {
  bg: "#121820",
  grid: "#1E2734",
  line: "#253040",
  axis: "#5C6878", // --muted-2: 차트 축 라벨 전용 (11px)
  up: "#F4516C",
  down: "#4F8CFF",
  warn: "#F5A524",
  ai: "#8B7CF6",
  ok: "#2ECC8A",
  vol: "#2A3442",
};

const KST = 9 * 3600;
const pad = (n: number) => String(n).padStart(2, "0");
function kstParts(t: number) {
  const d = new Date((t + KST) * 1000);
  return { mo: d.getUTCMonth() + 1, d: d.getUTCDate(), h: d.getUTCHours(), m: d.getUTCMinutes() };
}

export interface EntryMark {
  time: number;
  ts: string;
}

export function PriceChart({
  bars,
  tfSeconds,
  target,
  entries,
  livePrice,
  liveTs,
  label,
}: {
  bars: Bar[];
  tfSeconds: number;
  target: number | null;
  entries: EntryMark[];
  livePrice: number | null;
  liveTs: string | null;
  label: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candle = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ma = useRef<ISeriesApi<"Line"> | null>(null);
  const vol = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const targetLine = useRef<IPriceLine | null>(null);
  const data = useRef<Bar[]>([]);
  const ready = useRef<Promise<void> | null>(null);

  // 차트 만들기 (브라우저에서만 — 라이브러리를 동적으로 불러온다)
  useEffect(() => {
    let disposed = false;
    ready.current = (async () => {
      const lc = await import("lightweight-charts");
      if (disposed || !el.current) return;
      const c = lc.createChart(el.current, {
        autoSize: true,
        layout: {
          background: { type: lc.ColorType.Solid, color: C.bg },
          textColor: C.axis,
          fontSize: 11,
          fontFamily: "IBM Plex Mono, monospace",
          attributionLogo: false,
        },
        grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
        rightPriceScale: { borderColor: C.line },
        timeScale: {
          borderColor: C.line,
          timeVisible: true,
          tickMarkFormatter: (t: Time) => {
            const p = kstParts(Number(t));
            return p.h === 0 && p.m === 0 ? `${p.mo}/${p.d}` : `${pad(p.h)}:${pad(p.m)}`;
          },
        },
        localization: {
          locale: "ko-KR",
          timeFormatter: (t: Time) => {
            const p = kstParts(Number(t));
            return `${pad(p.mo)}-${pad(p.d)} ${pad(p.h)}:${pad(p.m)} KST`;
          },
        },
      });
      chart.current = c;
      candle.current = c.addSeries(lc.CandlestickSeries, {
        upColor: C.up,
        downColor: C.down,
        borderUpColor: C.up,
        borderDownColor: C.down,
        wickUpColor: C.up,
        wickDownColor: C.down,
        priceLineColor: C.up,
      });
      ma.current = c.addSeries(lc.LineSeries, {
        color: C.warn,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      vol.current = c.addSeries(lc.HistogramSeries, {
        color: C.vol,
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      markers.current = lc.createSeriesMarkers(candle.current, []);
    })();
    return () => {
      disposed = true;
      chart.current?.remove();
      chart.current = null;
      candle.current = null;
    };
  }, []);

  // 데이터
  useEffect(() => {
    void ready.current?.then(() => {
      if (!candle.current) return;
      data.current = [...bars];
      const t = (b: Bar) => b.time as UTCTimestamp;
      candle.current.setData(bars.map((b) => ({ time: t(b), open: b.open, high: b.high, low: b.low, close: b.close })));
      ma.current?.setData(movingAverage(bars, 20).map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
      vol.current?.setData(bars.map((b) => ({ time: t(b), value: b.volume, color: b.close >= b.open ? "rgba(244,81,108,0.55)" : C.vol })));
      chart.current?.timeScale().fitContent();
    });
  }, [bars]);

  // 목표가 점선
  useEffect(() => {
    void ready.current?.then(async () => {
      if (!candle.current) return;
      const lc = await import("lightweight-charts");
      if (targetLine.current) candle.current.removePriceLine(targetLine.current);
      targetLine.current = null;
      if (target != null) {
        targetLine.current = candle.current.createPriceLine({
          price: target,
          color: C.ai,
          lineWidth: 2,
          lineStyle: lc.LineStyle.Dashed,
          axisLabelVisible: true,
          title: "목표가",
        });
      }
    });
  }, [target]);

  // 진입 마커
  useEffect(() => {
    void ready.current?.then(() => {
      markers.current?.setMarkers(
        entries.map((e) => ({
          time: (Math.floor(e.time / tfSeconds) * tfSeconds) as UTCTimestamp,
          position: "belowBar" as const,
          shape: "arrowUp" as const,
          color: C.ok,
          text: `진입 ${kstTime(e.ts)}`,
        })),
      );
    });
  }, [entries, tfSeconds]);

  // 실시간 체결가 → 마지막 봉
  useEffect(() => {
    if (livePrice == null || !liveTs) return;
    const ts = Math.floor(Date.parse(liveTs) / 1000);
    if (!Number.isFinite(ts)) return;
    const bar = applyTick(data.current, tfSeconds, ts, livePrice);
    if (!bar || !candle.current) return;
    const arr = data.current;
    if (arr.length && arr[arr.length - 1]!.time === bar.time) arr[arr.length - 1] = bar;
    else arr.push(bar);
    candle.current.update({ time: bar.time as UTCTimestamp, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
  }, [livePrice, liveTs, tfSeconds]);

  return <div ref={el} role="img" aria-label={label} className="h-[360px] w-full" />;
}

"use client";

// 자산 곡선 카드 (Main.dc.html 행 2 왼쪽). 대시보드(업비트)와 포트폴리오(시장 선택)가 같이 쓴다.
// 데이터: GET /portfolio/equity (equity_snapshots, 벤치마크는 업비트 BTC 보유뿐 — ADR 0020 §2).
// 선은 늘어나는 SVG(preserveAspectRatio="none")로 그리고, 찌그러지면 안 되는 끝점·수익률·날짜 눈금은 HTML로 얹는다.
import { useState, type CSSProperties, type ReactNode } from "react";
import { Card, CardSkeleton, EmptyNote, ErrorNote, Segmented, cx } from "@/components/ui/primitives";
import { reasonText } from "@/lib/api";
import { fmtKrw, fmtPct, fmtUsd } from "@/lib/format";
import { curvePaths } from "@/lib/metrics";
import { useEquityCurve } from "@/lib/queries";

const CURVE_W = 600;
const CURVE_H = 200;
const PAD = 14; // 선이 위아래 끝에 붙지 않게 viewBox를 넓힌 몫

const pos = (x: number, y: number): CSSProperties => ({
  left: `${(x / CURVE_W) * 100}%`,
  top: `${((y + PAD) / (CURVE_H + PAD * 2)) * 100}%`,
});

export function EquityCurveCard({
  market = "upbit",
  subtitle = "전략 합산 vs BTC 보유 (같은 자본)",
  extra,
}: {
  market?: string;
  subtitle?: string;
  /** 제목 줄 오른쪽, 기간 버튼 앞에 놓을 것 (포트폴리오의 시장 선택) */
  extra?: ReactNode;
}) {
  const [range, setRange] = useState<"30" | "90" | "365">("30");
  const curve = useEquityCurve(market, Number(range) as 30 | 90 | 365);
  const paths = curvePaths(curve.data?.points ?? [], curve.data?.benchmark, CURVE_W, CURVE_H);
  const money = market === "us" ? (v: number) => fmtUsd(v) : fmtKrw;
  // 두 끝점이 가까우면 라벨이 겹치므로, 위에 있는 선의 라벨은 위로, 아래 선의 라벨은 아래로 단다
  const mainAbove = !paths?.benchEnd || paths.end.y <= paths.benchEnd.y;
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-[15px] font-semibold">자산 곡선 · 최근 {range === "365" ? "1년" : `${range}일`}</h2>
          <div className="text-xs text-muted">{subtitle}</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {extra}
          <Segmented
            label="기간"
            framed={false}
            value={range}
            onChange={setRange}
            options={[
              { value: "30", label: "30일" },
              { value: "90", label: "90일" },
              { value: "365", label: "1년" },
            ]}
          />
        </div>
      </div>
      <div className="flex h-[212px] items-center justify-center">
        {curve.isLoading ? (
          <CardSkeleton lines={3} className="w-full border-0 p-0" />
        ) : curve.isError ? (
          <ErrorNote>자산 곡선을 불러오지 못했습니다: {reasonText(curve.error)}</ErrorNote>
        ) : paths ? (
          <figure className="flex h-full w-full flex-col gap-1">
            <div className="relative h-[168px] w-full">
              <svg
                viewBox={`0 ${-PAD} ${CURVE_W} ${CURVE_H + PAD * 2}`}
                preserveAspectRatio="none"
                role="img"
                aria-label={`자산 곡선: ${money(paths.min)} ~ ${money(paths.max)}, 기간 수익률 ${fmtPct(paths.ret, { digits: 1 })}${
                  paths.benchRet != null ? `, BTC 보유 ${fmtPct(paths.benchRet, { digits: 1 })}` : ""
                }`}
                className="h-full w-full"
              >
                <g className="text-line2" stroke="currentColor" strokeWidth={1}>
                  {[0, 1, 2, 3].map((i) => (
                    <line key={i} x1={0} x2={CURVE_W} y1={(CURVE_H * i) / 3} y2={(CURVE_H * i) / 3} vectorEffect="non-scaling-stroke" />
                  ))}
                </g>
                {paths.bench ? (
                  <polyline
                    points={paths.bench}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    strokeDasharray="4 4"
                    vectorEffect="non-scaling-stroke"
                    className="text-bench"
                  />
                ) : null}
                <polyline
                  points={paths.main}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                  className="text-ai"
                />
              </svg>
              <span
                aria-hidden="true"
                className="absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-ai"
                style={pos(paths.end.x, paths.end.y)}
              />
              <span
                aria-hidden="true"
                className={cx(
                  "num absolute right-1.5 rounded-[4px] bg-bg2 px-1 text-[11px] font-semibold text-ink",
                  mainAbove ? "-translate-y-[calc(100%+6px)]" : "translate-y-[6px]",
                )}
                style={{ top: pos(0, paths.end.y).top }}
              >
                {market === "upbit" ? "전략 " : ""}
                {fmtPct(paths.ret, { digits: 1 })}
              </span>
              {paths.benchEnd ? (
                <span
                  aria-hidden="true"
                  className={cx(
                    "num absolute right-1.5 rounded-[4px] bg-bg2 px-1 text-[11px] text-muted",
                    mainAbove ? "translate-y-[6px]" : "-translate-y-[calc(100%+6px)]",
                  )}
                  style={{ top: pos(0, paths.benchEnd.y).top }}
                >
                  BTC {fmtPct(paths.benchRet, { digits: 1 })}
                </span>
              ) : null}
            </div>
            <div aria-hidden="true" className="num flex justify-between text-[11px] text-muted">
              {paths.xTicks.map((t) => (
                <span key={t.label}>{t.label}</span>
              ))}
            </div>
            <figcaption className="flex justify-between gap-2 text-[11px] text-muted">
              <span className="num">
                {money(paths.min)} ~ {money(paths.max)}
              </span>
              <span>
                {paths.bench
                  ? "실선 전략 합산 · 점선 BTC 보유"
                  : market === "upbit"
                    ? "BTC 보유 비교값 없음 (봉 없음)"
                    : "이 시장은 비교 벤치마크가 없습니다"}
              </span>
            </figcaption>
          </figure>
        ) : (
          <EmptyNote>아직 쌓인 평가액 스냅샷이 없습니다. scheduler가 1분마다 기록하면 이 자리에 그립니다.</EmptyNote>
        )}
      </div>
    </Card>
  );
}

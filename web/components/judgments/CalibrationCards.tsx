"use client";

// 보정 카드 4개 (AILog.dc.html 상단): Brier · ECE · 확신도 구간별 적중률 · 게이팅 A/B MDD.
// 값은 /judgments/calibration·/judgments/ab (weeks=4). 보정 소스가 연결되지 않았으면(503) 그렇다고 적는다.
import { Card, cx } from "@/components/ui/primitives";
import { reasonText } from "@/lib/api";
import { DASH, fmtNumber, fmtPct } from "@/lib/format";
import { useAb, useCalibration } from "@/lib/queries";

const BRIER_COIN = 0.25; // 동전 던지기 기준 (judgment/calibration.py BRIER_COIN과 같은 정의)

function Shell({ children, ai = false }: { children: React.ReactNode; ai?: boolean }) {
  return (
    <Card as="div" ai={ai} className="flex flex-col gap-1 rounded-[12px] px-4 py-3.5">
      {children}
    </Card>
  );
}

const negMdd = (v: number | null | undefined) => (v == null ? null : -Math.abs(v));

export function CalibrationCards() {
  const cal = useCalibration();
  const ab = useAb();
  const c = cal.data;
  const a = ab.data;
  const unavailable = cal.isError ? reasonText(cal.error) : null;
  const buckets = (c?.buckets ?? []).filter((b) => b.n > 0).slice(-3);
  return (
    <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
      <Shell>
        <div className="text-xs text-muted">Brier score · 최근 4주</div>
        <div className="flex items-baseline gap-2">
          <span className="num min-w-[4ch] text-2xl font-bold">{cal.isLoading ? "…" : fmtNumber(c?.brier, 2)}</span>
          {c?.brier != null ? (
            <span className={cx("text-xs font-semibold", c.brier < BRIER_COIN ? "text-ok-ink" : "text-warn")}>
              목표 0.25 미만 {c.brier < BRIER_COIN ? "충족" : "미충족"}
            </span>
          ) : null}
        </div>
        <div className="text-[11px] text-muted">{unavailable ?? "동전 던지기 0.25 · 낮을수록 보정이 좋음"}</div>
      </Shell>
      <Shell>
        <div className="text-xs text-muted">ECE (기대 보정 오차)</div>
        <div className="flex items-baseline gap-2">
          <span className="num min-w-[4ch] text-2xl font-bold">{cal.isLoading ? "…" : fmtNumber(c?.ece, 2)}</span>
          <span className="text-xs text-muted">판단 {c ? c.n : DASH}건</span>
        </div>
        <div className="text-[11px] text-muted">확신도 0.8이면 실제로 약 80% 맞음</div>
      </Shell>
      <Shell>
        <div className="text-xs text-muted">확신도 구간별 적중률 (24시간 후 방향)</div>
        {buckets.length ? (
          <ul className="flex flex-col gap-1 text-[11px]">
            {buckets.map((b) => (
              <li key={b.range} className="flex items-center gap-2">
                <span className="num w-[52px] text-muted">{b.range}</span>
                <span aria-hidden="true" className="h-1.5 flex-grow overflow-hidden rounded-[3px] bg-bg0">
                  <span
                    className={cx("block h-1.5", (b.avg_conf ?? 0) >= 0.7 ? "bg-ai" : "bg-muted2")}
                    style={{ width: `${(b.hit_rate ?? 0) * 100}%` }}
                  />
                </span>
                <span className="num w-[34px] text-right">{fmtPct(b.hit_rate, { signed: false, digits: 0 })}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-[11px] text-muted">{cal.isLoading ? "불러오는 중…" : "24시간 결과가 쌓인 판단이 아직 없습니다"}</div>
        )}
      </Shell>
      <Shell ai>
        <div className="text-xs text-muted">게이팅 효과 · 같은 기간 A/B</div>
        <div className="flex items-baseline gap-2">
          <span className="num text-2xl font-bold">MDD {ab.isLoading ? "…" : fmtPct(negMdd(a?.on.mdd))}</span>
          <span className="text-xs text-muted">vs OFF {fmtPct(negMdd(a?.off.mdd))}</span>
        </div>
        <div className="text-[11px] text-muted">
          {ab.isError
            ? reasonText(ab.error)
            : `수익률 ${fmtPct(a?.on.ret)} vs ${fmtPct(a?.off.ret)} · 관문 G2 조건: ON의 MDD가 낮을 것${a ? (a.g2_pass ? " (통과)" : " (미통과)") : ""}`}
        </div>
      </Shell>
    </div>
  );
}

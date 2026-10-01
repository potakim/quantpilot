"use client";

// TanStack Query 훅 모음. 화면의 숫자는 전부 여기서 받은 API 응답이다 (docs/10 §5).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { apiFetch } from "@/lib/api";
import type {
  AbReport,
  Calibration,
  FillRow,
  Gates,
  Health,
  JudgmentDetail,
  JudgmentRow,
  OrderRow,
  Page,
  Portfolio,
  PositionView,
  Quote,
  RiskEventRow,
  SettingsView,
  StrategyView,
} from "@/lib/types";
import type { ApiCandle } from "@/lib/chart-data";
import { useLive } from "@/lib/ws";

const qs = (params: Record<string, string | number | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const useHealth = () =>
  useQuery({ queryKey: ["health"], queryFn: () => apiFetch<Health>("/health"), refetchInterval: 15_000 });

export const usePortfolio = () =>
  useQuery({ queryKey: ["portfolio"], queryFn: () => apiFetch<Portfolio>("/portfolio"), refetchInterval: 30_000 });

export const useStrategies = () =>
  useQuery({ queryKey: ["strategies"], queryFn: () => apiFetch<StrategyView[]>("/strategies") });

export const useGates = () =>
  useQuery({ queryKey: ["gates"], queryFn: () => apiFetch<Gates>("/reports/gates"), staleTime: 60_000 });

export const useSettings = () =>
  useQuery({ queryKey: ["settings"], queryFn: () => apiFetch<SettingsView>("/settings"), staleTime: 60_000 });

export interface JudgmentFilter {
  from?: string;
  market?: string;
  strategy?: string;
  outcome?: string;
  symbol?: string;
  limit?: number;
}

export const useJudgments = (f: JudgmentFilter) =>
  useQuery({
    queryKey: ["judgments", f],
    queryFn: () => apiFetch<Page<JudgmentRow>>(`/judgments${qs({ ...f })}`),
  });

export const useJudgment = (id: number | null) =>
  useQuery({
    queryKey: ["judgment", id],
    queryFn: () => apiFetch<JudgmentDetail>(`/judgments/${id}`),
    enabled: id !== null,
  });

export const useCalibration = () =>
  useQuery({
    queryKey: ["calibration"],
    queryFn: () => apiFetch<Calibration>("/judgments/calibration?weeks=4"),
    retry: false,
    staleTime: 60_000,
  });

export const useAb = () =>
  useQuery({
    queryKey: ["ab"],
    queryFn: () => apiFetch<AbReport>("/judgments/ab?weeks=4"),
    retry: false,
    staleTime: 60_000,
  });

export const useFills = (f: { market?: string; from?: string; limit?: number }) =>
  useQuery({
    queryKey: ["fills", f],
    queryFn: () => apiFetch<Page<FillRow>>(`/fills${qs(f)}`),
  });

export const useOrders = (f: { market?: string; status?: string; limit?: number }) =>
  useQuery({
    queryKey: ["orders", f],
    queryFn: () => apiFetch<Page<OrderRow>>(`/orders${qs(f)}`),
  });

export const usePositions = (market?: string) =>
  useQuery({
    queryKey: ["positions", market ?? "all"],
    queryFn: () => apiFetch<PositionView[]>(`/positions${qs({ market })}`),
  });

export const useQuote = (market: string, symbol: string, enabled = true) =>
  useQuery({
    queryKey: ["quote", market, symbol],
    queryFn: () => apiFetch<Quote>(`/quotes/${market}/${encodeURIComponent(symbol)}`),
    retry: false,
    enabled,
    refetchInterval: 30_000,
  });

export const useCandles = (market: string, symbol: string, tf: string, limit = 300) =>
  useQuery({
    queryKey: ["candles", market, symbol, tf, limit],
    queryFn: () => apiFetch<ApiCandle[]>(`/candles/${market}/${encodeURIComponent(symbol)}${qs({ tf, limit })}`),
  });

export const useRiskEvents = (enabled: boolean) =>
  useQuery({
    queryKey: ["risk-events"],
    queryFn: () => apiFetch<Page<RiskEventRow>>("/risk/events?limit=5"),
    enabled,
  });

/** WS 채널이 갱신되면 해당 조회를 다시 받는다. */
export function useLiveInvalidation(): void {
  const qc = useQueryClient();
  const seq = useLive((s) => s.seq);
  useEffect(() => {
    if (seq.portfolio) void qc.invalidateQueries({ queryKey: ["portfolio"] });
  }, [qc, seq.portfolio]);
  useEffect(() => {
    if (seq.judgments) void qc.invalidateQueries({ queryKey: ["judgments"] });
  }, [qc, seq.judgments]);
  useEffect(() => {
    if (!seq.fills) return;
    for (const key of ["fills", "positions", "portfolio", "orders"]) void qc.invalidateQueries({ queryKey: [key] });
  }, [qc, seq.fills]);
  useEffect(() => {
    if (seq.orders) void qc.invalidateQueries({ queryKey: ["orders"] });
  }, [qc, seq.orders]);
  useEffect(() => {
    if (seq.strategy) void qc.invalidateQueries({ queryKey: ["strategies"] });
  }, [qc, seq.strategy]);
  useEffect(() => {
    if (!seq.risk) return;
    for (const key of ["health", "portfolio", "risk-events"]) void qc.invalidateQueries({ queryKey: [key] });
  }, [qc, seq.risk]);
}

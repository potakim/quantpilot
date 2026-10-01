"use client";

// /trade — 첫 전략의 첫 대상 종목으로 보낸다 (종목을 코드에 박지 않는다).
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { CardSkeleton, EmptyNote, ErrorNote } from "@/components/ui/primitives";
import { reasonText } from "@/lib/api";
import { useStrategies } from "@/lib/queries";

export function TradeIndex() {
  const router = useRouter();
  const { data, isError, error } = useStrategies();
  const first = data?.find((s) => s.enabled && s.symbols.length) ?? data?.find((s) => s.symbols.length);
  useEffect(() => {
    if (first) router.replace(`/trade/${first.market}/${encodeURIComponent(first.symbols[0]!)}`);
  }, [first, router]);
  return (
    <div className="p-6">
      <h1 className="sr-only">거래 · 차트</h1>
      {isError ? <ErrorNote>{reasonText(error)}</ErrorNote> : data && !first ? <EmptyNote>전략 대상 종목이 없습니다</EmptyNote> : <CardSkeleton />}
    </div>
  );
}

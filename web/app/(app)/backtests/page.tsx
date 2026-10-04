import type { Metadata } from "next";
import { BacktestView } from "@/components/backtests/BacktestView";

export const metadata: Metadata = { title: "백테스트" };

type Props = { searchParams: Promise<{ strategy?: string; id?: string }> };

export default async function Page({ searchParams }: Props) {
  const { strategy, id } = await searchParams;
  const n = id && /^\d+$/.test(id) ? Number(id) : undefined;
  return <BacktestView initialStrategy={strategy} initialId={n} />;
}

import type { Metadata } from "next";
import { StrategySettings } from "@/components/strategies/StrategySettings";

export const metadata: Metadata = { title: "전략 설정" };

export default function Page() {
  return <StrategySettings />;
}

import type { Metadata } from "next";
import { PortfolioView } from "@/components/portfolio/PortfolioView";

export const metadata: Metadata = { title: "포트폴리오" };

export default function Page() {
  return <PortfolioView />;
}

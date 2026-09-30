import type { Metadata } from "next";
import { JudgmentLog } from "@/components/judgments/JudgmentLog";

export const metadata: Metadata = { title: "AI 판단 로그" };

export default function JudgmentsPage() {
  return <JudgmentLog />;
}

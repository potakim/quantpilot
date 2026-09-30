import type { Metadata } from "next";
import { Phase2 } from "@/components/Phase2";

export const metadata: Metadata = { title: "백테스트" };

export default function Page() {
  return <Phase2 title="백테스트" />;
}

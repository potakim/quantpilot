import Link from "next/link";
import { Card } from "@/components/ui/primitives";

// 2단계 화면 자리 (docs/10 §1: 전략 설정·백테스트는 2단계). 가짜 데이터를 보여 주지 않는다.
export function Phase2({ title }: { title: string }) {
  return (
    <div className="p-5 md:p-6">
      <Card className="flex max-w-xl flex-col gap-3 p-6">
        <h1 className="text-lg font-semibold">{title}</h1>
        <p className="text-sm text-ink2">이 화면은 2단계에서 제공합니다.</p>
        <Link href="/" className="text-[13px] font-medium no-underline">
          대시보드로 →
        </Link>
      </Card>
    </div>
  );
}

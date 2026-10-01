"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MOBILE_NAV, isActive } from "@/components/layout/nav";
import { cx } from "@/components/ui/primitives";

// 모바일 하단 탭 5개 (Mobile.dc.html): 높이 84px(세이프 에어리어 포함), 터치 타깃 44px 이상.
export function MobileTabBar() {
  const pathname = usePathname() ?? "/";
  return (
    <nav
      aria-label="주 메뉴"
      className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 gap-1 border-t border-line bg-bg1 px-3 pt-2.5 md:hidden"
      style={{ minHeight: 84, paddingBottom: "max(24px, env(safe-area-inset-bottom))" }}
    >
      {MOBILE_NAV.map(({ href, label, Icon }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cx(
              "flex min-h-[48px] flex-col items-center justify-center gap-1 text-[11px] no-underline",
              active ? "font-semibold text-ink hover:text-ink" : "font-medium text-muted hover:text-ink2",
            )}
          >
            <Icon size={22} />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

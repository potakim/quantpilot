"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MonthLossCard } from "@/components/layout/MonthLossCard";
import { SETTINGS_NAV, SIDE_NAV, isActive } from "@/components/layout/nav";
import { cx } from "@/components/ui/primitives";

// 데스크톱 사이드바 220px (docs/10 §3). 활성 항목은 --bg-3 + --ink. 768px 미만에서는 하단 탭으로 바뀐다.
function NavLink({ href, label, Icon, active }: { href: string; label: string; Icon: (p: { size?: number }) => React.ReactNode; active: boolean }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cx(
        "flex items-center gap-3 rounded-block px-3.5 py-2.5 text-sm no-underline",
        active ? "bg-bg3 font-semibold text-ink hover:text-ink" : "font-medium text-muted hover:text-ink2",
      )}
    >
      <Icon />
      {label}
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname() ?? "/";
  return (
    <nav
      aria-label="주 메뉴"
      className="hidden w-[220px] shrink-0 flex-col gap-1 border-r border-line bg-bg1 px-3 py-4 md:flex"
    >
      {SIDE_NAV.map((n) => (
        <NavLink key={n.href} href={n.href} label={n.label} Icon={n.Icon} active={isActive(pathname, n.href)} />
      ))}
      <div className="flex-grow" />
      <MonthLossCard />
      <NavLink
        href={SETTINGS_NAV.href}
        label={SETTINGS_NAV.label}
        Icon={SETTINGS_NAV.Icon}
        active={isActive(pathname, SETTINGS_NAV.href)}
      />
    </nav>
  );
}

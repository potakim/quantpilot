import type { Metadata } from "next";
import { Dashboard } from "@/components/dashboard/Dashboard";

export const metadata: Metadata = { title: "대시보드" };

export default function DashboardPage() {
  return <Dashboard />;
}

import type { Metadata } from "next";
import { SettingsView } from "@/components/settings/SettingsView";

export const metadata: Metadata = { title: "설정 · API 키" };

export default function SettingsPage() {
  return <SettingsView />;
}

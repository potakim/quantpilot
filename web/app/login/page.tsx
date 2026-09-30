import type { Metadata } from "next";
import { Suspense } from "react";
import { LoginForm } from "@/components/LoginForm";

export const metadata: Metadata = { title: "로그인" };

export default function LoginPage() {
  return (
    <main id="main" className="flex min-h-screen items-center justify-center px-5">
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </main>
  );
}

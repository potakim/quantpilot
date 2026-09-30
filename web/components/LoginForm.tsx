"use client";

// 로그인 (ADR 0018 §1): 비밀번호를 같은 출처 /api/session으로 보내고, 토큰은 서버가 httpOnly 쿠키로 심는다.
// 비밀번호는 제출 직후 입력칸에서 지운다. 어디에도 저장하지 않는다.
import { useRouter, useSearchParams } from "next/navigation";
import { useRef, useState } from "react";
import { parseError, reasonText } from "@/lib/api";

/** ?next= 는 같은 사이트 경로만 받는다 (열린 리다이렉트 방지). */
export function safeNext(v: string | null): string {
  return v && /^\/(?!\/)[^\\]*$/.test(v) ? v : "/";
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const pwRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const input = pwRef.current;
    const password = input?.value ?? "";
    if (!password) {
      setError("비밀번호를 입력하세요");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password }),
        credentials: "same-origin",
      });
      if (input) input.value = "";
      if (!res.ok) {
        const body: unknown = await res.json().catch(() => null);
        const err = parseError(res.status, body);
        setError(res.status === 401 ? "비밀번호가 맞지 않습니다" : reasonText(err));
        return;
      }
      router.replace(safeNext(params.get("next")));
      router.refresh();
    } catch {
      setError("서버에 연결할 수 없습니다");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="flex w-full max-w-[360px] flex-col gap-4 rounded-card border border-line bg-bg2 p-6"
      aria-describedby="login-help"
    >
      <div className="flex items-center gap-2.5">
        <span aria-hidden="true" className="flex h-[26px] w-[26px] items-center justify-center rounded-btn bg-ai text-sm font-bold text-bg0">
          Q
        </span>
        <h1 className="text-base font-bold">QuantPilot 로그인</h1>
      </div>
      <p id="login-help" className="text-xs text-muted">
        단일 사용자 계정입니다. 서버에 설정한 관리자 비밀번호를 입력하세요.
      </p>
      <input type="text" name="username" autoComplete="username" value="admin" readOnly hidden />
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        비밀번호
        <input
          ref={pwRef}
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          className="h-10 rounded-btn border border-line bg-bg0 px-3 text-sm text-ink"
        />
      </label>
      {error ? (
        <p role="alert" className="rounded-block border border-warn-line bg-warn-bg px-3 py-2 text-xs text-warn-ink">
          {error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={busy}
        className="h-11 rounded-block bg-ai text-sm font-bold text-bg0 disabled:opacity-60"
      >
        {busy ? "확인 중…" : "로그인"}
      </button>
    </form>
  );
}

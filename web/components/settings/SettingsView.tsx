"use client";

// 설정 · API 키 (1단계 최소). 불변식 #10: 키 값은 POST /settings/keys로 보내기만 하고, 저장 뒤 입력칸을 비우며
// 다시 보여 주지 않는다 (API도 이름과 restart_required만 돌려준다). 불변식 #6: 리스크 규칙은 읽기 전용 표시뿐 —
// 이 앱 어디에도 규칙을 바꾸는 입력이 없다.
import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { IconLock } from "@/components/ui/Icons";
import { Card, CardSkeleton, ErrorNote } from "@/components/ui/primitives";
import { apiFetch, reasonText } from "@/lib/api";
import { fmtPct } from "@/lib/format";
import { useSettings } from "@/lib/queries";

// system.py KEY_NAMES와 같은 목록 (값이 아니라 이름이다)
const KEY_NAMES: [string, string][] = [
  ["upbit_access_key", "업비트 Access Key"],
  ["upbit_secret_key", "업비트 Secret Key"],
  ["kis_app_key", "KIS App Key"],
  ["kis_app_secret", "KIS App Secret"],
  ["kis_account", "KIS 계좌번호"],
  ["alpaca_key", "Alpaca Key"],
  ["alpaca_secret", "Alpaca Secret"],
  ["typesafe_api_key", "TypeSafe API Key"],
  ["anthropic_api_key", "Anthropic API Key"],
  ["google_api_key", "Google API Key"],
  ["dart_api_key", "DART API Key"],
  ["telegram_bot_token", "텔레그램 봇 토큰"],
  ["telegram_chat_id", "텔레그램 채팅 ID"],
];

function KeyForm() {
  const [name, setName] = useState(KEY_NAMES[0]![0]);
  const valueRef = useRef<HTMLInputElement>(null);
  const pwRef = useRef<HTMLInputElement>(null);
  const save = useMutation({
    mutationFn: (body: { keys: Record<string, string>; confirm_password: string }) =>
      apiFetch<{ stored: string[]; restart_required: boolean }>("/settings/keys", { method: "POST", json: body }),
  });

  function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const value = valueRef.current?.value ?? "";
    const pw = pwRef.current?.value ?? "";
    // 입력칸은 결과와 상관없이 바로 비운다 — 값이 화면에 남지 않게
    if (valueRef.current) valueRef.current.value = "";
    if (pwRef.current) pwRef.current.value = "";
    if (!value || !pw) return;
    save.mutate({ keys: { [name]: value }, confirm_password: pw });
  }

  return (
    <Card className="flex flex-col gap-4 p-5">
      <h2 className="text-[15px] font-semibold">거래소 · AI API 키 등록</h2>
      <p className="text-xs leading-relaxed text-muted">
        값은 서버의 키 파일(권한 0600)에 저장만 되고 화면·API 응답·로그에 다시 나오지 않습니다. 재시작 후 적용됩니다.
      </p>
      <form onSubmit={submit} className="flex flex-col gap-3" autoComplete="off">
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          키 이름
          <select
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-10 rounded-btn border border-line bg-bg0 px-3 text-sm text-ink"
          >
            {KEY_NAMES.map(([v, l]) => (
              <option key={v} value={v}>
                {l} ({v})
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          키 값
          <input
            ref={valueRef}
            type="password"
            name="key_value"
            autoComplete="new-password"
            spellCheck={false}
            className="h-10 rounded-btn border border-line bg-bg0 px-3 text-sm text-ink"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          비밀번호 확인 (2차 확인)
          <input
            ref={pwRef}
            type="password"
            name="confirm_password"
            autoComplete="current-password"
            className="h-10 rounded-btn border border-line bg-bg0 px-3 text-sm text-ink"
          />
        </label>
        <button
          type="submit"
          disabled={save.isPending}
          className="h-10 self-start rounded-btn bg-ink px-5 text-[13px] font-bold text-bg0 disabled:opacity-60"
        >
          {save.isPending ? "저장 중…" : "저장"}
        </button>
      </form>
      {save.isError ? <ErrorNote>{reasonText(save.error)}</ErrorNote> : null}
      {save.data ? (
        <p role="status" className="rounded-block border border-warn-line bg-warn-bg px-3 py-2.5 text-xs text-warn-ink">
          저장했습니다: {save.data.stored.join(", ")}
          {save.data.restart_required ? " · 서버를 재시작해야 적용됩니다 (restart_required)" : ""}
        </p>
      ) : null}
    </Card>
  );
}

function RiskRulesCard() {
  const q = useSettings();
  const r = q.data?.risk_rules;
  const rows: [string, string][] = r
    ? [
        ["거래당 최대 손실", fmtPct(r.max_loss_per_trade, { signed: false, digits: 0 })],
        ["월 손실 한도 (서킷브레이커)", fmtPct(r.monthly_loss_limit, { signed: false, digits: 0 })],
        ["종목 비중 상한", fmtPct(r.max_symbol_weight, { signed: false, digits: 0 })],
        ["단타 전략 비중 합 상한", fmtPct(r.max_intraday_weight, { signed: false, digits: 0 })],
        ["종목당 초당 주문", `${r.max_orders_per_symbol_per_sec}건`],
        ["연속 API 오류 할트", `${r.max_consecutive_api_errors}회`],
      ]
    : [];
  return (
    <Card className="flex flex-col gap-3 p-5">
      <h2 className="flex items-center gap-2 text-[15px] font-semibold">
        <IconLock size={16} className="text-muted" />
        리스크 규칙 (코드 고정 · 수정 불가)
      </h2>
      <p className="text-xs text-muted">
        리스크 규칙은 코드 상수입니다. 화면·API·AI·설정 파일 어디서도 더 느슨하게 바꿀 수 없습니다.
      </p>
      {q.isLoading ? <CardSkeleton lines={4} className="border-0 p-0" /> : null}
      {q.isError ? <ErrorNote>{reasonText(q.error)}</ErrorNote> : null}
      {r ? (
        <dl className="grid grid-cols-1 gap-1.5 text-[13px] sm:grid-cols-2">
          {rows.map(([k, v]) => (
            <div key={k} className="flex justify-between rounded-[8px] bg-bg3 px-3 py-2">
              <dt className="text-ink2">{k}</dt>
              <dd className="num flex items-center gap-1.5">
                {v}
                <IconLock size={12} className="text-muted" />
                <span className="sr-only">잠김</span>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </Card>
  );
}

function Logout() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        await fetch("/api/session", { method: "DELETE", credentials: "same-origin" }).catch(() => null);
        router.replace("/login");
      }}
      className="h-10 self-start rounded-btn border border-line px-5 text-[13px] text-ink2"
    >
      로그아웃
    </button>
  );
}

export function SettingsView() {
  return (
    <div className="flex max-w-3xl flex-col gap-4 p-5 md:p-6">
      <h1 className="text-lg font-semibold">설정 · API 키</h1>
      <RiskRulesCard />
      <KeyForm />
      <Logout />
    </div>
  );
}

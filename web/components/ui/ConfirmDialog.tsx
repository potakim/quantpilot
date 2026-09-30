"use client";

// 비밀번호 재확인 대화상자 (할트 해제처럼 사람이 확인해야 하는 조작). 네이티브 <dialog> — 초점 가두기·Esc 닫기.
import { useEffect, useId, useRef, useState } from "react";
import { reasonText } from "@/lib/api";

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  description: React.ReactNode;
  confirmLabel: string;
  onConfirm: (password: string) => Promise<void>;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const pwRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setError(null);
      d.showModal();
      pwRef.current?.focus();
    } else if (!open && d.open) d.close();
  }, [open]);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const input = pwRef.current;
    const pw = input?.value ?? "";
    if (!pw) {
      setError("비밀번호를 입력하세요");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onConfirm(pw);
      if (input) input.value = "";
      onClose();
    } catch (err) {
      setError(reasonText(err));
    } finally {
      setBusy(false);
      if (input) input.value = "";
    }
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby={`${id}-t`}
      aria-describedby={`${id}-d`}
      onClose={onClose}
      className="w-[min(420px,calc(100vw-32px))] rounded-card border border-line bg-bg2 p-0 text-ink backdrop:bg-black/60"
    >
      <form onSubmit={submit} className="flex flex-col gap-3 p-5">
        <h2 id={`${id}-t`} className="text-[15px] font-semibold">
          {title}
        </h2>
        <div id={`${id}-d`} className="text-[13px] leading-relaxed text-ink2">
          {description}
        </div>
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          비밀번호 확인
          <input
            ref={pwRef}
            type="password"
            name="confirm_password"
            autoComplete="current-password"
            className="h-10 rounded-btn border border-line bg-bg0 px-3 text-sm text-ink"
          />
        </label>
        {error ? (
          <p role="alert" className="rounded-block border border-warn-line bg-warn-bg px-3 py-2 text-xs text-warn-ink">
            {error}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="h-9 rounded-btn border border-line px-4 text-[13px] text-ink2">
            취소
          </button>
          <button
            type="submit"
            disabled={busy}
            className="h-9 rounded-btn bg-warn px-4 text-[13px] font-bold text-bg0 disabled:opacity-60"
          >
            {busy ? "처리 중…" : confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}

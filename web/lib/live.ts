"use client";

// 앱 전체가 공유하는 WS 연결 하나. 토큰은 같은 출처 /api/session/ws-token에서 받아 connect() 안에서만 쓴다.
import { useEffect } from "react";
import { LiveSocket, liveStore, type WsAuth } from "@/lib/ws";

async function getAuth(): Promise<WsAuth> {
  const res = await fetch("/api/session/ws-token", { cache: "no-store", credentials: "same-origin" });
  if (res.status === 401) {
    window.location.assign("/login");
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(`ws-token HTTP ${res.status}`);
  return (await res.json()) as WsAuth;
}

let socket: LiveSocket | null = null;
let users = 0;

function ensureSocket(): LiveSocket {
  socket ??= new LiveSocket({
    getAuth,
    store: liveStore,
    onUnauthorized: () => window.location.assign("/login"),
  });
  return socket;
}

/** 앱 셸이 켜 있는 동안 연결을 유지한다. */
export function useLiveConnection(): void {
  useEffect(() => {
    const s = ensureSocket();
    users += 1;
    if (users === 1) void s.start();
    return () => {
      users -= 1;
      if (users === 0) s.stop();
    };
  }, []);
}

/** 화면이 쓰는 채널을 구독한다 (마운트 동안). */
export function useLiveChannels(channels: string[]): void {
  const key = [...channels].sort().join("|");
  useEffect(() => {
    if (!key) return;
    return ensureSocket().acquire(key.split("|"));
  }, [key]);
}

"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, setUnauthorizedHandler } from "@/lib/api";

function toLogin() {
  const next = window.location.pathname;
  window.location.assign(next && next !== "/" && next !== "/login" ? `/login?next=${encodeURIComponent(next)}` : "/login");
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            refetchOnWindowFocus: false,
            retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
          },
        },
      }),
  );
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (window.location.pathname !== "/login") toLogin();
    });
    return () => setUnauthorizedHandler(undefined);
  }, []);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

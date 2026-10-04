import type { Config } from "tailwindcss";

// 토큰은 app/globals.css의 CSS 변수가 원본이다 (docs/10 §2). 여기서는 이름만 잇는다.
const v = (name: string) => `var(--${name})`;

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg0: v("bg-0"),
        bg1: v("bg-1"),
        bg2: v("bg-2"),
        bg3: v("bg-3"),
        bg4: v("bg-4"),
        line: v("line"),
        line2: v("line-2"),
        ink: v("ink"),
        ink2: v("ink-2"),
        muted: v("muted"),
        muted2: v("muted-2"),
        up: v("up"),
        "up-bg": v("up-bg"),
        down: v("down"),
        "down-bg": v("down-bg"),
        "down-2": v("down-2"),
        ok: v("ok"),
        "ok-ink": v("ok-ink"),
        "ok-bg": v("ok-bg"),
        warn: v("warn"),
        "warn-ink": v("warn-ink"),
        "warn-bg": v("warn-bg"),
        "warn-bg-2": v("warn-bg-2"),
        "warn-line": v("warn-line"),
        ai: v("ai"),
        "ai-ink": v("ai-ink"),
        "ai-bg": v("ai-bg"),
        "ai-line": v("ai-line"),
        "ai-line-2": v("ai-line-2"),
        bench: v("bench"),
      },
      fontFamily: {
        sans: [v("font-sans")],
        mono: [v("font-mono")],
      },
      borderRadius: {
        card: v("r-card"),
        block: v("r-block"),
        btn: v("r-btn"),
        pill: v("r-pill"),
      },
      screens: {
        md: "768px",
      },
    },
  },
  plugins: [],
};

export default config;

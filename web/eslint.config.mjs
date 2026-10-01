import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

// next/core-web-vitals + jsx-a11y 권장 규칙 전부 (docs/10 §5 접근성)
const config = [
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts", ".lighthouseci/**"] },
  ...compat.extends("next/core-web-vitals", "next/typescript", "plugin:jsx-a11y/recommended"),
  {
    rules: {
      "no-console": "error",
    },
  },
  {
    files: ["scripts/**/*.mjs", "lighthouse/**/*.cjs"],
    rules: { "no-console": "off", "@typescript-eslint/no-require-imports": "off" },
  },
];

export default config;

import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: { extend: { colors: { ink: "#070b12", panel: "#101722", line: "#263244", muted: "#8492a6" } } },
  plugins: [],
} satisfies Config;

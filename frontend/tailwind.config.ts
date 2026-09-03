import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        sidebar: "var(--sidebar)",
        header: "var(--header)",
        surface: { DEFAULT: "var(--surface-1)", alt: "var(--surface-2)", hover: "var(--surface-hover)" },
        line: { DEFAULT: "var(--border)", subtle: "var(--border-subtle)", row: "var(--row-border)" },
        foreground: { DEFAULT: "var(--text-primary)", secondary: "var(--text-secondary)", muted: "var(--text-muted)" },
        primary: { DEFAULT: "var(--primary)", hover: "var(--primary-hover)", soft: "var(--primary-soft)", contrast: "var(--primary-contrast)" },
        success: { DEFAULT: "var(--success)", soft: "var(--success-soft)" },
        warning: { DEFAULT: "var(--warning)", soft: "var(--warning-soft)" },
        danger: { DEFAULT: "var(--danger)", soft: "var(--danger-soft)" },
        ink: "var(--background)",
        panel: "var(--surface-1)",
        muted: "var(--text-muted)",
      },
      boxShadow: { panel: "var(--panel-shadow)" },
    },
  },
  plugins: [],
} satisfies Config;

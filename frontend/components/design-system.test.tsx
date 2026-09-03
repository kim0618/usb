import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const source = (path: string) => readFileSync(path, "utf8");
const codeFiles = (directory: string): string[] => readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
  const path = join(directory, entry.name);
  return entry.isDirectory() ? codeFiles(path) : /\.(?:css|tsx?)$/.test(entry.name) && !entry.name.endsWith(".test.tsx") ? [path] : [];
});

describe("Stage 9.16 semantic design system", () => {
  it("defines true-black dark and off-white light themes", () => {
    const css = source("app/globals.css");
    expect(css).toContain('--background: #000000');
    expect(css).toContain('[data-theme="light"]');
    expect(css).toContain('--background: #f7f8fa');
    expect(css).toContain('--surface-1: #ffffff');
  });

  it("bootstraps dark by default and restores persisted preferences before hydration", () => {
    const layout = source("app/layout.tsx");
    expect(layout).toContain('data-theme="dark"');
    expect(layout).toContain('localStorage.getItem("usb-theme")');
    expect(layout).toContain("suppressHydrationWarning");
  });

  it("uses primary tokens for selected navigation and tabs", () => {
    expect(source("components/app-shell.tsx")).toContain('bg-primary-soft font-semibold text-primary');
    expect(source("components/section-tabs.tsx")).toContain('border-primary bg-primary-soft text-primary');
  });

  it("maps success, warning, and danger through semantic tones", () => {
    const ui = source("components/ui.tsx");
    expect(ui).toContain("tone-${tone ?? inferredTone}");
    const css = source("app/globals.css");
    ["success", "warning", "danger", "info", "indigo", "neutral"].forEach(tone => expect(css).toContain(`.tone-${tone}`));
  });

  it("defines reusable semantic action buttons and themed metric accents", () => {
    const css = source("app/globals.css");
    expect(css).toContain(".btn-action");
    expect(css).toContain(".btn-action-compact");
    expect(css).toContain(".btn-success-soft");
    expect(css).toContain(".btn-danger-soft");
    ["primary", "blue", "indigo", "gold", "green", "bluegreen"].forEach(tone => expect(css).toContain(`.metric-card-${tone}`));
  });

  it("uses semantic action hierarchy across analysis screens", () => {
    const candidates = source("app/candidates/page.tsx");
    const research = source("app/research/page.tsx");
    const decisions = source("components/research-decision.tsx");
    expect(candidates).toContain('className="btn-action-secondary"');
    expect(candidates).toContain('className="btn-action-primary"');
    expect(research).toContain('className="btn-action-primary"');
    expect(research).toContain('className="btn-action-secondary-compact whitespace-nowrap"');
    expect(decisions).toContain('className="btn-success-soft"');
    expect(decisions).toContain('className="btn-danger-soft"');
  });

  it("makes total assets the strongest card while preserving individual accents and decorative icons", () => {
    const css = source("app/globals.css");
    const trading = source("app/trading/page.tsx");
    expect(trading).toContain('accent="primary"');
    ["indigo", "gold", "green", "bluegreen"].forEach(tone => expect(trading).toContain(`accent="${tone}"`));
    expect(css).toContain(".metric-card-primary");
    expect(css).toContain(".metric-card-primary .metric-card-icon");
    expect(source("components/ui.tsx")).toContain('aria-hidden="true"');
  });

  it("defines two blue action levels with shared geometry and clear keyboard focus", () => {
    const css = source("app/globals.css");
    expect(css).toContain(".btn-action-primary");
    expect(css).toContain(".btn-action-secondary");
    expect(css).toContain(".btn-action-secondary-compact");
    expect(css).toContain("focus-visible:ring-2");
    expect(css).toMatch(/\.btn-action-primary \{ @apply inline-flex h-9/);
    expect(css).toMatch(/\.btn-action-secondary \{ @apply inline-flex h-9/);
  });

  it("applies workflow action hierarchy without changing semantic decisions or segments", () => {
    const trading = source("app/trading/page.tsx");
    const candidates = source("app/candidates/page.tsx");
    const research = source("app/research/page.tsx");
    expect(trading).toContain('className="btn-action-secondary-compact">분석 보기');
    expect(trading).toContain("btn-compact-active");
    expect(candidates).toContain('className="btn-action-primary" disabled={busy}');
    expect(candidates).toContain('className="btn-action-secondary" disabled={busy}');
    expect(candidates).toContain('className="btn-action-secondary">GPT 분석 열기');
    expect(candidates).not.toContain("GPT 분석 열기 →");
    expect(research).toContain('className="btn-action-primary"');
    expect(research).toContain('className="btn-action-secondary-compact whitespace-nowrap"');
  });

  it("contains no legacy cyan or teal application classes", () => {
    const application = ["app", "components"].flatMap(codeFiles).map(source).join("\n");
    expect(application).not.toMatch(/(?:cyan|teal)-/);
  });
});

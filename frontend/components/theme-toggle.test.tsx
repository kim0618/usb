import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { THEME_STORAGE_KEY, ThemeToggle } from "./theme-toggle";

describe("Stage 9.16 theme switching", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.dataset.theme = "dark";
  });
  afterEach(cleanup);

  it("defaults to dark when there is no stored preference", async () => {
    render(<ThemeToggle/>);
    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", "dark"));
    expect(screen.getByRole("button", { name: "라이트 모드로 전환" })).toBeInTheDocument();
  });

  it.each(["dark", "light"] as const)("restores the stored %s theme", async theme => {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
    render(<ThemeToggle/>);
    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", theme));
  });

  it("toggles dark to light and persists the selection", async () => {
    render(<ThemeToggle/>);
    fireEvent.click(screen.getByRole("button", { name: "라이트 모드로 전환" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(screen.getByRole("button", { name: "다크 모드로 전환" })).toBeInTheDocument();
  });

  it("toggles light to dark and persists the selection", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    render(<ThemeToggle/>);
    const toggle = await screen.findByRole("button", { name: "다크 모드로 전환" });
    fireEvent.click(toggle);
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });
});

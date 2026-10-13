import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PnlHeadline, PositionCardShell } from "./position-card";
import { LivePositionCard } from "./crypto-live-terminal";
import { MobilePositionCard } from "./crypto-terminal-layout";
import type { CryptoState } from "../lib/crypto-paper";

vi.mock("@/lib/crypto-paper", async importOriginal => ({ ...(await importOriginal<object>()) }));
afterEach(cleanup);

const section = (container: HTMLElement) => [...container.querySelectorAll("section > div, section > dl, section > button")]
  .map(el => el.tagName);

describe("one position card for PAPER and LIVE", () => {
  it("prints the source as a badge and keeps the same order of sections", () => {
    const common = { testId: "c", side: "LONG", leverage: "20x", qty: "0.108 BTC", held: "3시간 22분",
      headline: <PnlHeadline pnl={{ krw: "-172,461원", usdt: "-128.6064 USDT", tone: "-128.6" }}
        net={{ shown: true, krw: "-180,320원", usdt: "-134.4700 USDT", tone: "-134.47", note: "수수료·펀딩·체결가 포함" }}/>,
      facts: [["진입가", "1"], ["Mark", "2"], ["수량", "3"], ["청산가", "4"], ["사용 증거금", "5"], ["마진 모드", "교차"]] as never };
    const paper = render(<PositionCardShell source="PAPER" {...common}/>);
    const paperShape = section(paper.container);
    const paperText = paper.container.textContent!;
    paper.unmount();
    const live = render(<PositionCardShell source="LIVE" {...common}/>);
    expect(section(live.container)).toEqual(paperShape);
    expect(live.container.textContent!.replace("LIVE", "PAPER")).toBe(paperText);
    expect(screen.getByTestId("position-source")).toHaveTextContent("LIVE");
    expect(live.container.textContent).toContain("LONG20x0.108 BTC · 3시간 22분 보유");
  });

  it("puts KRW first and USDT under it, and tones gains green and losses red", () => {
    const { container } = render(<PnlHeadline
      pnl={{ krw: "+125원", usdt: "+0.0930 USDT", tone: "0.093" }}
      net={{ shown: true, krw: "-1,081원", usdt: "-0.8043 USDT", tone: "-0.8" }}/>);
    const text = container.textContent!;
    expect(text.indexOf("+125원")).toBeLessThan(text.indexOf("+0.0930 USDT"));
    expect(container.querySelector(".text-success")!.textContent).toContain("+125원");
    expect(container.querySelector(".text-danger")!.textContent).toContain("-1,081원");
  });

  it("shows a dash and a short muted reason when the close cannot be priced", () => {
    const { container } = render(<PnlHeadline pnl={{ krw: "+1원", usdt: "+0.0007 USDT", tone: "1" }}
      net={{ shown: false, reason: "실시간 호가 확인 불가", testId: "r" }}/>);
    expect(container.textContent).not.toContain("미리보기 불가");
    const reason = screen.getByTestId("r");
    expect(reason).toHaveTextContent("실시간 호가 확인 불가");
    expect(reason.className).toContain("text-muted");
    expect(reason.className).not.toContain("text-warning");
  });

  it("is the only position card both screens render", () => {
    for (const path of ["components/crypto-terminal-layout.tsx", "components/crypto-live-terminal.tsx"]) {
      expect(readFileSync(path, "utf8"), path).toContain("PositionCardShell");
    }
    void MobilePositionCard; void LivePositionCard; void ({} as CryptoState);
  });
});

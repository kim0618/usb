import { describe, expect, it } from "vitest";
import { marketSessionSchedule } from "./market-session";

describe("US market session KST display", () => {
  it("converts daylight-saving sessions to KST", () => {
    expect(marketSessionSchedule("2026-09-08").map(item => item.kstRange)).toEqual([
      "17:00 ~ 22:30", "22:30 ~ 05:00", "05:00 ~ 09:00",
    ]);
  });

  it("converts standard-time sessions to KST", () => {
    expect(marketSessionSchedule("2026-12-08").map(item => item.kstRange)).toEqual([
      "18:00 ~ 23:30", "23:30 ~ 06:00", "06:00 ~ 10:00",
    ]);
  });

  it("uses authoritative open and early-close instants when provided", () => {
    const schedule = marketSessionSchedule("2026-11-27", "2026-11-27T09:30:00-05:00", "2026-11-27T13:00:00-05:00");
    expect(schedule.map(item => item.kstRange)).toEqual([
      "18:00 ~ 23:30", "23:30 ~ 03:00", "03:00 ~ 10:00",
    ]);
  });
});

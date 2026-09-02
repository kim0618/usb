import { describe, expect, it } from "vitest";
import { decimal, formatDecimalString } from "./format";

describe("financial display formatting", () => {
  it("preserves string input while formatting for display", () => { expect(formatDecimalString("10250.5500")).toBe("10,250.55"); expect(decimal("1.4238", "R")).toBe("1.4238R"); });
  it("does not display null literals", () => { expect(decimal(null)).toBe("—"); });
});

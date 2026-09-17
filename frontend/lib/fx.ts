/** The one USD/KRW rate the frontend converts with.
 *
 *  It is FIXED, not a quote. The value is the one Strategy A's screens have always used:
 *  the paper account opened with USD 7,428.92 (backend `PAPER_INITIAL_CASH`) standing for
 *  KRW 10,000,000, so the rate is that ratio rather than a number typed in here. No FX API
 *  is called anywhere; when a trusted rate arrives, this object is the only thing to change.
 *
 *  The header label, every KRW secondary line and every USD figure derived from a KRW mock
 *  read this object, so no two places on screen can use different rates.
 */

export type FxMode = "FIXED";

export interface FxConfig {
  usdKrw: number;
  mode: FxMode;
}

const PAPER_INITIAL_CASH_USD = 7_428.92;
const PAPER_INITIAL_CASH_KRW = 10_000_000;

export const FX_CONFIG: FxConfig = {
  usdKrw: PAPER_INITIAL_CASH_KRW / PAPER_INITIAL_CASH_USD,
  mode: "FIXED",
};

export const FX_MODE_LABELS: Readonly<Record<FxMode, string>> = { FIXED: "고정" };

/** Half away from zero, so -x always displays as the mirror of +x. `+ 0` drops a -0. */
const roundTo = (value: number, digits: number) => {
  const factor = 10 ** digits;
  return Math.sign(value) * Math.round(Math.abs(value) * factor) / factor + 0;
};

/** USD to whole won. */
export const usdToKrw = (usd: number, fx: FxConfig = FX_CONFIG): number => roundTo(usd * fx.usdKrw, 0);

/** KRW to cents. */
export const krwToUsd = (krw: number, fx: FxConfig = FX_CONFIG): number => roundTo(krw / fx.usdKrw, 2);

/** The rate as the header prints it, e.g. "1,346.09". */
export const formatFxRate = (fx: FxConfig = FX_CONFIG): string =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(fx.usdKrw);

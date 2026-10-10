/** Which instrument the manual terminal is looking at, and where that choice lives.
 *
 *  One module so there is exactly one answer. The tab strip, the LIVE hook, the PAPER hook, the
 *  chart and every fetch read the selection from here; none of them keeps a second copy. The
 *  failure this prevents is not cosmetic: two components disagreeing about the selected symbol
 *  is a screen that labels one instrument and prices another.
 *
 *  The server is still the authority on what may be traded. This file's list is what the tabs
 *  render before the first `/status` answers, and `/status` returns `symbols` which replaces it.
 *  A symbol the server does not list is never selectable.
 */

/** Ordered, and the first entry is the default. Mirrors `app/crypto/symbols.py`. */
export const CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;

export type CryptoSymbol = (typeof CRYPTO_SYMBOLS)[number];

export const DEFAULT_SYMBOL: CryptoSymbol = CRYPTO_SYMBOLS[0];

/** The short label on a tab. Not the base asset of a *quantity*, which comes from Binance's
 *  own `baseAsset` on the account snapshot - this is only the word on the button. */
export const SYMBOL_LABELS: Record<CryptoSymbol, string> = {
  BTCUSDT: "BTC", ETHUSDT: "ETH", SOLUSDT: "SOL",
};

export function isCryptoSymbol(value: unknown): value is CryptoSymbol {
  return typeof value === "string" && (CRYPTO_SYMBOLS as readonly string[]).includes(value);
}

export function symbolLabel(symbol: string): string {
  return isCryptoSymbol(symbol) ? SYMBOL_LABELS[symbol] : symbol.replace(/USDT$/, "");
}

/** The coin a quantity is denominated in, for a label only.
 *
 *  Used before an account snapshot exists. Once one does, `account.base_asset` is Binance's own
 *  answer and is the one to render; this is the fallback, not the authority. */
export function baseAsset(symbol: string): string {
  return symbol.replace(/USDT$/, "");
}

const STORAGE_KEY = "usb.crypto.symbol";

/** Read the remembered selection, or the default.
 *
 *  `localStorage` is wrapped because it throws in a private window and under blocked site data,
 *  and a terminal that fails to render because it could not remember a tab is worse than one
 *  that opens on BTC. An unrecognised stored value is discarded rather than trusted: the stored
 *  string is attacker-adjacent only in theory, but it is also simply stale after the supported
 *  set changes, and a tab for a symbol the server will refuse is a dead screen.
 */
/** A USDT perpetual's name: letters (Binance names some in Chinese) and digits, then USDT. */
export const SYMBOL_SHAPE = /^[\p{L}\p{N}]{1,36}USDT$/u;

/** The remembered selection, if it is still selectable.
 *
 *  `permitted` is the list the screen may select from. `null` means that list is not known yet
 *  (the HOT slot has not answered): the remembered symbol is then accepted on its shape alone,
 *  provisionally, so a reload on the HOT tab comes back to the HOT tab instead of flashing BTC.
 *  `useSelectedSymbol` moves the selection to BTC as soon as the real list says otherwise.
 */
export function readStoredSymbol(permitted: readonly string[] | null = CRYPTO_SYMBOLS): string {
  const allowed = (symbol: string) => permitted === null ? SYMBOL_SHAPE.test(symbol)
    : permitted.includes(symbol);
  try {
    if (typeof window === "undefined") return DEFAULT_SYMBOL;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && allowed(stored)) return stored;
  } catch {
    // Private window, blocked site data, or a browser that throws on access. Fall through.
  }
  if (permitted === null || allowed(DEFAULT_SYMBOL)) return DEFAULT_SYMBOL;
  return permitted[0] ?? DEFAULT_SYMBOL;
}

/** Remember the selection. Never throws; a failure to persist is not a failure to switch. */
export function storeSymbol(symbol: string): void {
  try {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(STORAGE_KEY, symbol);
  } catch {
    // See `readStoredSymbol`. The selection still applies for this page's lifetime.
  }
}

/** Append `symbol` to a query string, leaving an existing one alone.
 *
 *  Centralised so no call site can forget it. A request that omits the symbol gets the server's
 *  default, which is BTCUSDT - that is the exact bug this helper exists to make impossible,
 *  because an omitted parameter does not look like an error anywhere.
 */
export function withSymbol(path: string, symbol: string): string {
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}symbol=${encodeURIComponent(symbol)}`;
}

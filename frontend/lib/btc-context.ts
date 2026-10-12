/** BTC Manual Trading Market Context UI V2: what the BTC chart and the line under it show.
 *
 *  The backend (`app.crypto.btc_context_ui_v1`, its own process) has already reduced every source
 *  to display figures and a single `live` flag per section, and publishes `null` wherever a value
 *  is not current. This module only fetches that payload with a GET and turns it into chart lines
 *  and a few words: no threshold, no state vocabulary and no second reading of the numbers lives
 *  here.
 *
 *  Prices go on the chart (nearest resistance and support, nearest ask and bid wall). What is not
 *  a price - the 60 s trade flow, open interest and funding - is one compact line under it.
 */
import { useCallback, useSyncExternalStore } from "react";
import type { ChartOverlay } from "@/lib/crypto-paper";

/** Where the API is. Empty in a build that was not pointed at one, in which case the terminal
 *  draws no context at all and asks for nothing (see `crypto-terminal-context-slot`). */
export const BTC_CONTEXT_BASE = (process.env.NEXT_PUBLIC_BTC_CONTEXT_BASE ?? "").trim().replace(/\/$/, "");

export const BTC_CONTEXT_SYMBOL = "BTCUSDT";
/** How often the screen asks. The backend reads the collector's file on each request and serves the
 *  venue figures from its own cache, so this sets screen freshness only. */
export const BTC_CONTEXT_POLL_MS = 5_000;
/** A request that takes longer than this is abandoned; the next poll tries again. */
export const BTC_CONTEXT_TIMEOUT_MS = 4_000;
/** Past this since the last good answer, nothing is drawn rather than an old reading. */
export const BTC_CONTEXT_HOLD_MS = 15_000;

export const MISSING = "--";

export type BtcContextPayload = {
  symbol: string;
  server_time_ms: number;
  structure: {
    live: boolean; resistance_pct: string | null; support_pct: string | null;
    resistance_price?: string | null; support_price?: string | null;
  };
  liquidity: {
    live: boolean; ask: string | null; bid: string | null;
    ask_wall_price?: string | null; bid_wall_price?: string | null;
  };
  flow: { live: boolean; window: string; buy_pct: number | null; sell_pct: number | null };
  derivatives: {
    live: boolean; oi_direction: "UP" | "DOWN" | "FLAT" | null; oi_change_pct: string | null;
    oi_window: string; oi_value_usdt: string | null; funding_pct: string | null; basis_pct: string | null;
  };
  /** V3. The collector's published walls per side, nearest first. Absent from a V2 backend. */
  walls?: BtcWalls;
  /** V3. The recorder's entry snapshot of the open episode. Absent from a V2 backend. */
  entry?: BtcEntry;
};

export type BtcWall = {
  /** 1 = nearest to the price on its side. */
  rank: number; price: string; notional_usdt: string | null; multiple: string | null;
  /** How long the collector has seen this wall (carried across its own refreshes), ms. */
  age_ms: number | null; own_age_ms: number | null; continuity: string | null;
  first_seen_ms: number | null;
  /** 1 = largest published notional on its side. A sort, not a grade. */
  size_rank: number;
};
export type BtcWallEnd = {
  side: "ASK" | "BID"; price: string; notional_usdt: string | null; observed_at_ms: number | null;
  /** Whether the price path reached the wall before it went; null when the collector cannot say. */
  price_reached: boolean | null;
};
export type BtcWalls = {
  live: boolean; mid: string | null; ask: BtcWall[]; bid: BtcWall[]; recent_ends: BtcWallEnd[];
  /** True for a side the collector read and found no qualifying wall on. Absent or false for a
   *  side it could not read, so the screen says "none" only when that is what the collector said. */
  empty?: { ask: boolean; bid: boolean };
};
/** Where an entry row came from: the recorder's own output, or a hand-built preview store that the
 *  API serves only when started for a preview. */
export type EntrySource = "RECORDER" | "FIXTURE";
export type BtcEntry = { available: false } | {
  available: true; source?: EntrySource; episode_id: string | null; direction_sign: 1 | -1 | null;
  entry_ms: number | null; entry_price: string | null;
  market_recorded: boolean; collector_recorded: boolean;
  flow: { window: string; buy_pct: number | null; sell_pct: number | null };
  oi_value_usdt: string | null; oi_change_pct: string | null; funding_pct: string | null;
  basis_pct: string | null; ask_wall_price: string | null; bid_wall_price: string | null;
  mid: string | null;
};

/** One GET. Throws on any transport, status or shape failure; the caller draws nothing. */
export async function fetchBtcContext(base: string = BTC_CONTEXT_BASE,
  signal?: AbortSignal): Promise<BtcContextPayload> {
  const response = await fetch(`${base}/snapshot`, { method: "GET", cache: "no-store", signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const body = await response.json() as BtcContextPayload;
  if (!body || body.symbol !== BTC_CONTEXT_SYMBOL || !body.structure || !body.liquidity
    || !body.flow || !body.derivatives) throw new Error("unexpected payload");
  return body;
}

// --------------------------------------------------------------------------- one poll loop

type Reading = { payload: BtcContextPayload; at: number } | null;
type Snapshot = { last: Reading; tick: number };
type Store = { snapshot: Snapshot; listeners: Set<() => void>; stop: (() => void) | null };

/** One loop per API base, however many parts of the screen read it (the chart lines and the line
 *  under the chart). It runs while at least one of them is mounted and enabled, and stops when the
 *  last one leaves - which is what makes the HOT tab cost zero requests. */
const stores = new Map<string, Store>();
const EMPTY: Snapshot = { last: null, tick: 0 };

function storeFor(base: string): Store {
  let store = stores.get(base);
  if (!store) {
    store = { snapshot: EMPTY, listeners: new Set(), stop: null };
    stores.set(base, store);
  }
  return store;
}

function startLoop(base: string, store: Store) {
  let stopped = false;
  let inFlight: AbortController | null = null;
  const publish = (last: Reading) => {
    store.snapshot = { last, tick: Date.now() };
    for (const listener of store.listeners) listener();
  };
  const poll = async () => {
    // A tick is published even when nothing new arrived, so a reading ages out on time.
    publish(store.snapshot.last);
    if (inFlight || (typeof document !== "undefined" && document.hidden)) return;
    const controller = new AbortController();
    inFlight = controller;
    const timer = setTimeout(() => controller.abort(), BTC_CONTEXT_TIMEOUT_MS);
    try {
      const payload = await fetchBtcContext(base, controller.signal);
      if (!stopped) publish({ payload, at: Date.now() });
    } catch {
      // Nothing drawn, not an error banner: this must never cover the order screen.
    } finally {
      clearTimeout(timer);
      inFlight = null;
    }
  };
  void poll();
  const id = setInterval(() => { void poll(); }, BTC_CONTEXT_POLL_MS);
  store.stop = () => { stopped = true; clearInterval(id); inFlight?.abort(); };
}

function subscribe(base: string, listener: () => void): () => void {
  const store = storeFor(base);
  store.listeners.add(listener);
  if (!store.stop) startLoop(base, store);
  return () => {
    store.listeners.delete(listener);
    if (store.listeners.size === 0 && store.stop) {
      store.stop();
      store.stop = null;
      // Leaving BTC drops the reading, so coming back never draws an old one first.
      store.snapshot = EMPTY;
    }
  };
}

const noop = () => () => {};

/** The current reading, or `null` when disabled, never answered, or older than the hold window.
 *  `enabled` false asks nothing at all. Only GETs, one in flight at a time per base. */
export function useBtcContext(base: string = BTC_CONTEXT_BASE, enabled = true): BtcContextPayload | null {
  const on = enabled && base.length > 0;
  // Stable per (base, on): a new function here would unsubscribe and resubscribe on every render,
  // which restarts the loop and fires a request each time.
  const subscribeHere = useCallback(
    (listener: () => void) => (on ? subscribe(base, listener) : noop()), [base, on]);
  const read = useCallback(() => (on ? storeFor(base).snapshot : EMPTY), [base, on]);
  const snapshot = useSyncExternalStore(subscribeHere, read, () => EMPTY);
  const last = snapshot.last;
  if (!on || !last || snapshot.tick - last.at > BTC_CONTEXT_HOLD_MS) return null;
  return last.payload;
}

/** Test hook: forget every loop's last reading, the frozen entries and the wall selection. */
export function resetBtcContextStores() {
  frozenEntries.clear();
  setWallSelection("NEAR");
  setStructureDisplay("HIDDEN");
  for (const store of stores.values()) {
    store.stop?.();
    store.stop = null;
    store.listeners.clear();
    store.snapshot = EMPTY;
  }
  stores.clear();
}

// --------------------------------------------------------------------------- chart lines

function positive(value: string | null | undefined): number | null {
  if (value == null) return null;
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** BTCUSDT prices as the chart's axis prints them, with thousands separators: "83,113.9". */
export function priceText(value: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

// --------------------------------------------------------------------------- V3: walls and paths

/** Which three walls per side the screen shows. A preview choice, not a decision: "NEAR" is the
 *  collector's own order (the next walls the price meets), "SIZE" the three largest published
 *  notionals on that side, still listed in the order the price would meet them. */
export type WallSelection = "NEAR" | "SIZE";
export const WALLS_PER_SIDE = 3;

let wallSelection: WallSelection = "NEAR";
const selectionListeners = new Set<() => void>();
export function setWallSelection(next: WallSelection) {
  if (next === wallSelection) return;
  wallSelection = next;
  for (const listener of selectionListeners) listener();
}
/** Shared by the panel (which switches it) and the chart lines (which follow it). */
export function useWallSelection(): WallSelection {
  return useSyncExternalStore(
    listener => { selectionListeners.add(listener); return () => { selectionListeners.delete(listener); }; },
    () => wallSelection, () => "NEAR" as WallSelection);
}

/** How the V2 resistance and support lines are drawn: HIDDEN (the default: Manual Context R0
 *  found no edge in them) or ANNOTATION (a faint dotted reference line with its name only, an
 *  option kept on the panel). Neither gives them the weight of a wall. */
export type StructureDisplay = "HIDDEN" | "ANNOTATION";
let structureDisplay: StructureDisplay = "HIDDEN";
const structureListeners = new Set<() => void>();
export function setStructureDisplay(next: StructureDisplay) {
  if (next === structureDisplay) return;
  structureDisplay = next;
  for (const listener of structureListeners) listener();
}
export function useStructureDisplay(): StructureDisplay {
  return useSyncExternalStore(
    listener => { structureListeners.add(listener); return () => { structureListeners.delete(listener); }; },
    () => structureDisplay, () => "HIDDEN" as StructureDisplay);
}

export function pickWalls(walls: BtcWall[], selection: WallSelection, n = WALLS_PER_SIDE): BtcWall[] {
  const chosen = selection === "NEAR" ? walls.slice(0, n)
    : [...walls].sort((a, b) => a.size_rank - b.size_rank).slice(0, n);
  return [...chosen].sort((a, b) => a.rank - b.rank);
}

/** How many wall lines per side the chart draws on each timeframe. PREVIEW CANDIDATE for the
 *  user's review, not a settled rule: walls are the live book, so their prices are the same on
 *  every timeframe, but on a long timeframe they sit within a few pixels of the price. The panel
 *  under the chart always lists all three per side whatever the timeframe. */
export const WALL_LINES_BY_TIMEFRAME: Record<string, number> = {
  "15s": 3, "1m": 3, "10m": 2, "1h": 1, "4h": 0, "1d": 0,
};
export function wallLinesFor(timeframe: string | undefined): number {
  return timeframe == null ? WALLS_PER_SIDE : WALL_LINES_BY_TIMEFRAME[timeframe] ?? WALLS_PER_SIDE;
}

/** The operator's own position, as the terminal already holds it. `sign` +1 long, -1 short. */
export type ContextPosition = { sign: 1 | -1; entry: number; qty?: string | null };
/** What the panel under the chart is handed by the terminal: the account's position and mark. */
export type ContextView = { position: ContextPosition | null; mark: number | null };

/** Signed percent from `from` to `to`: (to - from) / from. */
export function pctFrom(from: number, to: number): number {
  return (to - from) / from * 100;
}
/** Signed distance from the entry to a price, in the price's own direction: + is above the entry,
 *  - is below, for a LONG and a SHORT alike. The sign is never turned towards the position's gain:
 *  "진입 대비 +0.34%" means the level is 0.34% above the entry, nothing more. */
export function entryDistancePct(position: ContextPosition, price: number): number {
  return pctFrom(position.entry, price);
}

export function pctText(value: number | null, places = 2): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  const fixed = Math.abs(value).toFixed(places);
  if (Number(fixed) === 0) return `${fixed}%`;
  return `${value > 0 ? "+" : "-"}${fixed}%`;
}

export type PathRow = {
  id: string; side: "ASK" | "BID"; order: number; price: number;
  /** The figure the row leads with: from the entry with a position, from the price without. */
  primaryPct: number;
  /** From the current price. With no position this equals `primaryPct`. */
  currentPct: number;
  /** From the entry, in the price's direction (`entryDistancePct`); null without a position. */
  entryPct: number | null;
  /** Room between this row and the one before it on its path (the price, for the first). */
  gapPct: number;
  ageMs: number | null; notional: string | null; multiple: string | null; sizeRank: number;
  continuity: string | null;
};
export type Paths = {
  mode: "MARKET" | "POSITION";
  current: number | null;
  /** Above the price: the ask walls, nearest first. */
  up: PathRow[];
  /** Below the price: the bid walls, nearest first. */
  down: PathRow[];
  /** How many walls the collector published on each side (the rows above are a choice of three). */
  upCount: number; downCount: number;
  /** True only when the collector read the side and found no qualifying wall. */
  upEmpty: boolean; downEmpty: boolean;
};

function pathRows(side: "ASK" | "BID", walls: BtcWall[], current: number,
  position: ContextPosition | null): PathRow[] {
  let previous = current;
  const rows: PathRow[] = [];
  walls.forEach((wall, i) => {
    const price = Number(wall.price);
    if (!Number.isFinite(price) || price <= 0) return;
    const currentPct = pctFrom(current, price);
    const entryPct = position ? entryDistancePct(position, price) : null;
    rows.push({
      id: `${side === "ASK" ? "ask" : "bid"}-${i + 1}`, side, order: i + 1, price,
      primaryPct: entryPct ?? currentPct, currentPct, entryPct,
      gapPct: Math.abs(pctFrom(previous, price)),
      ageMs: wall.age_ms, notional: wall.notional_usdt, multiple: wall.multiple,
      sizeRank: wall.size_rank, continuity: wall.continuity,
    });
    previous = price;
  });
  return rows;
}

/** The two paths from the current price: the next walls up and the next walls down, each with the
 *  room between consecutive walls.
 *
 *  "Current" is the collector's mid: the middle of the same Binance book the walls are read from,
 *  so an ask wall is always above it and a bid wall below. The terminal's mark is used only when
 *  there is no mid. (The PAPER screen's mark is Bybit's; measured against it, a Binance ask wall
 *  could read as below the price - seen in the V3 preview at 83,800 vs a Bybit mark of 83,825.1.) */
export function wallPaths(payload: BtcContextPayload | null, opts: {
  mark?: number | null; position?: ContextPosition | null; selection?: WallSelection;
} = {}): Paths {
  const position = opts.position ?? null;
  const mode = position ? "POSITION" : "MARKET";
  const walls = payload?.walls;
  const mid = positive(walls?.mid);
  const current = mid ?? (opts.mark != null && opts.mark > 0 ? opts.mark : null);
  const counts = { upCount: walls?.ask.length ?? 0, downCount: walls?.bid.length ?? 0,
    upEmpty: walls?.empty?.ask === true, downEmpty: walls?.empty?.bid === true };
  if (!walls || current == null) return { mode, current: current ?? null, up: [], down: [], ...counts };
  const selection = opts.selection ?? "NEAR";
  return {
    mode, current,
    up: pathRows("ASK", pickWalls(walls.ask, selection), current, position),
    down: pathRows("BID", pickWalls(walls.bid, selection), current, position),
    ...counts,
  };
}

/** "4m", "35s", "1h 12m": how long a wall has been seen. */
export function ageText(ms: number | null): string {
  if (ms == null || ms < 0) return MISSING;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// --------------------------------------------------------------------------- V3: entry vs current

/** The first entry context seen for each episode. The recorder's store only grows, so this should
 *  never differ from a later answer; holding the first one makes "as at entry" a property of the
 *  screen too, whatever a later answer says. */
const frozenEntries = new Map<string, BtcEntry & { available: true }>();

/** The entry context for the position on screen, or null when there is none to compare with:
 *  no position, no recorder answer, or an answer for a different position (sign mismatch). */
export function entryFor(payload: BtcContextPayload | null,
  position: ContextPosition | null): (BtcEntry & { available: true }) | null {
  if (!position) return null;
  const entry = payload?.entry;
  if (!entry || !entry.available || entry.direction_sign !== position.sign) return null;
  const key = entry.episode_id ?? `${entry.entry_ms}:${entry.direction_sign}`;
  const held = frozenEntries.get(key);
  if (held) return held;
  frozenEntries.set(key, entry);
  return entry;
}

function flowWords(buy: number | null | undefined, sell: number | null | undefined): string {
  if (buy == null || sell == null) return MISSING;
  if (buy > sell) return `매수우세 ${buy}%`;
  if (sell > buy) return `매도우세 ${sell}%`;
  return `중립 ${buy}%`;
}

export type Comparison = {
  flow: { entry: string; current: string };
  oi: { entry: string; current: string };
  funding: { entry: string; current: string };
  basis: { entry: string; current: string };
  walls: { ask: string; bid: string };
  /** When the entry was recorded; null without a recorder answer. */
  entryMs: number | null;
  /** Where the entry figures came from; null when there is no entry record at all. */
  source: EntrySource | null;
};

/** Entry against current for the same figures, side by side. No arrow word, no reading of the
 *  change: the screen states the two numbers. */
export function entryComparison(payload: BtcContextPayload | null,
  entry: (BtcEntry & { available: true }) | null): Comparison {
  const d = payload?.derivatives;
  return {
    flow: { entry: flowWords(entry?.flow.buy_pct, entry?.flow.sell_pct),
            current: flowReading(payload).text },
    oi: { entry: compactUsd(entry?.oi_value_usdt ?? null), current: compactUsd(d?.oi_value_usdt ?? null) },
    funding: { entry: signed(entry?.funding_pct ?? null), current: signed(d?.funding_pct ?? null) },
    basis: { entry: signed(entry?.basis_pct ?? null), current: signed(d?.basis_pct ?? null) },
    walls: {
      ask: positive(entry?.ask_wall_price) != null ? priceText(Number(entry?.ask_wall_price)) : MISSING,
      bid: positive(entry?.bid_wall_price) != null ? priceText(Number(entry?.bid_wall_price)) : MISSING,
    },
    entryMs: entry?.entry_ms ?? null,
    source: entry ? (entry.source === "FIXTURE" ? "FIXTURE" : "RECORDER") : null,
  };
}

/** The context lines for the BTC chart.
 *
 *  V3, when the backend publishes walls: nearest resistance and support, and up to three walls per
 *  side (by the panel's selection), thinned per timeframe by `WALL_LINES_BY_TIMEFRAME`.
 *  V2 backend (no `walls`): the collector's single nearest wall per side, as before.
 *  A side the backend did not publish (stale, missing, no wall) is simply not drawn.
 *
 *  `label` is the line's own title and is empty: the axis label carries the price and the chart's
 *  tag (`tag`) carries the name, so the name never competes with the price for the axis. */
export function contextChartLines(payload: BtcContextPayload | null, opts: {
  timeframe?: string; selection?: WallSelection; structure?: StructureDisplay;
} = {}): ChartOverlay[] {
  if (!payload || payload.symbol !== BTC_CONTEXT_SYMBOL) return [];
  const lines: ChartOverlay[] = [];
  const s = payload.structure, l = payload.liquidity;
  // V3: resistance and support are a reference annotation at most (name only on the tag, the
  // price on the axis), or not drawn. Never the full "저항 83,499.9 -0.37%" tag of V2.
  if ((opts.structure ?? "HIDDEN") === "ANNOTATION") {
    const resistance = positive(s.resistance_price);
    if (resistance != null) {
      lines.push({ id: "ctx-resistance", price: resistance, kind: "RESISTANCE", short: "저항", label: "", tag: "저항" });
    }
    const support = positive(s.support_price);
    if (support != null) {
      lines.push({ id: "ctx-support", price: support, kind: "SUPPORT", short: "지지", label: "", tag: "지지" });
    }
  }
  if (payload.walls) {
    const n = wallLinesFor(opts.timeframe);
    const selection = opts.selection ?? "NEAR";
    // Several walls a side: the tag is the name and order only ("매도1"), so a stack of six covers
    // little of the chart's left edge. The price is on the axis label and in the panel below.
    for (const [side, kind, name, tag] of [["ask", "ASK_WALL", "매도벽", "매도"], ["bid", "BID_WALL", "매수벽", "매수"]] as const) {
      pickWalls(payload.walls[side], selection).slice(0, n).forEach((wall, i) => {
        const price = positive(wall.price);
        if (price == null) return;
        lines.push({ id: `ctx-${side}-wall-${i + 1}`, price, kind, short: `${name}${i + 1}`, label: "",
          tag: `${tag}${i + 1}` });
      });
    }
    return lines;
  }
  const ask = positive(l.ask_wall_price);
  if (ask != null) {
    lines.push({ id: "ctx-ask-wall", price: ask, kind: "ASK_WALL", short: "매도벽", label: "",
      tag: `매도벽 ${priceText(ask)}` });
  }
  const bid = positive(l.bid_wall_price);
  if (bid != null) {
    lines.push({ id: "ctx-bid-wall", price: bid, kind: "BID_WALL", short: "매수벽", label: "",
      tag: `매수벽 ${priceText(bid)}` });
  }
  return lines;
}

// --------------------------------------------------------------------------- the line under it

/** A rate the backend already rounded, with its sign written out: "-0.0014" -> "-0.0014%". */
function signed(value: string | null): string {
  if (value == null) return MISSING;
  const n = Number(value);
  if (!Number.isFinite(n)) return MISSING;
  const text = value.replace(/^[+-]/, "");
  if (n > 0) return `+${text}%`;
  if (n < 0) return `-${text}%`;
  return `${text}%`;
}

/** USDT notional as a short figure: 7705535139 -> "7.71B". */
export function compactUsd(value: string | null): string {
  if (value == null) return MISSING;
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return MISSING;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return n.toFixed(0);
}

const ARROW: Record<string, string> = { UP: "↑", DOWN: "↓", FLAT: "→" };

export type FlowReading = {
  /** "매수우세 64%", "매도우세 73%", "중립 50%", or "--". */
  text: string;
  /** Buy share 0..100 for the bar, or null when there is no reading. */
  buyPct: number | null;
  side: "BUY" | "SELL" | "EVEN" | null;
};

/** The 60 s flow as the larger side and its share. The backend's two shares add to 100, so the
 *  larger one is the whole statement; only an exact 50/50 has no larger side. */
export function flowReading(payload: BtcContextPayload | null): FlowReading {
  const buy = payload?.flow.buy_pct, sell = payload?.flow.sell_pct;
  if (buy == null || sell == null) return { text: MISSING, buyPct: null, side: null };
  if (buy > sell) return { text: `매수우세 ${buy}%`, buyPct: buy, side: "BUY" };
  if (sell > buy) return { text: `매도우세 ${sell}%`, buyPct: buy, side: "SELL" };
  return { text: `중립 ${buy}%`, buyPct: buy, side: "EVEN" };
}

export type ContextLine = {
  flow: FlowReading;
  /** "OI ↑" - the direction only. */
  oi: string;
  /** "펀딩 -0.0014%". */
  funding: string;
  /** Behind the disclosure: the figures the line leaves out. */
  detail: { oiValue: string; oiChange: string; basis: string };
  live: { flow: boolean; derivatives: boolean };
};

export function contextLine(payload: BtcContextPayload | null): ContextLine {
  const d = payload?.derivatives;
  return {
    flow: flowReading(payload),
    oi: `OI ${d?.oi_direction ? ARROW[d.oi_direction] ?? MISSING : MISSING}`,
    funding: `펀딩 ${signed(d?.funding_pct ?? null)}`,
    detail: {
      oiValue: `OI ${compactUsd(d?.oi_value_usdt ?? null)}`,
      oiChange: `1시간 ${signed(d?.oi_change_pct ?? null)}`,
      basis: `베이시스 ${signed(d?.basis_pct ?? null)}`,
    },
    live: { flow: Boolean(payload?.flow.live), derivatives: Boolean(d?.live) },
  };
}

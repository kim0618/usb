"use client";

/** HALF CLOSE and FULL CLOSE, side by side, in the position area.
 *
 *  One component for both accounts. The caller decides what each press sends: a CLOSE of
 *  `half` on the held side (PAPER engine partial close, or LIVE reduceOnly), or the existing full
 *  close. When `half.qty` is null the HALF button is off and says why; it never becomes a full
 *  close. */
export function PositionCloseButtons({ half, unit, onHalf, onFull, busy, disabled, testIdPrefix,
  fullTestId }: {
  half: { qty: string | null; reason: string | null };
  unit: string;
  onHalf: (qty: string) => void;
  onFull: () => void;
  busy?: boolean;
  /** Both buttons off (no quote, nothing held). */
  disabled?: boolean;
  testIdPrefix: string;
  /** The FULL CLOSE button's id where an existing one is kept. */
  fullTestId?: string;
}) {
  return (
    <div data-testid={`${testIdPrefix}-close-buttons`}>
      <div className="grid grid-cols-2 gap-2">
        <button type="button" className="btn-muted h-11 w-full text-sm font-bold"
          data-testid={`${testIdPrefix}-half-close`} disabled={busy || disabled || half.qty == null}
          title={half.qty == null ? half.reason ?? undefined : `${half.qty} ${unit} 청산`}
          onClick={() => { if (half.qty) onHalf(half.qty); }}>
          HALF CLOSE{half.qty != null && <span className="ml-1 text-[11px] font-normal tabular-nums opacity-80">
            {Number(half.qty).toLocaleString("en-US", { maximumFractionDigits: 8 })}</span>}
        </button>
        <button type="button" className="btn-muted h-11 w-full text-sm font-bold"
          data-testid={fullTestId ?? `${testIdPrefix}-full-close`} disabled={busy || disabled} onClick={onFull}>
          FULL CLOSE
        </button>
      </div>
      {half.qty == null && half.reason && (
        <p className="mt-1 text-[11px] text-muted" data-testid={`${testIdPrefix}-half-close-blocked`}>
          HALF CLOSE 불가 · {half.reason}
        </p>
      )}
    </div>
  );
}

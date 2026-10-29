/**
 * Landed-cost preview: spread purchase-bill charges (freight, hamali...) over the
 * stock lines. A mirror of `buying.services.charges` on the server, which stays the
 * source of truth: every charge is split on its own, in whole paise, rounded DOWN
 * line by line with the remainder on the last line that has a weight. BigInt only,
 * so there is never float drift.
 */

export type DistributeBy = "Amount" | "Qty";

export interface LandedCharge {
  amount: string;
  distribute_by: DistributeBy | string;
}

export interface LandedLine {
  key: string;
  /** line amount (qty x rate) as a decimal string */
  amount: string;
  qty: string;
}

const DEC_RE = /^\d+(\.\d+)?$/;

/** Decimal string -> integer at `scale` fraction digits (extra digits are cut). */
function units(value: string | null | undefined, scale: number): bigint {
  const s = String(value ?? "").trim().replace(/^-/, "");
  if (!DEC_RE.test(s)) return 0n;
  const [i, f = ""] = s.split(".");
  return BigInt(i + f.padEnd(scale, "0").slice(0, scale));
}

function fmt(int: bigint, scale: number): string {
  const s = int.toString().padStart(scale + 1, "0");
  return `${s.slice(0, s.length - scale)}.${s.slice(s.length - scale)}`;
}

/** Shares (in paise) of `total` over `weights`; they always add up to `total`. */
export function splitPaise(total: bigint, weights: bigint[]): bigint[] {
  const sum = weights.reduce((a, b) => a + b, 0n);
  const out = weights.map(() => 0n);
  if (sum <= 0n) return out;
  let last = -1;
  weights.forEach((w, i) => {
    if (w > 0n) last = i;
  });
  let given = 0n;
  weights.forEach((w, i) => {
    if (i === last || w <= 0n) return;
    out[i] = (total * w) / sum; // floor: ROUND_DOWN
    given += out[i];
  });
  out[last] = total - given;
  return out;
}

/**
 * Landed share of every line, as `{key: "12.34"}`. Lines are the stock lines in
 * document order. A charge that cannot be spread (no weight) is skipped.
 */
export function allocateCharges(charges: LandedCharge[], lines: LandedLine[]): Record<string, string> {
  const totals = lines.map(() => 0n);
  for (const c of charges) {
    const amount = units(c.amount, 2);
    if (amount <= 0n) continue;
    const weights = lines.map((l) => (c.distribute_by === "Qty" ? units(l.qty, 3) : units(l.amount, 2)));
    splitPaise(amount, weights).forEach((share, i) => {
      totals[i] += share;
    });
  }
  const out: Record<string, string> = {};
  lines.forEach((l, i) => {
    out[l.key] = fmt(totals[i], 2);
  });
  return out;
}

/** `(amount + share) / qty` to 6 places (the valuation-rate precision), or null. */
export function landedRate(amount: string, share: string, qty: string): string | null {
  const q = units(qty, 3);
  if (q <= 0n) return null;
  // (amount[2dp] + share[2dp]) / qty[3dp] -> 6 dp: scale numerator by 10^(6+3-2)
  const num = (units(amount, 2) + units(share, 2)) * 10n ** 7n;
  const half = q / 2n;
  return fmt((num + half) / q, 6);
}

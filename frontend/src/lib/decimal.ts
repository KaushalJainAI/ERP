/**
 * Exact decimal-string helpers (BigInt based, no floats).
 *
 * Money/qty values travel as decimal strings. Anything the client computes
 * (line-amount previews, totals) goes through here so there is never float
 * drift. The server remains the source of truth for persisted totals; these
 * results are display-only estimates.
 */

const DEC_RE = /^-?\d+(\.\d+)?$/;

interface Scaled {
  int: bigint;
  scale: number;
}

function parse(value: string | number | null | undefined): Scaled | null {
  if (value === null || value === undefined) return null;
  const s = String(value).trim();
  if (!DEC_RE.test(s)) return null;
  const neg = s.startsWith("-");
  const body = neg ? s.slice(1) : s;
  const [i, f = ""] = body.split(".");
  const int = BigInt(i + f);
  return { int: neg ? -int : int, scale: f.length };
}

const pow10 = (n: number): bigint => 10n ** BigInt(n);

/** Divide by 10^drop rounding half-even (matches Python Decimal.quantize default). */
function roundScaled(int: bigint, drop: number): bigint {
  if (drop <= 0) return int * pow10(-drop);
  const div = pow10(drop);
  const neg = int < 0n;
  const abs = neg ? -int : int;
  let q = abs / div;
  const r = abs % div;
  const twice = r * 2n;
  if (twice > div || (twice === div && q % 2n === 1n)) q += 1n;
  return neg ? -q : q;
}

function format(int: bigint, scale: number): string {
  const neg = int < 0n;
  const abs = (neg ? -int : int).toString().padStart(scale + 1, "0");
  const whole = scale > 0 ? abs.slice(0, abs.length - scale) : abs;
  const frac = scale > 0 ? abs.slice(abs.length - scale) : "";
  return `${neg ? "-" : ""}${whole}${frac ? "." + frac : ""}`;
}

export function isDecimal(value: unknown): boolean {
  return typeof value === "string" || typeof value === "number" ? DEC_RE.test(String(value).trim()) : false;
}

/** Round a decimal string to `places` fractional digits (half-even). Null if not numeric. */
export function roundDec(value: string | number | null | undefined, places: number): string | null {
  const p = parse(value);
  if (!p) return null;
  return format(roundScaled(p.int, p.scale - places), places);
}

/** a * b rounded to `places` digits. Null when either operand is not numeric. */
export function mulDec(
  a: string | number | null | undefined,
  b: string | number | null | undefined,
  places = 2
): string | null {
  const x = parse(a);
  const y = parse(b);
  if (!x || !y) return null;
  const scale = x.scale + y.scale;
  return format(roundScaled(x.int * y.int, scale - places), places);
}

/** Sum of decimal strings (non-numeric entries ignored), rounded to `places`. */
export function sumDec(values: Array<string | number | null | undefined>, places = 2): string {
  const parsed = values.map(parse).filter((p): p is Scaled => p !== null);
  const scale = Math.max(places, ...parsed.map((p) => p.scale));
  let total = 0n;
  for (const p of parsed) total += p.int * pow10(scale - p.scale);
  return format(roundScaled(total, scale - places), places);
}

/** True when `value` is an in-progress/complete decimal with at most `places` fraction digits. */
export function isValidDecimalInput(value: string, places: number, allowNegative = false): boolean {
  if (value === "") return true;
  const re = new RegExp(`^${allowNegative ? "-?" : ""}\\d*(\\.\\d{0,${places}})?$`);
  return re.test(value);
}

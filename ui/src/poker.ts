/** Cards, hand classes and range strings — mirrors protocol/cards.py. */

export const RANKS = "AKQJT98765432";
export const SUITS = "sdch";

export const ALL_CARDS: string[] = [];
for (const rank of RANKS) for (const suit of SUITS) ALL_CARDS.push(rank + suit);

export function cardId(card: string): number {
  return 4 * RANKS.indexOf(card[0]!.toUpperCase()) + SUITS.indexOf(card[1]!.toLowerCase());
}

/** 169-class matrix coordinates: row = first rank index, col = second.
 *  row == col → pair; row < col → suited; row > col → offsuit. */
export function classLabel(row: number, col: number): string {
  if (row === col) return RANKS[row]! + RANKS[col]!;
  if (row < col) return RANKS[row]! + RANKS[col]! + "s";
  return RANKS[col]! + RANKS[row]! + "o";
}

export function classCoords(label: string): [number, number] {
  const hi = RANKS.indexOf(label[0]!);
  const lo = RANKS.indexOf(label[1]!);
  if (label.length === 2) return [hi, lo];
  return label[2] === "s" ? [hi, lo] : [lo, hi];
}

/** All 169 class labels in row-major order (AA first, as the API's
 *  hand_grid emits them). */
export const CLASSES: string[] = [];
for (let r = 0; r < 13; r++) for (let c = 0; c < 13; c++) CLASSES.push(classLabel(r, c));

const SUITED = (hi: number, lo: number) => classLabel(hi, lo);
const OFFSUIT = (hi: number, lo: number) => classLabel(lo, hi);

function expandToken(body: string): string[] {
  if (body === "*" || body === "random" || body === "any") return [...CLASSES];
  if (body.length === 4 && SUITS.includes(body[1]!.toLowerCase()) && SUITS.includes(body[3]!.toLowerCase())) {
    // specific combo "AsKd" -> its class
    const a = body.slice(0, 2), b = body.slice(2, 4);
    const ra = RANKS.indexOf(a[0]!.toUpperCase()), rb = RANKS.indexOf(b[0]!.toUpperCase());
    if (ra < 0 || rb < 0) throw new Error(`bad combo ${body}`);
    if (ra === rb) return [RANKS[ra]! + RANKS[rb]!];
    return a[1] === b[1] ? [SUITED(ra, rb)] : [OFFSUIT(ra, rb)];
  }
  const parseClass = (t: string): [number, number, string] => {
    if (t.length !== 2 && t.length !== 3) throw new Error(`cannot parse range token ${t}`);
    const f = RANKS.indexOf(t[0]!.toUpperCase()), s = RANKS.indexOf(t[1]!.toUpperCase());
    const kind = t.length === 3 ? t[2]!.toLowerCase() : "";
    if (f < 0 || s < 0 || !["", "s", "o"].includes(kind)) throw new Error(`cannot parse range token ${t}`);
    if (f === s && kind) throw new Error(`a pair cannot be suited or offsuit: ${t}`);
    return f <= s ? [f, s, kind] : [s, f, kind];
  };
  const emit = (hi: number, lo: number, kind: string): string[] => {
    if (hi === lo) return [RANKS[hi]! + RANKS[lo]!];
    if (kind === "s") return [SUITED(hi, lo)];
    if (kind === "o") return [OFFSUIT(hi, lo)];
    return [SUITED(hi, lo), OFFSUIT(hi, lo)];
  };
  if (body.includes("-")) {
    const [left, right] = body.split("-", 2);
    const [ha, la, ka] = parseClass(left!);
    const [hb, lb, kb] = parseClass(right!);
    if (ka !== kb) throw new Error(`range ${body} mixes suited and offsuit ends`);
    if (ha === la && hb === lb) {
      const [lo2, hi2] = [Math.min(ha, hb), Math.max(ha, hb)];
      const out: string[] = [];
      for (let r = lo2; r <= hi2; r++) out.push(RANKS[r]! + RANKS[r]!);
      return out;
    }
    if (ha !== hb || ha === la || hb === lb)
      throw new Error(`range ${body} must be pair-pair or share its high card`);
    const [lo2, hi2] = [Math.min(la, lb), Math.max(la, lb)];
    const out: string[] = [];
    for (let k = lo2; k <= hi2; k++) out.push(...emit(ha, k, ka));
    return out;
  }
  if (body.endsWith("+")) {
    const [hi, lo, kind] = parseClass(body.slice(0, -1));
    const out: string[] = [];
    if (hi === lo) for (let r = 0; r <= hi; r++) out.push(RANKS[r]! + RANKS[r]!);
    else for (let k = hi + 1; k <= lo; k++) out.push(...emit(hi, k, kind));
    return out;
  }
  const [hi, lo, kind] = parseClass(body);
  return emit(hi, lo, kind);
}

/** Parse a range string into per-class weights (0..1). Class-level: a
 *  combo token sets its whole class; the engine keeps per-combo weights,
 *  but the editor works on classes. */
export function parseRange(text: string): Map<string, number> {
  const weights = new Map<string, number>();
  const tokens = text.replace(/,/g, " ").split(/\s+/).filter(Boolean);
  if (!tokens.length) throw new Error("range is empty");
  for (const token of tokens) {
    const [body, wtext] = token.split(":", 2);
    let weight = 1.0;
    if (wtext !== undefined) {
      weight = Number(wtext);
      if (!Number.isFinite(weight) || weight < 0) throw new Error(`bad weight in range token ${token}`);
    }
    for (const cls of expandToken(body!)) weights.set(cls, weight);
  }
  for (const [k, v] of weights) if (v <= 0) weights.delete(k);
  if (!weights.size) throw new Error("range has no combos with positive weight");
  return weights;
}

/** Serialize per-class weights to a range string (`AKs`, `TT:0.5`, ...). */
export function serializeRange(weights: Map<string, number>): string {
  const parts: string[] = [];
  for (const cls of CLASSES) {
    const w = weights.get(cls);
    if (w === undefined || w <= 0) continue;
    parts.push(w === 1 ? cls : `${cls}:${w}`);
  }
  return parts.join(", ");
}

export const BET_SIZE_SETS: Record<string, number[]> = {
  third: [1 / 3],
  half: [0.5],
  pot: [1.0],
  standard: [0.5, 1.0],
  small_big: [1 / 3, 0.75],
  wide: [1 / 3, 2 / 3, 1.0],
  polar: [0.75, 1.5],
  overbet: [0.5, 1.0, 2.0],
};

/** Accessible categorical palette (Okabe-Ito) — also labeled in text. */
export const ACTION_COLORS = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#999999"];

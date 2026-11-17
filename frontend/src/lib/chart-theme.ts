/**
 * Chart colours for the dark terminal theme. The categorical order was validated against the app
 * surface (#0d1117) for colour-vision-deficiency separation and contrast; assign slots in order and
 * never cycle them. Polarity (gain/loss) uses the app's up/down tokens and always carries a sign.
 */

export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"] as const;
export const UP = "#22c55e";
export const DOWN = "#ef4444";
export const GRID = "#222c3a";
export const AXIS_TICK = { fill: "#8b98a9", fontSize: 10 };
export const TOOLTIP_STYLE = { background: "#131a23", border: "1px solid #2f3c4f", fontSize: 12, borderRadius: 6 };
export const TOOLTIP_LABEL = { color: "#e6edf3" };

/** Diverging blue <-> grey <-> red scale for values in [-1, 1] (e.g. correlations). */
export function divergingColor(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "transparent";
  const mid = [56, 56, 53];
  const pos = [57, 135, 229];
  const neg = [230, 103, 103];
  const t = Math.min(1, Math.abs(v));
  const end = v >= 0 ? pos : neg;
  const c = mid.map((m, i) => Math.round(m + (end[i] - m) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

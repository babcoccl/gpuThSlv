import { ACTION_COLORS, CLASSES } from "../poker";

interface Action {
  name: string;
  label: string;
}

interface Cell {
  hand_class: string;
  combos: number;
  frequencies: Record<string, number>;
}

/** 13x13 grid colored by each class's action mix (stacked fractions).
 *  A text legend always accompanies the colors. */
export default function StrategyGrid({
  actions,
  cells,
  onSelect,
  selected,
}: {
  actions: Action[];
  cells: Cell[];
  onSelect?: (handClass: string) => void;
  selected?: string | null;
}) {
  const byClass = new Map(cells.map((c) => [c.hand_class, c]));
  return (
    <div>
      <div className="legend" role="list" aria-label="action colors">
        {actions.map((a, i) => (
          <span key={a.name} role="listitem">
            <i className="swatch" style={{ background: ACTION_COLORS[i % ACTION_COLORS.length] }} />
            {a.label}
          </span>
        ))}
      </div>
      <div className="range-grid strat-grid" role="grid" aria-label="strategy grid" style={{ marginTop: 6 }}>
        {Array.from({ length: 13 }, (_, row) => (
          <div role="row" key={row} style={{ display: "contents" }}>
            {CLASSES.slice(row * 13, row * 13 + 13).map((cls) => {
          const cell = byClass.get(cls);
          const total = cell ? Object.values(cell.frequencies).reduce((a, b) => a + b, 0) : 0;
          const stops: string[] = [];
          if (cell && total > 0) {
            let acc = 0;
            actions.forEach((a, i) => {
              const frac = (cell.frequencies[a.name] ?? 0) / total;
              const color = ACTION_COLORS[i % ACTION_COLORS.length];
              stops.push(`${color} ${acc * 100}% ${(acc + frac) * 100}%`);
              acc += frac;
            });
          }
          const title = cell
            ? `${cls}: ${actions.map((a) => `${a.label} ${(((cell.frequencies[a.name] ?? 0) / (total || 1)) * 100).toFixed(0)}%`).join(" / ")}`
            : `${cls}: no reach`;
          return (
            <div
              key={cls}
              role="gridcell"
              tabIndex={0}
              className="cell"
              aria-label={title}
              title={title}
              data-cls={cls}
              style={
                stops.length
                  ? { background: `linear-gradient(90deg, ${stops.join(", ")})`, color: "#fff" }
                  : undefined
              }
              onClick={() => onSelect?.(cls)}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect?.(cls)}
            >
              <span style={selected === cls ? { outline: "2px solid #fff", padding: "0 1px" } : undefined}>
                {cls}
              </span>
            </div>
          );
        })}
          </div>
        ))}
      </div>
    </div>
  );
}

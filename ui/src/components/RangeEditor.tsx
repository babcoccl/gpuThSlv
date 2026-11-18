import { useRef, useState } from "react";
import { CLASSES, classLabel, parseRange, serializeRange } from "../poker";

interface Props {
  label: string;
  weights: Map<string, number>;
  onChange: (weights: Map<string, number>) => void;
}

function comboCount(weights: Map<string, number>): number {
  let n = 0;
  for (const cls of weights.keys()) {
    if (cls.length === 2) n += 6;
    else n += cls[2] === "s" ? 4 : 12;
  }
  return n;
}

/** 13x13 range editor: click/drag paints the brush weight onto classes;
 *  the slider edits the focused class (per-class weight) and doubles as the
 *  drag brush; the text box imports/exports the range string. Arrow keys
 *  move focus; Space toggles the focused cell. */
export default function RangeEditor({ label, weights, onChange }: Props) {
  const [brush, setBrush] = useState(1.0);
  const [focused, setFocused] = useState<string | null>(null);
  const [text, setText] = useState(() => serializeRange(weights));
  const [importError, setImportError] = useState<string | null>(null);
  const dragging = useRef(false);
  const paintValue = useRef(1.0);

  const setClass = (cls: string, value: number) => {
    const next = new Map(weights);
    if (value <= 0) next.delete(cls);
    else next.set(cls, value);
    onChange(next);
    setText(serializeRange(next));
  };

  const startDrag = (cls: string) => {
    const cur = weights.get(cls) ?? 0;
    paintValue.current = cur > 0 ? 0 : brush;
    dragging.current = true;
    setFocused(cls);
    setClass(cls, paintValue.current);
  };

  const importText = () => {
    try {
      const parsed = parseRange(text);
      onChange(parsed);
      setImportError(null);
      setText(serializeRange(parsed));
    } catch (err) {
      setImportError(err instanceof Error ? err.message : String(err));
    }
  };

  const onGridKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el.dataset.cls === undefined) return;
    const r = Number(el.dataset.row), c = Number(el.dataset.col);
    let [nr, nc] = [r, c];
    if (e.key === "ArrowRight") nc += 1;
    else if (e.key === "ArrowLeft") nc -= 1;
    else if (e.key === "ArrowDown") nr += 1;
    else if (e.key === "ArrowUp") nr -= 1;
    else if (e.key === " " || e.key === "Enter") {
      startDrag(el.dataset.cls!);
      dragging.current = false;
      e.preventDefault();
      return;
    } else return;
    nr = Math.max(0, Math.min(12, nr));
    nc = Math.max(0, Math.min(12, nc));
    document
      .querySelector<HTMLElement>(`[data-player="${label}"][data-cls="${classLabel(nr, nc)}"]`)
      ?.focus();
    e.preventDefault();
  };

  const focusedWeight = focused ? weights.get(focused) ?? 0 : brush;
  const onSlider = (value: number) => {
    setBrush(value);
    if (focused) setClass(focused, value);
  };

  return (
    <fieldset className="panel" aria-label={label}>
      <legend className="muted">
        {label} — {comboCount(weights)} combos
      </legend>
      <div
        className="range-grid"
        role="grid"
        aria-label={`${label} range grid`}
        data-player={label}
        onKeyDown={onGridKey}
        onPointerUp={() => (dragging.current = false)}
        onPointerLeave={() => (dragging.current = false)}
      >
        {Array.from({ length: 13 }, (_, row) => (
          <div role="row" key={row} style={{ display: "contents" }}>
            {CLASSES.slice(row * 13, row * 13 + 13).map((cls, col) => {
              const w = weights.get(cls) ?? 0;
              return (
                <div
                  key={cls}
                  role="gridcell"
                  tabIndex={0}
                  data-cls={cls}
                  data-row={row}
                  data-col={col}
                  className={`cell${w > 0 ? " on" : ""}`}
                  aria-label={`${cls} weight ${w}`}
                  onPointerDown={(e) => {
                    e.preventDefault();
                    (e.currentTarget as HTMLElement).focus();
                    startDrag(cls);
                  }}
                  onPointerEnter={() => dragging.current && setClass(cls, paintValue.current)}
                  onFocus={() => setFocused(cls)}
                >
                  {w > 0 && <i className="fill" style={{ opacity: Math.min(1, w) }} />}
                  <span>{cls}</span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <div>
          <label htmlFor={`brush-${label}`}>
            weight{focused ? ` — ${focused}` : " (paint brush)"}
          </label>
          <input
            id={`brush-${label}`}
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={focusedWeight}
            onChange={(e) => onSlider(Number(e.target.value))}
          />
          <span className="mono"> {focusedWeight.toFixed(2)}</span>
        </div>
        <div className="grow">
          <label htmlFor={`range-text-${label}`}>range string (import/export)</label>
          <input
            id={`range-text-${label}`}
            type="text"
            className="mono"
            style={{ width: "100%" }}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onBlur={importText}
            onKeyDown={(e) => e.key === "Enter" && importText()}
            placeholder="QQ+, AKs, 77-TT, AKo:0.5, *"
            aria-invalid={!!importError}
          />
        </div>
      </div>
      {importError && (
        <p className="error" role="alert">
          {importError}
        </p>
      )}
      <p className="kbd-hint">
        Click or drag to paint; arrows move, Space toggles; the slider edits the focused class.
      </p>
    </fieldset>
  );
}

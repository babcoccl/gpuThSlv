import { ALL_CARDS, RANKS, SUITS } from "../poker";

interface Props {
  selected: string[];
  onChange: (cards: string[]) => void;
  max?: number;
  /** cards shown but not selectable (e.g. board while picking dead cards) */
  disabled?: string[];
  /** style for disabled-but-listed cards */
  dead?: string[];
  label?: string;
}

/** 13 ranks x 4 suits deck grid; click toggles, arrow keys move focus. */
export default function CardPicker({ selected, onChange, max, disabled = [], dead = [], label }: Props) {
  const blocked = new Set([...selected, ...disabled]);
  const deadSet = new Set(dead);

  const toggle = (card: string) => {
    if (selected.includes(card)) onChange(selected.filter((c) => c !== card));
    else if (max === undefined || selected.length < max) onChange([...selected, card]);
  };

  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el.dataset.card === undefined) return;
    const idx = ALL_CARDS.indexOf(el.dataset.card);
    let next = idx;
    if (e.key === "ArrowRight") next = idx + 1;
    else if (e.key === "ArrowLeft") next = idx - 1;
    // The deck renders as a 13-column grid; vertical arrows move one
    // displayed row (13 cards), horizontal one cell.
    else if (e.key === "ArrowDown") next = idx + 13;
    else if (e.key === "ArrowUp") next = idx - 13;
    else if (e.key === " " || e.key === "Enter") {
      const card = el.dataset.card;
      if (card && !deadSet.has(card)) toggle(card);
      e.preventDefault();
      return;
    } else return;
    next = Math.max(0, Math.min(ALL_CARDS.length - 1, next));
    const target = document.querySelector<HTMLElement>(`[data-card="${ALL_CARDS[next]}"]`);
    target?.focus();
    e.preventDefault();
  };

  return (
    <div>
      {label && <label>{label}</label>}
      <div className="deck" role="grid" aria-label={label ?? "card picker"} onKeyDown={onKey}>
        {RANKS.split("").map((rank) => (
          <div role="row" key={rank} style={{ display: "contents" }}>
            {SUITS.split("").map((suit) => {
              const card = rank + suit;
              const isSel = selected.includes(card);
              const isDead = deadSet.has(card);
              const blockedCard = blocked.has(card) && !isSel;
              const atMax = max !== undefined && selected.length >= max && !isSel;
              return (
                <button
                  key={card}
                  type="button"
                  role="gridcell"
                  data-card={card}
                  className={`card-${suit}${isSel ? " sel" : ""}${isDead ? " dead" : ""}`}
                  aria-selected={isSel}
                  aria-label={card}
                  disabled={isDead || blockedCard || atMax}
                  onClick={() => toggle(card)}
                >
                  {card}
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

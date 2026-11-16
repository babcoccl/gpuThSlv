"""Cards, hole-card combos, and range strings.

Uses the same conventions as ``gpu_cfr.games.hunl_cards`` so the strings
produced here are the hand names inside the engine's infoset keys:

* card id = ``4 * rank_index + suit_index`` with ranks ``AKQJT98765432`` and
  suits ``sdch`` (``As`` = 0, ``Ad`` = 1, ``2h`` = 51);
* a combo is a ``(low_id, high_id)`` pair, written low id first (``AsAd``,
  ``9d9h``, ``4c3c``).

Pure standard library, Python 3.9 syntax.
"""

from typing import Dict, Iterable, List, Sequence, Tuple

RANKS = "AKQJT98765432"
SUITS = "sdch"
CARD_COUNT = 52

Combo = Tuple[int, int]


def card_id(text: str) -> int:
    if not isinstance(text, str) or len(text) != 2:
        raise ValueError("card must be two characters like 'Ah', got %r" % (text,))
    rank = RANKS.find(text[0].upper())
    suit = SUITS.find(text[1].lower())
    if rank < 0 or suit < 0:
        raise ValueError("unknown card %r (ranks %s, suits %s)" % (text, RANKS, SUITS))
    return 4 * rank + suit


def card_str(card: int) -> str:
    if not 0 <= card < CARD_COUNT:
        raise ValueError("card id must be in [0, 52), got %r" % (card,))
    return RANKS[card // 4] + SUITS[card % 4]


def parse_cards(cards: Iterable[str], what: str = "cards") -> Tuple[int, ...]:
    """Card ids for ``cards``; a single string like ``"KsJsTh"`` is split in pairs."""
    if isinstance(cards, str):
        text = cards.replace(" ", "").replace(",", "")
        if len(text) % 2:
            raise ValueError("%s string %r has an odd length" % (what, cards))
        cards = [text[i : i + 2] for i in range(0, len(text), 2)]
    ids = tuple(card_id(card) for card in cards)
    if len(set(ids)) != len(ids):
        raise ValueError("%s contain duplicate cards: %s" % (what, " ".join(card_str(i) for i in ids)))
    return ids


def make_combo(first: int, second: int) -> Combo:
    if first == second:
        raise ValueError("a combo needs two different cards, got %s twice" % card_str(first))
    return (first, second) if first < second else (second, first)


def combo_str(combo: Combo) -> str:
    return card_str(combo[0]) + card_str(combo[1])


def parse_combo(text: str) -> Combo:
    if not isinstance(text, str) or len(text) != 4:
        raise ValueError("combo must be four characters like 'AhKh', got %r" % (text,))
    return make_combo(card_id(text[:2]), card_id(text[2:]))


def combos_conflict(first: Combo, second: Combo) -> bool:
    return first[0] in second or first[1] in second


def _class_combos(high: int, low: int, kind: str) -> List[Combo]:
    """Combos of a rank class; ``kind`` is ``"s"``, ``"o"``, or ``""`` (both)."""
    combos = []
    if high == low:
        for a in range(4):
            for b in range(a + 1, 4):
                combos.append(make_combo(4 * high + a, 4 * high + b))
        return combos
    for a in range(4):
        for b in range(4):
            if (kind == "s" and a != b) or (kind == "o" and a == b):
                continue
            combos.append(make_combo(4 * high + a, 4 * low + b))
    return combos


def _parse_class(text: str) -> Tuple[int, int, str]:
    """``"AKs"`` -> (rank index of A, rank index of K, "s"), high card first."""
    if len(text) not in (2, 3):
        raise ValueError("cannot parse range token %r" % (text,))
    first, second = RANKS.find(text[0].upper()), RANKS.find(text[1].upper())
    kind = text[2].lower() if len(text) == 3 else ""
    if first < 0 or second < 0 or kind not in ("", "s", "o"):
        raise ValueError("cannot parse range token %r" % (text,))
    if first == second and kind:
        raise ValueError("a pair cannot be suited or offsuit: %r" % (text,))
    high, low = (first, second) if first <= second else (second, first)
    return high, low, kind


def _token_combos(token: str) -> List[Combo]:
    if token in ("*", "random", "any"):
        return [(a, b) for a in range(CARD_COUNT) for b in range(a + 1, CARD_COUNT)]
    if len(token) == 4 and token[1].lower() in SUITS and token[3].lower() in SUITS and "-" not in token:
        return [parse_combo(token)]
    if "-" in token:
        left, _, right = token.partition("-")
        high_a, low_a, kind_a = _parse_class(left)
        high_b, low_b, kind_b = _parse_class(right)
        if kind_a != kind_b:
            raise ValueError("range %r mixes suited and offsuit ends" % (token,))
        if high_a == low_a and high_b == low_b:
            lo, hi = sorted((high_a, high_b))
            return [c for rank in range(lo, hi + 1) for c in _class_combos(rank, rank, "")]
        if high_a != high_b or high_a == low_a or high_b == low_b:
            raise ValueError("range %r must be pair-pair or share its high card" % (token,))
        lo, hi = sorted((low_a, low_b))
        return [c for kicker in range(lo, hi + 1) for c in _class_combos(high_a, kicker, kind_a)]
    if token.endswith("+"):
        high, low, kind = _parse_class(token[:-1])
        if high == low:
            return [c for rank in range(0, high + 1) for c in _class_combos(rank, rank, "")]
        return [c for kicker in range(high + 1, low + 1) for c in _class_combos(high, kicker, kind)]
    return _class_combos(*_parse_class(token))


def parse_range(text: Sequence[str]) -> Dict[Combo, float]:
    """Parse a range into ``{combo: weight}``.

    ``text`` is a string of comma/space separated tokens, or a list of tokens.
    Tokens: ``AA``, ``AKs``, ``AKo``, ``AK`` (suited and offsuit), ``TT+``,
    ``A5s+`` (kicker up to just below the high card), ``77-TT``, ``A2s-A5s``,
    a specific combo ``AhKh``, and ``*`` for every combo. A token may carry a
    weight, ``AKo:0.5``; the default is 1. If a combo appears twice the later
    token wins. Zero-weight combos are dropped.
    """
    if isinstance(text, str):
        tokens = text.replace(",", " ").split()
    else:
        tokens = [str(token).strip() for token in text]
    if not tokens:
        raise ValueError("range is empty")
    weights = {}  # type: Dict[Combo, float]
    for token in tokens:
        body, sep, weight_text = token.partition(":")
        weight = 1.0
        if sep:
            try:
                weight = float(weight_text)
            except ValueError:
                raise ValueError("bad weight in range token %r" % (token,))
        if not 0.0 <= weight < float("inf"):
            raise ValueError("range weight must be finite and non-negative: %r" % (token,))
        for combo in _token_combos(body):
            weights[combo] = weight
    weights = {combo: weight for combo, weight in weights.items() if weight > 0.0}
    if not weights:
        raise ValueError("range has no combos with positive weight")
    return weights

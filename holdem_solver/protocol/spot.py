"""``SpotConfig``: a heads-up river or turn spot, as plain data.

This is the description the client sends to the worker (as a JSON object in
``build``); the worker turns it into a GPU-CFR ``GameSpec`` in
``holdem_solver.engine_worker.spot_builder``. Everything here is what the
engine's ``build_hunl_river_game`` / ``build_hunl_turn_game`` can express:

* one street tree per street: check, bet, fold, call. **No raises.**
* one effective ``stack`` and one bet-size set shared by both players and, on
  the turn, both streets (fractions of the current street's pot, capped at the
  remaining stack);
* player 0 acts first (out of position), player 1 second;
* payoffs are player 0's chips relative to the start of the spot, with the
  starting ``pot`` counted as half sunk by each player.

Pure standard library, Python 3.9 syntax.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .cards import Combo, card_str, combo_str, combos_conflict, parse_cards, parse_range

BET_SIZE_SETS = {
    "third": (1.0 / 3.0,),
    "half": (0.5,),
    "pot": (1.0,),
    "standard": (0.5, 1.0),
    "small_big": (1.0 / 3.0, 0.75),
    "wide": (1.0 / 3.0, 2.0 / 3.0, 1.0),
    "polar": (0.75, 1.5),
    "overbet": (0.5, 1.0, 2.0),
}  # type: Dict[str, Tuple[float, ...]]

STREET_BY_BOARD_CARDS = {4: "turn", 5: "river"}
DEFAULT_MAX_NODES = 2000000

_FIELDS = ("board", "range0", "range1", "pot", "stack", "bet_sizes", "all_in", "dead_cards", "name", "max_nodes")


@dataclass(frozen=True)
class ResolvedSpot:
    """A ``SpotConfig`` after card removal.

    ``hands0`` / ``hands1`` are the combos that survive the board and dead
    cards, sorted by card id, with their weights. ``removed0`` / ``removed1``
    are the combos of the written range that the board or a dead card blocks.
    ``num_deals`` counts (hand0, hand1) pairs that share no card; pairs that do
    are not dealt.
    """

    street: str
    board: Tuple[int, ...]
    dead_cards: Tuple[int, ...]
    hands0: Tuple[Tuple[Combo, float], ...]
    hands1: Tuple[Tuple[Combo, float], ...]
    removed0: Tuple[Combo, ...]
    removed1: Tuple[Combo, ...]
    num_deals: int
    bet_fractions: Tuple[float, ...]

    @property
    def weighted(self) -> bool:
        """Whether any weight differs from the rest (else the deal is uniform)."""
        weights = {weight for _, weight in self.hands0} | {weight for _, weight in self.hands1}
        return len(weights) > 1

    def weights(self, player: int) -> Dict[str, float]:
        """``{hand string: weight}`` in the engine's hand naming."""
        return {combo_str(combo): weight for combo, weight in (self.hands0, self.hands1)[player]}

    def blocked_opponent_combos(self, player: int) -> Dict[str, int]:
        """For each hand of ``player``, how many opponent combos it blocks."""
        mine, theirs = (self.hands0, self.hands1) if player == 0 else (self.hands1, self.hands0)
        return {
            combo_str(combo): sum(1 for other, _ in theirs if combos_conflict(combo, other)) for combo, _ in mine
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "street": self.street,
            "board": [card_str(card) for card in self.board],
            "dead_cards": [card_str(card) for card in self.dead_cards],
            "num_hands": [len(self.hands0), len(self.hands1)],
            "num_removed": [len(self.removed0), len(self.removed1)],
            "num_deals": self.num_deals,
            "num_blocked_pairs": len(self.hands0) * len(self.hands1) - self.num_deals,
            "weighted": self.weighted,
            "bet_fractions": list(self.bet_fractions),
        }


@dataclass(frozen=True)
class SpotConfig:
    """A heads-up no-limit hold'em river (5 board cards) or turn (4) spot.

    Args:
        board: Board cards, ``("Ks", "Js", "Th", "7d", "2c")`` or ``"KsJsTh7d2c"``.
        range0: Player 0's range (first to act); see ``cards.parse_range``.
        range1: Player 1's range.
        pot: Chips in the pot before this street's action.
        stack: Effective stack behind, the same for both players.
        bet_sizes: A name from ``BET_SIZE_SETS`` or pot fractions, e.g. ``(0.5, 1.0)``.
        all_in: Also allow betting the whole remaining stack.
        dead_cards: Known cards outside the board that neither range may hold
            (river only: the engine would still deal them as river cards).
        name: GameSpec name; defaults to ``spot_<street>``.
        max_nodes: Refuse to build a tree larger than this; ``None`` disables.
    """

    board: Tuple[str, ...]
    range0: str
    range1: str
    pot: float = 20.0
    stack: float = 100.0
    bet_sizes: Union[str, Tuple[float, ...]] = "standard"
    all_in: bool = False
    dead_cards: Tuple[str, ...] = field(default_factory=tuple)
    name: Optional[str] = None
    max_nodes: Optional[int] = DEFAULT_MAX_NODES

    def __post_init__(self) -> None:
        board = tuple(card_str(card) for card in parse_cards(self.board, "board"))
        dead = tuple(card_str(card) for card in parse_cards(self.dead_cards, "dead_cards"))
        object.__setattr__(self, "board", board)
        object.__setattr__(self, "dead_cards", dead)
        for attr in ("range0", "range1"):
            value = getattr(self, attr)
            if not isinstance(value, str):
                value = ",".join(str(token) for token in value)
            object.__setattr__(self, attr, value)
        if not isinstance(self.bet_sizes, str):
            object.__setattr__(self, "bet_sizes", tuple(float(size) for size in self.bet_sizes))
        object.__setattr__(self, "pot", float(self.pot))
        object.__setattr__(self, "stack", float(self.stack))
        object.__setattr__(self, "all_in", bool(self.all_in))
        if self.name is not None and not isinstance(self.name, str):
            raise ValueError("name must be a string")
        if self.max_nodes is not None and (isinstance(self.max_nodes, bool) or int(self.max_nodes) <= 0):
            raise ValueError("max_nodes must be a positive integer or None")
        self.resolve()

    @property
    def street(self) -> str:
        return STREET_BY_BOARD_CARDS[len(self.board)]

    @property
    def game_name(self) -> str:
        return self.name or "spot_%s" % self.street

    def bet_fractions(self) -> Tuple[float, ...]:
        """Pot fractions handed to the engine, ascending and de-duplicated.

        ``all_in`` adds ``stack / pot``. The engine caps every bet at the
        remaining stack and merges equal amounts, so that fraction is an
        all-in on either street and larger fractions collapse into it.
        """
        if isinstance(self.bet_sizes, str):
            if self.bet_sizes not in BET_SIZE_SETS:
                raise ValueError(
                    "unknown bet-size set %r; choose from %s" % (self.bet_sizes, ", ".join(sorted(BET_SIZE_SETS)))
                )
            sizes = list(BET_SIZE_SETS[self.bet_sizes])
        else:
            sizes = list(self.bet_sizes)
        for size in sizes:
            if not (math.isfinite(size) and size > 0.0):
                raise ValueError("bet sizes must be positive pot fractions, got %r" % (size,))
        if self.all_in:
            sizes.append(self.stack / self.pot)
        if not sizes:
            raise ValueError("bet_sizes is empty and all_in is off; the engine needs at least one bet size")
        return tuple(sorted(set(sizes)))

    def resolve(self) -> ResolvedSpot:
        """Validate and apply card removal; raises ``ValueError`` on a bad spot."""
        if len(self.board) not in STREET_BY_BOARD_CARDS:
            raise ValueError(
                "board must have 4 (turn) or 5 (river) cards, got %d; the engine has no flop or preflop builder"
                % len(self.board)
            )
        if not (math.isfinite(self.pot) and self.pot > 0.0 and math.isfinite(self.stack) and self.stack > 0.0):
            raise ValueError("pot and stack must be positive")
        board = parse_cards(self.board, "board")
        dead = parse_cards(self.dead_cards, "dead_cards")
        if set(board) & set(dead):
            raise ValueError("dead_cards overlap the board")
        if dead and self.street != "river":
            raise ValueError("dead_cards are only supported on the river: the turn builder deals every unseen card")
        blocked = set(board) | set(dead)
        hands = []  # type: List[Tuple[Tuple[Combo, float], ...]]
        removed = []  # type: List[Tuple[Combo, ...]]
        for label, text in (("range0", self.range0), ("range1", self.range1)):
            weights = parse_range(text)
            kept = sorted((combo, weight) for combo, weight in weights.items() if not blocked & set(combo))
            if not kept:
                raise ValueError("%s has no combos left after removing board and dead cards" % label)
            hands.append(tuple(kept))
            removed.append(tuple(sorted(combo for combo in weights if blocked & set(combo))))
        num_deals = sum(1 for a, _ in hands[0] for b, _ in hands[1] if not combos_conflict(a, b))
        if num_deals == 0:
            raise ValueError("every (range0, range1) hand pair shares a card; there is no deal")
        return ResolvedSpot(
            street=self.street,
            board=board,
            dead_cards=dead,
            hands0=hands[0],
            hands1=hands[1],
            removed0=removed[0],
            removed1=removed[1],
            num_deals=num_deals,
            bet_fractions=self.bet_fractions(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "board": list(self.board),
            "range0": self.range0,
            "range1": self.range1,
            "pot": self.pot,
            "stack": self.stack,
            "bet_sizes": self.bet_sizes if isinstance(self.bet_sizes, str) else list(self.bet_sizes),
            "all_in": self.all_in,
            "dead_cards": list(self.dead_cards),
            "name": self.name,
            "max_nodes": self.max_nodes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SpotConfig":
        if not isinstance(data, Mapping):
            raise ValueError("spot must be an object")
        unknown = sorted(set(data) - set(_FIELDS))
        if unknown:
            raise ValueError("unknown spot fields: %s" % ", ".join(unknown))
        missing = [key for key in ("board", "range0", "range1") if key not in data]
        if missing:
            raise ValueError("spot is missing: %s" % ", ".join(missing))
        try:
            return cls(**dict(data))
        except TypeError as exc:
            raise ValueError("bad spot: %s" % exc)


def as_spot_dict(game: Union["SpotConfig", Mapping[str, Any], Sequence[Any]]) -> Dict[str, Any]:
    """Wire form of a spot given as a ``SpotConfig`` or a mapping (validated)."""
    if isinstance(game, SpotConfig):
        return game.to_dict()
    return SpotConfig.from_dict(game).to_dict()

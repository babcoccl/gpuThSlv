"""Hand-written HUNL river/turn ``GameSpec`` builder with raise support.

Phase 6. ``gpu_cfr.efg.spec`` is a plain dataclass spec (see
docs/PHASE6.md), so a builder only has to emit ``nodes``, ``infosets``,
``chance_reach``, and ``metadata`` — ``StagewiseCFRSolver`` compiles it.

Grammar (per street, matching ``build_hunl_river_game`` at ``raise_cap=0``):

* facing no bet: ``check`` plus one ``b<amt>`` per bet size for the acting
  player, ``amt = min(stack - contribution, fraction * street_pot)``;
* facing a bet: ``fold``, ``call``, then one ``r<amt>`` per raise size while
  ``raises_used < raise_cap`` and the raiser can cover the call; ``r<amt>``
  raises the raiser's total contribution to ``amt``, sized either as a
  multiple of the bet level faced or as a pot fraction of the pot after a
  call;
* ``check`` after ``check``, or a ``call``, ends the street (showdown on the
  river, river-deal chance node on the turn); ``fold`` ends the hand.

Node ids are preorder (parent before children, actions in order), matching
the shipped builders. The whole deal subtree — turn line, river chance
node, river street — is emitted once into a ``_Template`` and stamped per
deal with offset ids, so build cost is a flat copy loop per deal. River
cards and hole cards appear as ``%CARD<i>%`` / ``%HAND%`` placeholders
resolved at stamp time; showdown ranks are memoized per (hand, river card).

Only ``.venv-engine`` (Python 3.9) imports this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from gpu_cfr.efg.spec import ActionSpec, GameSpec, InfoSetSpec, NodeSpec, NodeType
from gpu_cfr.games.hunl_cards import (
    card_to_str,
    hand_to_str,
    hands_conflict,
    str_to_card,
)
from gpu_cfr.games.hunl_poker_eval import hunl_showdown_rank

Hand = Tuple[int, int]
# A raise size: ("x", m) raises to m times the bet level faced (the
# opponent's total contribution this spot); ("p", f) raises to the bet level
# plus f times the pot after calling (f=1 is a pot-sized raise).
RaiseSize = Tuple[str, float]
SizeSets = Union[Sequence[float], Mapping[int, Sequence[float]]]

_NUM_RIVER_CARDS = 44  # 52 - 4 board - 4 hole cards


def _fmt_amount(amount: float) -> str:
    """The shipped builders' ``%g`` style: ``b10``, ``b6.66667``."""
    return "%g" % amount


def _size_list(sizes: SizeSets, player: int) -> Tuple[float, ...]:
    """Bet pot-fractions for ``player`` from a shared or per-player mapping."""
    if isinstance(sizes, Mapping):
        return tuple(float(f) for f in sizes[player])
    return tuple(float(f) for f in sizes)


def _street_bet_amounts(fractions: Sequence[float], street_pot: float,
                        remaining: float) -> Tuple[float, ...]:
    """Sorted unique bet amounts for one street's bet options."""
    return tuple(sorted({min(remaining, fraction * street_pot)
                         for fraction in fractions}))


def _raise_amounts(raise_sizes: Sequence[RaiseSize], bet_level: float,
                   pot_total: float, to_call: float,
                   raise_target_max: float) -> Tuple[float, ...]:
    """Sorted unique raise-to levels above ``bet_level``.

    ``bet_level`` is the opponent's total contribution this spot (the level
    being raised); ``pot_total`` is pot + both contributions before this
    action; ``raise_target_max`` is the raiser's stack cap (all-in raise).
    """
    levels = set()
    for kind, value in raise_sizes:
        if kind == "x":
            level = value * bet_level
        elif kind == "p":
            level = bet_level + value * (pot_total + to_call)
        else:
            raise ValueError("raise size kind must be 'x' or 'p', got %r" % (kind,))
        # No-limit min-raise: the raise increment must at least equal the
        # increment faced (to_call); a shorter all-in is still legal.
        level = min(max(level, bet_level + to_call), raise_target_max)
        if level > bet_level:
            levels.add(level)
    return tuple(sorted(levels))


@dataclass(frozen=True)
class _TemplateNode:
    """One node of a deal's betting tree, ids relative to the subtree root."""

    node_type: Any
    player: int
    # Infoset key containing ``%HAND%`` and (on a turn spot's river street)
    # ``%CARD%`` placeholders; None for chance/terminal nodes.
    infoset_key: Optional[str]
    # (name, child rel id); river-card chance actions use name "%CARD<i>%"
    # where i indexes the deal's 44-card river list.
    actions: Tuple[Tuple[str, int], ...]
    # Terminal payoff rule: None, "fold:<folder player>", or "showdown";
    # c0/c1 are the contributions this spot the terminal was stamped under.
    payoff_kind: Optional[str]
    c0: float
    c1: float
    # River-card index this node sits under (-1 for river-only games);
    # showdown terminals use it to pick the per-card showdown ranks.
    card_index: int


class _Template:
    """A deal's subtree built once, stamped per deal with offset ids."""

    def __init__(self) -> None:
        self.nodes = []  # type: List[Optional[_TemplateNode]]

    def reserve(self) -> int:
        rel = len(self.nodes)
        self.nodes.append(None)
        return rel

    def place(self, rel: int, node_type: Any, player: int,
              infoset_key: Optional[str],
              actions: Tuple[Tuple[str, int], ...],
              payoff_kind: Optional[str] = None, c0: float = 0.0,
              c1: float = 0.0, card_index: int = -1) -> int:
        self.nodes[rel] = _TemplateNode(node_type, player, infoset_key,
                                        actions, payoff_kind, c0, c1,
                                        card_index)
        return rel


class _StreetBuilder:
    """Recursive emit of one street's player nodes into a template.

    ``c0``/``c1`` are *total* contributions this spot (a turn spot's river
    street starts with both players at their turn contribution and the pot
    grown by twice it). Bets are sized on ``street_pot``; a player's stack
    remaining is ``stack - c_player``. ``key_prefix`` carries
    ``p<pl>|%HAND%|`` plus the street's path section (e.g. the turn history
    and ``%CARD%`` marker on the river street).
    """

    def __init__(self, template: _Template, street_pot: float, stack: float,
                 bet_fractions: SizeSets, raise_cap: int,
                 raise_sizes: Tuple[RaiseSize, ...],
                 on_street_end: Callable[[Tuple[str, ...], float, float], int],
                 key_prefix: Tuple[str, str], card_index: int = -1,
                 base_pot: Optional[float] = None) -> None:
        self._t = template
        self._pot = street_pot
        # ``street_pot`` sizes bets; ``base_pot`` is the pot at this street's
        # start so ``pot_total`` (for "p" raises) is base + both cumulative
        # contributions — for a river street contributions carry over from
        # the turn, so base_pot is the pre-turn pot, not street_pot.
        self._base_pot = street_pot if base_pot is None else base_pot
        self._stack = stack
        self._fractions = bet_fractions
        self._raise_cap = raise_cap
        self._raise_sizes = raise_sizes
        self._end = on_street_end
        self._prefix = key_prefix
        self._card = card_index
        # Bet amounts are fixed at street start (bets only appear facing no
        # bet, where both contributions equal the street-start level).
        self._bets = (
            _street_bet_amounts(_size_list(bet_fractions, 0), street_pot, stack),
            _street_bet_amounts(_size_list(bet_fractions, 1), street_pot, stack),
        )

    def build(self, history: Tuple[str, ...], c0: float, c1: float,
              to_act: int, checked: bool, raises_used: int) -> int:
        """Emit the subtree where ``to_act`` acts; returns its rel id."""
        rel = self._t.reserve()
        faced = (c1 - c0) if to_act == 0 else (c0 - c1)
        mine = c0 if to_act == 0 else c1
        remaining = self._stack - mine
        actions = []  # type: List[Tuple[str, int]]
        if faced <= 0.0:
            if checked:
                actions.append(("check", self._end(history + ("check",), c0, c1)))
            else:
                actions.append(("check", self.build(history + ("check",), c0, c1,
                                                    1 - to_act, True, raises_used)))
            emitted = set()
            for amount in self._bets[to_act]:
                amount = min(amount, remaining)
                if amount <= 0.0 or amount in emitted:
                    continue
                emitted.add(amount)
                nc = (c0 + amount, c1) if to_act == 0 else (c0, c1 + amount)
                name = "b%s" % _fmt_amount(amount)
                child = self.build(history + (name,), nc[0], nc[1],
                                   1 - to_act, False, raises_used)
                actions.append((name, child))
        else:
            actions.append(("fold",
                            self._t.place(self._t.reserve(), NodeType.TERMINAL,
                                          -1, None, (), "fold:%d" % to_act,
                                          c0, c1, self._card)))
            call = min(faced, remaining)
            nc = (c0 + call, c1) if to_act == 0 else (c0, c1 + call)
            actions.append(("call", self._end(history + ("call",), nc[0], nc[1])))
            if raises_used < self._raise_cap and remaining > faced:
                bet_level = mine + faced  # opponent's total contribution
                for level in _raise_amounts(self._raise_sizes, bet_level,
                                            self._base_pot + c0 + c1, faced,
                                            mine + remaining):
                    nc = (level, c1) if to_act == 0 else (c0, level)
                    name = "r%s" % _fmt_amount(level)
                    child = self.build(history + (name,), nc[0], nc[1],
                                       1 - to_act, False, raises_used + 1)
                    actions.append((name, child))
        key = "%s%s" % (self._prefix[to_act], ",".join(history))
        self._t.place(rel, NodeType.PLAYER, to_act, key, tuple(actions),
                      card_index=self._card)
        return rel


def _showdown_payoff(rank0: int, rank1: int, pot_total: float) -> float:
    """player 0's net chips at showdown; lower rank wins."""
    if rank0 < rank1:
        return pot_total / 2.0
    if rank0 > rank1:
        return -pot_total / 2.0
    return 0.0


def _terminal_payoff(node: _TemplateNode, pot: float, rank0: int,
                     rank1: int) -> float:
    """Stamp a terminal's payoff for one deal; ``pot`` is the spot pot."""
    if node.payoff_kind == "showdown":
        return _showdown_payoff(rank0, rank1, pot + 2.0 * min(node.c0, node.c1))
    folder = int(node.payoff_kind.split(":")[1])
    if folder == 0:
        return -(pot / 2.0) - node.c0
    return (pot / 2.0) + node.c1


def _enumerate_deals(hands0: Sequence[Hand],
                     hands1: Sequence[Hand]) -> List[Tuple[Hand, Hand]]:
    return [(a, b) for a in hands0 for b in hands1 if not hands_conflict(a, b)]


def _river_cards(board_ids: Sequence[int], h0: Hand, h1: Hand) -> List[int]:
    seen = set(board_ids) | set(h0) | set(h1)
    return [card for card in range(52) if card not in seen]


class _RankCache:
    """Memoized ``hunl_showdown_rank`` keyed by (hand, board-with-card)."""

    def __init__(self, board_ids: Sequence[int]) -> None:
        self._board4 = list(board_ids)
        self._cache = {}  # type: Dict[Tuple[Hand, int], int]
        self._flat = {}  # type: Dict[Hand, int]

    def river_rank(self, hand: Hand) -> int:
        rank = self._flat.get(hand)
        if rank is None:
            rank = hunl_showdown_rank(hand, list(self._board4))
            self._flat[hand] = rank
        return rank

    def turn_rank(self, hand: Hand, card: int) -> int:
        key = (hand, card)
        rank = self._cache.get(key)
        if rank is None:
            rank = hunl_showdown_rank(hand, self._board4 + [card])
            self._cache[key] = rank
        return rank


def _stamp_deal(nodes: Dict[int, NodeSpec], infosets: Dict[str, InfoSetSpec],
                chance_reach: Dict[int, float], template: _Template,
                offset: int, pot: float, h0: Hand, h1: Hand,
                river_cards: Optional[List[int]],
                ranks: Optional[List[Tuple[int, int]]],
                rank0: int, rank1: int) -> None:
    """Copy ``template`` into ``nodes``/``infosets`` at ``offset``.

    ``river_cards``/``ranks`` are used on turn deals: the 44 unseen cards and
    the (rank0, rank1) pair under each. On river deals only ``rank0``/``rank1``
    are needed.
    """
    s0, s1 = hand_to_str(h0), hand_to_str(h1)
    for rel, node in enumerate(template.nodes):
        assert node is not None
        key = node.infoset_key
        if key is not None:
            key = key.replace("%HAND%", s0 if node.player == 0 else s1)
            if node.card_index >= 0:
                assert river_cards is not None
                key = key.replace("%CARD%", card_to_str(river_cards[node.card_index]))
            infosets[key] = InfoSetSpec(key, node.player,
                                        tuple(nm for nm, _ in node.actions))
        if node.node_type == NodeType.CHANCE and river_cards is not None:
            actions = []
            reach = 1.0 / len(node.actions)
            for nm, ch in node.actions:
                idx = int(nm[5:-1])
                actions.append(ActionSpec(card_to_str(river_cards[idx]),
                                          offset + ch))
                chance_reach[offset + ch] = reach
        else:
            actions = [ActionSpec(nm, offset + ch) for nm, ch in node.actions]
        if node.payoff_kind == "showdown" and node.card_index >= 0:
            assert ranks is not None
            r0, r1 = ranks[node.card_index]
            payoff = _terminal_payoff(node, pot, r0, r1)
        elif node.payoff_kind is not None:
            payoff = _terminal_payoff(node, pot, rank0, rank1)
        else:
            payoff = 0.0
        nodes[offset + rel] = NodeSpec(offset + rel, node.node_type,
                                       node.player, key, tuple(actions),
                                       payoff)


def _assemble(name: str, pot: float, stack: float, board: Sequence[str],
              board_ids: List[int], deals: List[Tuple[Hand, Hand]],
              template: _Template, metadata: Dict[str, Any],
              per_deal_cards: bool) -> GameSpec:
    """Stamp ``template`` for each deal and assemble the ``GameSpec``."""
    num_deals = len(deals)
    num_tree = len(template.nodes)
    nodes = {}  # type: Dict[int, NodeSpec]
    infosets = {}  # type: Dict[str, InfoSetSpec]
    chance_reach = {0: 1.0}  # type: Dict[int, float]
    root_actions = []  # type: List[ActionSpec]
    cache = _RankCache(board_ids)
    for di, (h0, h1) in enumerate(deals):
        offset = 1 + di * num_tree
        root_actions.append(ActionSpec(
            "%s:%s" % (hand_to_str(h0), hand_to_str(h1)), offset))
        chance_reach[offset] = 1.0 / num_deals
        if per_deal_cards:
            cards = _river_cards(board_ids, h0, h1)
            ranks = [(cache.turn_rank(h0, c), cache.turn_rank(h1, c))
                     for c in cards]
            _stamp_deal(nodes, infosets, chance_reach, template, offset,
                        pot, h0, h1, cards, ranks, 0, 0)
        else:
            _stamp_deal(nodes, infosets, chance_reach, template, offset,
                        pot, h0, h1, None, None,
                        cache.river_rank(h0), cache.river_rank(h1))
    nodes[0] = NodeSpec(0, NodeType.CHANCE, actions=tuple(root_actions))
    return GameSpec(name=name, root=0, nodes=nodes, infosets=infosets,
                    chance_reach=chance_reach, metadata=metadata)


def build_river_spec(board: Sequence[str], pot: float, stack: float,
                     bet_sizes: SizeSets, hands0: Sequence[Hand],
                     hands1: Sequence[Hand], *, raise_cap: int = 0,
                     raise_sizes: Sequence[RaiseSize] = (),
                     name: str = "hunl_river_custom") -> GameSpec:
    """Build a river endgame ``GameSpec``.

    Identical to ``build_hunl_river_game`` when ``raise_cap == 0`` and
    ``bet_sizes`` is a shared list of pot fractions. ``bet_sizes`` may also
    be ``{0: [...], 1: [...]}`` for per-player sizes. ``raise_sizes`` entries
    are ``("x", m)`` (raise to ``m`` x the bet level faced) or ``("p", f)``
    (raise to the bet level plus ``f`` x the pot after calling).
    """
    board_ids = [str_to_card(card) for card in board]
    if len(board_ids) != 5:
        raise ValueError("a river board has exactly 5 cards")
    if raise_cap < 0:
        raise ValueError("raise_cap must be >= 0")
    pot, stack = float(pot), float(stack)

    template = _Template()

    def street_end(history: Tuple[str, ...], c0: float, c1: float) -> int:
        return template.place(template.reserve(), NodeType.TERMINAL, -1,
                              None, (), "showdown", c0, c1, -1)

    builder = _StreetBuilder(template, pot, stack, bet_sizes, raise_cap,
                             tuple(raise_sizes), street_end,
                             ("p0|%HAND%|", "p1|%HAND%|"))
    builder.build((), 0.0, 0.0, 0, False, 0)

    bets = builder._bets
    metadata = {
        "board": tuple(board), "pot": pot, "stack": stack,
        "bet_amounts": bets[0],
        "num_hands": (len(hands0), len(hands1)),
        "num_deals": len(_enumerate_deals(hands0, hands1)),
        "raise_cap": raise_cap, "builder": "custom",
    }
    if bets[0] != bets[1]:
        metadata["bet_amounts"] = bets
        metadata["bet_amounts_by_player"] = True
    if raise_sizes:
        metadata["raise_sizes"] = [list(r) for r in raise_sizes]
    deals = _enumerate_deals(hands0, hands1)
    return _assemble(name, pot, stack, board, board_ids, deals, template,
                     metadata, per_deal_cards=False)


def build_turn_spec(board: Sequence[str], pot: float, stack: float,
                    bet_sizes: SizeSets, hands0: Sequence[Hand],
                    hands1: Sequence[Hand], *, raise_cap: int = 0,
                    raise_sizes: Sequence[RaiseSize] = (),
                    river_bet_sizes: Optional[SizeSets] = None,
                    name: str = "hunl_turn_custom") -> GameSpec:
    """Turn endgame: each continuing turn line ends in a 44-way river-deal
    chance node followed by a river street subtree per card.

    ``river_bet_sizes`` defaults to ``bet_sizes``; either may be a shared
    list or ``{0: [...], 1: [...]}`` per player. ``raise_cap`` applies per
    street.
    """
    board_ids = [str_to_card(card) for card in board]
    if len(board_ids) != 4:
        raise ValueError("a turn board has exactly 4 cards")
    if raise_cap < 0:
        raise ValueError("raise_cap must be >= 0")
    pot, stack = float(pot), float(stack)
    river_fractions = river_bet_sizes if river_bet_sizes is not None else bet_sizes

    template = _Template()

    raise_sizes_t = tuple(raise_sizes)

    turn_end_levels = set()  # contribution level at each called turn line

    def street_end(history: Tuple[str, ...], c0: float, c1: float) -> int:
        """Emit the river-deal chance node plus one river street per card."""
        chance_rel = template.reserve()
        river_pot = pot + c0 + c1
        turn_hist = ",".join(history)
        turn_end_levels.add((c0, c1))
        child_ids = []
        for idx in range(_NUM_RIVER_CARDS):
            river = _StreetBuilder(
                template, river_pot, stack, river_fractions, raise_cap,
                raise_sizes_t, river_showdown_impl(idx),
                ("p0|%HAND%|" + turn_hist + "|%CARD%|",
                 "p1|%HAND%|" + turn_hist + "|%CARD%|"),
                card_index=idx, base_pot=pot)
            child_ids.append(river.build((), c0, c1, 0, False, 0))
        template.place(chance_rel, NodeType.CHANCE, -1, None,
                       tuple(("%CARD" + str(i) + "%", ch)
                             for i, ch in enumerate(child_ids)))
        return chance_rel

    def river_showdown_impl(idx: int) -> Callable[[Tuple[str, ...], float, float], int]:
        def end(history: Tuple[str, ...], c0: float, c1: float) -> int:
            return template.place(template.reserve(), NodeType.TERMINAL, -1,
                                  None, (), "showdown", c0, c1, idx)
        return end

    builder = _StreetBuilder(template, pot, stack, bet_sizes, raise_cap,
                             raise_sizes_t, street_end,
                             ("p0|%HAND%|", "p1|%HAND%|"))
    builder.build((), 0.0, 0.0, 0, False, 0)

    bets = builder._bets
    # River bet amounts per turn end contribution, matching the shipped
    # metadata shape: ((turn_contrib, river_amounts), ...).
    river_amounts = {}
    deals = _enumerate_deals(hands0, hands1)
    metadata = {
        "board": tuple(board), "pot": pot, "stack": stack,
        "turn_bet_amounts": bets[0],
        "num_hands": (len(hands0), len(hands1)),
        "num_deals": len(deals), "num_rivers": _NUM_RIVER_CARDS,
        "raise_cap": raise_cap, "builder": "custom",
    }
    if bets[0] != bets[1]:
        metadata["turn_bet_amounts"] = bets
        metadata["bet_amounts_by_player"] = True
    # Every level at which the turn ended in a check-check or a call —
    # includes p1's opening bets and called raise levels, not just p0's.
    for c0, c1 in sorted(turn_end_levels):
        c = max(c0, c1)
        river_pot_c = pot + c0 + c1
        amounts = (_street_bet_amounts(_size_list(river_fractions, 0), river_pot_c, stack - c),
                   _street_bet_amounts(_size_list(river_fractions, 1), river_pot_c, stack - c))
        river_amounts[c] = amounts[0] if amounts[0] == amounts[1] else amounts
    metadata["river_bet_amounts"] = tuple(sorted(river_amounts.items()))
    if raise_sizes:
        metadata["raise_sizes"] = [list(r) for r in raise_sizes]
    return _assemble(name, pot, stack, board, board_ids, deals, template,
                     metadata, per_deal_cards=True)

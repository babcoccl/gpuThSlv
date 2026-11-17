"""Turn a ``SpotConfig`` into a GPU-CFR ``GameSpec``.

Uses the engine's own builders, ``gpu_cfr.games.hunl_river.build_hunl_river_game``
and ``gpu_cfr.games.hunl_turn.build_hunl_turn_game``; their accepted
parameters are recorded in docs/PHASE2.md. Range weights are not a builder
argument: the tree is built with a uniform deal and the weights are applied to
the built solver with ``set_root_ranges``.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Union

from holdem_solver.protocol.spot import ResolvedSpot, SpotConfig


class SpotTooLargeError(ValueError):
    """The spot's tree would exceed ``SpotConfig.max_nodes``."""


@dataclass(frozen=True)
class BuiltSpot:
    config: SpotConfig
    resolved: ResolvedSpot
    spec: Any
    info: Dict[str, Any]

    def apply_weights(self, solver: Any) -> None:
        """Set the root deal distribution on a solver built from ``spec``."""
        if self.resolved.weighted:
            solver.set_root_ranges(self.resolved.weights(0), self.resolved.weights(1))


def _engine_builder(street: str) -> Any:
    from holdem_solver.engine_worker.solver import _load_gpu_cfr

    _load_gpu_cfr()  # puts the vendored GPU-CFR on sys.path
    if street == "river":
        from gpu_cfr.games.hunl_river import build_hunl_river_game

        return build_hunl_river_game
    from gpu_cfr.games.hunl_turn import build_hunl_turn_game

    return build_hunl_turn_game


def _build(config: SpotConfig, resolved: ResolvedSpot, hands0: Any, hands1: Any, name: str) -> Any:
    from holdem_solver.protocol.cards import card_str

    board = tuple(card_str(card) for card in resolved.board)
    if config.needs_custom_builder:
        from holdem_solver.engine_worker.solver import _load_gpu_cfr

        _load_gpu_cfr()  # puts the vendored GPU-CFR on sys.path
        from holdem_solver.engine_worker.tree_builder import build_river_spec, build_turn_spec

        bet_sizes = config.bet_size_map()
        if resolved.street == "turn":
            if isinstance(bet_sizes, dict) and "turn" in bet_sizes:
                return build_turn_spec(
                    board,
                    pot=config.pot,
                    stack=config.stack,
                    bet_sizes=bet_sizes["turn"],
                    river_bet_sizes=bet_sizes["river"],
                    raise_cap=config.raise_cap,
                    raise_sizes=config.raise_sizes,
                    hands0=hands0,
                    hands1=hands1,
                    name=name,
                )
            return build_turn_spec(
                board,
                pot=config.pot,
                stack=config.stack,
                bet_sizes=bet_sizes,
                raise_cap=config.raise_cap,
                raise_sizes=config.raise_sizes,
                hands0=hands0,
                hands1=hands1,
                name=name,
            )
        return build_river_spec(
            board,
            pot=config.pot,
            stack=config.stack,
            bet_sizes=bet_sizes,
            raise_cap=config.raise_cap,
            raise_sizes=config.raise_sizes,
            hands0=hands0,
            hands1=hands1,
            name=name,
        )
    return _engine_builder(resolved.street)(
        board,
        pot=config.pot,
        stack=config.stack,
        bet_fractions=resolved.bet_fractions,
        hands0=hands0,
        hands1=hands1,
        name=name,
    )


def estimate_spot_nodes(config: Union[SpotConfig, Mapping[str, Any]]) -> int:
    """Exact node count of the spot's tree, without building it.

    Every deal hangs an identical betting tree under the root chance node, so
    one single-deal tree gives the size: ``1 + num_deals * (single - 1)``.
    """
    if not isinstance(config, SpotConfig):
        config = SpotConfig.from_dict(config)
    resolved = config.resolve()
    hand0, hand1 = next(
        (a, b) for a, _ in resolved.hands0 for b, _ in resolved.hands1 if a[0] not in b and a[1] not in b
    )
    single = _build(config, resolved, [hand0], [hand1], "spot_probe")
    return 1 + resolved.num_deals * (len(single.nodes) - 1)


def build_spot(config: Union[SpotConfig, Mapping[str, Any]]) -> BuiltSpot:
    """Build the GameSpec for ``config`` (a ``SpotConfig`` or its dict form)."""
    if not isinstance(config, SpotConfig):
        config = SpotConfig.from_dict(config)
    resolved = config.resolve()
    estimated = estimate_spot_nodes(config)
    if config.max_nodes is not None and estimated > config.max_nodes:
        raise SpotTooLargeError(
            "spot needs %d nodes (%d deals), over max_nodes=%d; narrow the ranges or bet sizes, or raise max_nodes"
            % (estimated, resolved.num_deals, config.max_nodes)
        )
    spec = _build(
        config,
        resolved,
        [combo for combo, _ in resolved.hands0],
        [combo for combo, _ in resolved.hands1],
        config.game_name,
    )
    info = resolved.summary()
    info.update({
        "pot": config.pot,
        "stack": config.stack,
        "all_in": config.all_in,
        "raise_cap": config.raise_cap,
        "raise_sizes": [list(size) for size in config.raise_sizes],
        "custom_builder": config.needs_custom_builder,
    })
    for key in ("bet_amounts", "turn_bet_amounts", "river_bet_amounts", "num_rivers"):
        if key in spec.metadata:
            info[key] = spec.metadata[key]
    return BuiltSpot(config=config, resolved=resolved, spec=spec, info=info)

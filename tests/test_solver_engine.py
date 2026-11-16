import pytest
import torch

from holdem_solver.engine import SolverEngine, SolverProgress

KUHN_VALUE_P0 = -1.0 / 18.0


def _kuhn_engine(device: str, **kwargs):
    options = {"device": device, "variant": "cfr_plus", "seed": 42}
    options.update(kwargs)
    return SolverEngine("kuhn_poker", **options)


@pytest.mark.gpu
def test_kuhn_converges_cuda():
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    with _kuhn_engine("cuda", iterations=2000) as engine:
        result = engine.solve(iterations=2000)
        assert result.iterations_run == 2000
        assert result.total_iterations == 2000
        assert result.exploitability < 0.005
        assert result.profile_value_p0 == pytest.approx(KUHN_VALUE_P0, abs=0.001)


@pytest.mark.gpu
def test_solve_progress_callback_reports_exploitability_every_n():
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    events = []
    with _kuhn_engine("cuda", iterations=1000) as engine:
        result = engine.solve(iterations=1000, progress_every=250, progress_callback=events.append)
    assert result.iterations_run == 1000
    assert [event.iterations_run for event in events] == [250, 500, 750, 1000]
    assert all(isinstance(event, SolverProgress) for event in events)
    assert all(event.exploitability >= 0.0 for event in events)
    assert events[-1].iteration == 1000


@pytest.mark.gpu
def test_cpu_cuda_agreement():
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    iterations = 2000
    cpu = _kuhn_engine("cpu", iterations=iterations)
    cuda = _kuhn_engine("cuda", iterations=iterations)
    try:
        cpu_result = cpu.solve(iterations=iterations)
        cuda_result = cuda.solve(iterations=iterations)
        assert cpu_result.exploitability == pytest.approx(cuda_result.exploitability, abs=1e-4)
        assert cpu_result.profile_value_p0 == pytest.approx(cuda_result.profile_value_p0, abs=1e-4)
        cpu_flat = cpu.get_strategy_flat().detach().cpu()
        cuda_flat = cuda.get_strategy_flat().detach().cpu()
        assert torch.allclose(cpu_flat, cuda_flat, atol=2e-4, rtol=1e-4)
    finally:
        cpu.close()
        cuda.close()


@pytest.mark.gpu
def test_reset_matches_fresh_solve_cuda():
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    iterations = 1000
    fresh = _kuhn_engine("cuda", iterations=iterations)
    reused = _kuhn_engine("cuda", iterations=iterations)
    try:
        fresh_result = fresh.solve(iterations=iterations)
        fresh_flat = fresh.get_strategy_flat().detach().cpu().clone()

        reused.solve(iterations=iterations)
        first_flat = reused.get_strategy_flat().detach().cpu().clone()
        reused.reset()
        assert reused.iterations == 0
        second_result = reused.solve(iterations=iterations)
        second_flat = reused.get_strategy_flat().detach().cpu().clone()

        # CUDA float reductions are not bitwise deterministic across graph
        # replays; 2e-4 is far above the observed ~1e-5 run-to-run spread.
        assert torch.allclose(first_flat, second_flat, atol=2e-4, rtol=1e-4)
        assert torch.allclose(second_flat, fresh_flat, atol=2e-4, rtol=1e-4)
        assert second_result.iterations_run == iterations
        assert second_result.total_iterations == iterations
        assert fresh_result.total_iterations == iterations
    finally:
        fresh.close()
        reused.close()


def test_solve_mode_validation_cpu():
    engine = _kuhn_engine("cpu", iterations=10)
    try:
        with pytest.raises(ValueError):
            engine.solve(iterations=1, time_budget=1.0)
        with pytest.raises(ValueError):
            engine.solve(iterations=0)
        with pytest.raises(ValueError):
            engine.solve(time_budget=-1.0)
        with pytest.raises(ValueError):
            engine.solve(target_exploitability=-0.1)
        with pytest.raises(ValueError):
            engine.solve(target_exploitability=0)
        with pytest.raises(ValueError):
            engine.solve(iterations=1, progress_every=0)
    finally:
        engine.close()


def test_solve_time_budget_cpu():
    with _kuhn_engine("cpu", iterations=10) as engine:
        result = engine.solve(time_budget=0.05, chunk_iterations=10)
        assert result.iterations_run > 0
        assert result.time_budget_exhausted
        assert result.exploitability >= 0.0


def test_solve_target_exploitability_cpu():
    with _kuhn_engine("cpu", iterations=100) as engine:
        result = engine.solve(target_exploitability=0.02, chunk_iterations=50)
        assert result.target_reached
        assert result.exploitability <= 0.02
        assert result.iterations_run > 0


def test_build_after_empty_constructor_cpu():
    engine = SolverEngine(device="cpu", variant="cfr_plus", seed=42, iterations=10)
    try:
        assert not engine.is_built
        engine.build("kuhn_poker")
        assert engine.is_built
        result = engine.solve(iterations=5)
        assert result.iterations_run == 5
        assert engine.iterations == 5
    finally:
        engine.close()


@pytest.mark.gpu
def test_close_releases_engine_state_cuda():
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    before = torch.cuda.memory_allocated()
    engine = _kuhn_engine("cuda", iterations=10)
    engine.solve(iterations=10)
    engine.close()
    assert torch.cuda.memory_allocated() <= before + 1024 * 1024
    assert not engine.is_built
    assert engine.iterations == 0
    with pytest.raises(RuntimeError):
        engine.get_strategy()
    engine.close()

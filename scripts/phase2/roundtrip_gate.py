"""Phase 2A gate evidence: run from .venv-api.

For each GPU: spawn a .venv-engine worker, solve Kuhn through the client,
SIGKILL the worker, restart it, and solve again.
"""

import os
import signal
import subprocess
import sys

from holdem_solver.client import EngineClient, NotBuiltError, WorkerDiedError


def compute_pids(gpu: int) -> list[int]:
    out = subprocess.run(
        ["nvidia-smi", "-i", str(gpu), "--query-compute-apps=pid", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(int(pid) for pid in out.stdout.split())


def solve_kuhn(client: EngineClient, label: str) -> None:
    built = client.build("kuhn_poker", variant="cfr_plus", seed=42)
    result = client.solve(iterations=2000)
    strategy = client.get_strategy()
    row_sums = [float(row.sum()) for row in strategy.values()]
    print(
        f"  {label}: nodes={built.num_nodes} infosets={len(strategy)} iterations={result.total_iterations} "
        f"exploitability={result.exploitability:.9f} value_p0={result.profile_value_p0:.9f} "
        f"solve_s={result.elapsed_seconds:.3f} peak_reserved_mib={result.vram['peak_reserved_bytes'] / 2**20:.1f} "
        f"row_sum_min={min(row_sums):.6f} row_sum_max={max(row_sums):.6f}"
    )
    assert result.exploitability < 0.005 and abs(result.profile_value_p0 + 1 / 18) < 0.001


def main() -> int:
    print(f"client python {sys.version.split()[0]} ({sys.executable})")
    for gpu in (0, 1):
        with EngineClient(gpu) as client:
            info = client.ping()
            print(
                f"gpu{gpu}: worker pid={info['pid']} python={info['python']} torch={info['torch']} "
                f"CUDA_VISIBLE_DEVICES={info['cuda_visible_devices']} device={info['cuda_device_name']}"
            )
            solve_kuhn(client, "first solve ")
            old_pid = client.worker_pid
            os.kill(old_pid, signal.SIGKILL)
            try:
                client.solve(iterations=10)
            except WorkerDiedError as exc:
                print(f"  after SIGKILL: WorkerDiedError: {exc}")
            info = client.restart()
            print(f"  restarted: worker pid={info['pid']} state={info['state']} shm_files={os.listdir(client.shm_dir)}")
            try:
                client.get_strategy()
            except NotBuiltError as exc:
                print(f"  before rebuild: NotBuiltError: {exc}")
            solve_kuhn(client, "second solve")
            pids = compute_pids(gpu)
            print(f"  nvidia-smi compute pids on gpu{gpu}: {pids} (killed {old_pid} present: {old_pid in pids})")
            assert old_pid not in pids and info["pid"] in pids
        print(f"  shutdown: socket exists={os.path.exists(client.socket_path)}")
    print("GATE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

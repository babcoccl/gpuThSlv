#!/usr/bin/env bash
# Capture Phase 0 host facts. Commands are allowed to fail; statuses are printed.
set +e

section() {
    printf '\n=== %s ===\n' "$1"
}

section "uname -a"
uname -a
printf 'uname_status=%s\n' "$?"

section "nvidia-smi"
nvidia-smi
printf 'nvidia_smi_status=%s\n' "$?"

section "nvidia-smi query"
nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,compute_cap --format=csv
printf 'nvidia_smi_query_status=%s\n' "$?"

section "CUDA toolkit"
command -v nvcc || true
nvcc --version 2>&1
printf 'nvcc_status=%s\n' "$?"
ls -la /usr/local/cuda 2>&1 | head -40
printf 'cuda_dir_status=%s\n' "${PIPESTATUS[0]}"

section "Python versions"
command -v python3 || true
python3 --version 2>&1
printf 'python3_status=%s\n' "$?"
for p in python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8; do
    printf '%s: ' "$p"
    command -v "$p" || true
    if command -v "$p" >/dev/null 2>&1; then
        "$p" --version 2>&1
    fi
done

section "uv"
command -v uv || true
uv --version 2>&1
printf 'uv_status=%s\n' "$?"
uv python list --only-installed 2>&1 | head -80
printf 'uv_python_list_status=%s\n' "${PIPESTATUS[0]}"

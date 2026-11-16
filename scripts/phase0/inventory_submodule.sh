#!/usr/bin/env bash
# Inventory the vendored GPU-CFR submodule without modifying it.
set +e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUBMODULE="$ROOT_DIR/third_party/GPU-CFR"
cd "$SUBMODULE" || exit 1

section() {
    printf '\n=== %s ===\n' "$1"
}

section "submodule status"
git status --short
printf 'git_status=%s\n' "$?"
git rev-parse HEAD
printf 'rev_parse_status=%s\n' "$?"
git log -1 --format='%H%n%ad%n%s' --date=iso-strict
printf 'git_log_status=%s\n' "$?"

section "tracked files"
git ls-files | sort
printf 'tracked_file_count=%s\n' "$(git ls-files | wc -l)"

section "extension modules"
find gpu_cfr probes -type f -name '*.so' | sort
printf 'so_count=%s\n' "$(find gpu_cfr probes -type f -name '*.so' | wc -l)"
printf 'non_cpython39_so_count=%s\n' "$(find gpu_cfr probes -type f -name '*.so' ! -name '*cpython-39-x86_64-linux-gnu.so' | wc -l)"
find gpu_cfr probes -type f -name '*.so' ! -name '*cpython-39-x86_64-linux-gnu.so' | sort

section "license-like tracked files"
git ls-files | grep -Ei '(^|/)(license|licence|copying|notice)(\.|$)|license|licence|copying|notice' || true
printf 'license_file_search_status=%s\n' "$?"

section "license text search"
grep -RIl --exclude='*.so' --exclude='*.pyc' -E 'MIT License|Apache License|BSD|GPL|LGPL|license|License' . | sort | head -100
printf 'license_text_search_status=%s\n' "$?"

section "README license badge"
sed -n '1,8p' README.md

section "GitHub license API"
curl -fsSL https://api.github.com/repos/lbn187/GPU-CFR | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"full_name": d.get("full_name"), "license": d.get("license"), "private": d.get("private"), "default_branch": d.get("default_branch")}, indent=2))'
printf 'github_license_api_status=%s\n' "$?"

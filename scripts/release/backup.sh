#!/usr/bin/env bash
# Back up the job DB (jobs + saved spots tables) to
# $DATA_DIR/backups/<ts>/solves.db using SQLite's online backup so it is safe
# while the API is running. Strategy files are excluded — they are
# regenerable via `python -m holdem_solver.api.regen_evs`.
#
#   scripts/release/backup.sh [dest-dir]
source "$(dirname "$0")/common.sh"

DEST="${1:-$BACKUP_DIR/$(date +%Y%m%d-%H%M%S)}"
DB="$DATA_DIR/solves.db"
[ -f "$DB" ] || die "no job DB at $DB"

mkdir -p "$DEST"
# Online backup via the API venv's Python (any python3 with sqlite3 works).
PY="$CURRENT_LINK/.venv-api/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
"$PY" - "$DB" "$DEST/solves.db" <<'EOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)
EOF

cat > "$DEST/manifest.json" <<EOF
{"created_at": "$(date -Is)", "source_db": "$DB",
 "version": "$(basename "$(readlink -f "$CURRENT_LINK" 2>/dev/null)" 2>/dev/null || echo unknown)",
 "excludes": "strategy-*.npz / *.evs.npz (regenerable via regen_evs)"}
EOF
log "backup -> $DEST"
echo "$DEST"

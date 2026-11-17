#!/usr/bin/env bash
# Install the systemd *user* units (no sudo) and start them at boot.
#   deploy/install-user-units.sh            # install + enable + start
# Needs: .venv-api and .venv-engine built, and `loginctl enable-linger $USER`
# (done below; it may ask polkit for permission on some hosts).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONF="$HOME/.config/holdem-solver"
UNITS="$HOME/.config/systemd/user"
LOGDIR="${HOLDEM_LOG_DIR:-$HOME/.local/state/holdem-solver/logs}"
DATADIR="${HOLDEM_DATA_DIR:-$HOME/.local/share/holdem-solver}"
mkdir -p "$UNITS" "$CONF" "$LOGDIR" "$DATADIR"
chmod 700 "$CONF" "$LOGDIR" "$DATADIR"
if [ ! -f "$CONF/holdem.env" ]; then
  install -m 600 "$REPO/deploy/holdem.env.example" "$CONF/holdem.env"
  key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  sed -i "s|^HOLDEM_API_KEY=.*|HOLDEM_API_KEY=$key|" "$CONF/holdem.env"
  echo "wrote $CONF/holdem.env with a new API key"
fi
render() { sed -e "s|@REPO@|$REPO|g" -e "s|@LOGDIR@|$LOGDIR|g" -e "s|@DATADIR@|$DATADIR|g" "$1"; }
for unit in "$REPO"/deploy/systemd/user/*; do
  render "$unit" > "$UNITS/$(basename "$unit")"
done
render "$REPO/deploy/logrotate/holdem-solver" > "$REPO/deploy/logrotate/holdem-solver.rendered"
systemctl --user daemon-reload
systemctl --user enable holdem-solver.target holdem-api.service holdem-worker@0.service \
  holdem-worker@1.service holdem-logrotate.timer
loginctl enable-linger "$USER" || echo "WARNING: enable-linger failed; units start at login, not boot"
systemctl --user restart holdem-solver.target holdem-logrotate.timer
systemctl --user --no-pager status holdem-api.service holdem-worker@0.service holdem-worker@1.service | head -40

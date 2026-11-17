#!/bin/zsh
# Push new Trakt plays to Letterboxd. Called by Emacs `log-watched` after a
# successful Trakt sync; safe to run by hand.
#
# Runs on the push host (its machine-id is the one word in .letterboxd-host,
# which is gitignored so no hostname lands in this public repo). If that host
# is unreachable, runs here instead. The ledger is Syncthing-shared and
# Letterboxd's own diary is checked before every log, so a fallback run never
# double-logs.
set -u
cd "${0:A:h}"
uv=/opt/homebrew/bin/uv
host=$(cat .letterboxd-host 2>/dev/null)
here=$(cat ~/.config/machine-id 2>/dev/null)

if [[ -n "$host" && "$host" == "$here" ]]; then
  : # push host is this machine: fall through to the local run below
elif [[ -n "$host" ]]; then
  # Run the push over ssh and wait for it, so the exit code and output reach
  # Emacs (a launchctl kickstart returns before the push has done anything).
  # A login shell gives the remote the PATH that launchd would.
  if ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$host" true 2>/dev/null; then
    exec ssh -n -o BatchMode=yes "$host" \
      "zsh -lc 'cd ~/roaming/code/trakt-sync && $uv run -q letterboxd_push.py run'"
  fi
  echo "letterboxd-kick: $host unreachable, pushing from $here" >&2
fi
exec "$uv" run -q letterboxd_push.py run

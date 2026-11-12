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
label=com.marcosandrade.letterboxd-push
host=$(cat .letterboxd-host 2>/dev/null)
here=$(cat ~/.config/machine-id 2>/dev/null)

if [[ -n "$host" && "$host" == "$here" ]]; then
  launchctl kickstart "gui/$(id -u)/$label" && exit 0
elif [[ -n "$host" ]]; then
  ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$host" \
    "launchctl kickstart gui/\$(id -u)/$label" 2>/dev/null && exit 0
  echo "letterboxd-kick: $host unreachable, pushing from $here" >&2
fi
exec "$uv" run -q letterboxd_push.py run

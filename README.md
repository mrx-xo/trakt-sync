# Trakt Sync

Syncs your org files to [Trakt.tv](https://trakt.tv):

- `movies-watched.org` → Trakt **watch history** (with dates)
- `movies-to-watch.org` → Trakt **watchlist**

Your org files are the source of truth. This tool parses them, matches titles to Trakt movies, and pushes to your account.

## Setup

1. Create a `.env` file with your Trakt API credentials:

```
TRAKT_CLIENT_ID=your_client_id
TRAKT_CLIENT_SECRET=your_client_secret
```

You can get these by creating an app at https://trakt.tv/oauth/applications.

2. On first run, you'll be prompted to authorize via device code (go to a URL, enter a code). The token is cached in `token.json` and lasts ~90 days. Delete `token.json` to re-auth.

## How to Use

There are two ways to sync: the **Emacs UI** (recommended) or the **CLI**.

### Emacs UI (recommended)

This gives you a picker to manually confirm ambiguous matches.

```elisp
;; Sync watch history — parses org, searches Trakt, shows picker for unmatched titles
M-x trakt-sync

;; Sync watchlist — same picker flow but for movies-to-watch.org
M-x trakt-sync-watchlist

;; Push all previously mapped entries to Trakt (no re-matching)
M-x trakt-sync-commit

;; Test the UI with mock data (for debugging)
M-x trakt-sync-test-ui
```

The Elisp side lives at `~/.emacs.d/lisp/trakt-sync.el`.

### CLI

```bash
# Watch history — dry run
python3 sync.py

# Watch history — push to Trakt
python3 sync.py --commit

# Watchlist — dry run
python3 sync_watchlist.py

# Watchlist — push to Trakt
python3 sync_watchlist.py --commit
```

Note: the CLI scripts don't use mappings for ambiguous titles. Use the Emacs flow for reliable matching.

### Manually running the Emacs backend

The Emacs UI calls `sync_emacs.py` under the hood. You can also run its commands directly:

```bash
# Watch history commands
python3 sync_emacs.py candidates
python3 sync_emacs.py commit
python3 sync_emacs.py search "The Thing"

# Watchlist commands
python3 sync_emacs.py watchlist-candidates
python3 sync_emacs.py watchlist-commit
```

## Files

- `parse_org.py` — Parses `movies-watched.org` into structured entries
- `trakt_client.py` — Trakt API client (auth, search, history)
- `sync_emacs.py` — JSON interface used by the Emacs UI (history + watchlist)
- `sync.py` — Standalone CLI sync (watch history)
- `sync_watchlist.py` — Standalone CLI sync (watchlist)
- `mappings.json` — Watch history title-to-movie mappings
- `watchlist_mappings.json` — Watchlist title-to-movie mappings
- `token.json` — Cached OAuth token (delete to re-auth)
- `.env` — API credentials (not committed)
- `letterboxd_push.py` — Mirrors Trakt plays to the Letterboxd diary (see below)
- `launchd/com.marcosandrade.letterboxd-push.plist` — 30-minute timer for it

## Org File Formats

### movies-watched.org (watch history)

```org
--- 2025 ---
The Brutalist               01/15/25
Nosferatu                   01/20/25
The Substance               02/01/25
Sinners (2)                 03/10/25
```

- Dates can be `MM/DD/YY`, `MM/DD/YYYY`, or `MM/DD`
- Year separators (`--- 2025 ---`) set the year context
- Rewatch markers like `(2)` or `(x2)` log multiple watches

### movies-to-watch.org (watchlist)

```org
* Eraserhead
* Taxi Driver (1976)
* The Prestige
```

- Just `* Title` lines under org headings
- Optional year hint in parens for disambiguation

## Trakt Account

Currently syncing to: **mr-x00**

- History: https://trakt.tv/users/mr-x00/history/movies
- Watchlist: https://trakt.tv/users/mr-x00/watchlist

## Letterboxd mirror

Letterboxd has no open API, so `letterboxd_push.py` copies new Trakt plays into
the Letterboxd diary by driving a real Chromium window. Trakt stays the source
of truth: log in Emacs (`log-watched`) as usual. After the Trakt sync succeeds,
Emacs runs `letterboxd-kick.sh` in the background and the diary entry appears
within about 20 seconds. Entries carry the watched date and the rewatch flag;
no rating or like (the logger records neither). Nothing runs on a timer.

`letterboxd-kick.sh` runs the push on the **push host**, the machine whose
`~/.config/machine-id` is written in `.letterboxd-host` (gitignored, so no
hostname enters this public repo). The script runs the push there over ssh and
waits for it, so Emacs gets the exit code: a failed film raises a macOS
notification and an echo-area message, both pointing at the
` *trakt-sync-after-log*` buffer. If the push host is unreachable, the script
pushes from the local machine instead, which opens a Chromium window there for
a few seconds per film. The LaunchAgent `com.marcosandrade.letterboxd-push`
on the push host is kept for manual `launchctl kickstart` runs only. Duplicates are prevented twice over: the
`letterboxd_pushed.json` ledger (Trakt history id -> pushed, Syncthing-shared,
gitignored) and a check of the film's Letterboxd diary before every log.

Per-machine state, deliberately outside `~/roaming`:
`~/.local/state/letterboxd-push/` holds the browser `profile/` (the login),
`push.log` and `launchd.log`. Each machine that may push needs its own logged-in
profile.

```bash
uv run letterboxd_push.py status            # ledger counts + failing films
uv run letterboxd_push.py check             # is this machine's login still good? (read-only)
uv run letterboxd_push.py run --dry-run     # what a push would log
./letterboxd-kick.sh                        # push now (push host, else here)
uv run letterboxd_push.py push-one --tmdb 603 --date 2026-11-12   # log one film by hand
uv run letterboxd_push.py export-csv        # seeded plays as a Letterboxd import CSV
```

### Setup on a new machine

1. `uv run letterboxd_push.py login`: sign in with Remember me ticked, then
   quit the window with Cmd+Q. Or copy a working profile from another Mac with
   no Chromium running on either side:
   `rsync -a --exclude 'Singleton*' ~/.local/state/letterboxd-push/profile/ <host>:.local/state/letterboxd-push/profile/`
2. `uv run letterboxd_push.py check` should print `ok: logged in as ...`.
3. Push host only: copy `launchd/com.marcosandrade.letterboxd-push.plist` to
   `~/Library/LaunchAgents/`, `launchctl bootstrap gui/$(id -u)` it, and write
   its machine-id into `.letterboxd-host`.

### History before 2026-11-12

The 79 plays in Trakt on 2026-11-12 were seeded into the ledger as pushed
(`seed`), not logged by the browser. They reach Letterboxd through its CSV
import: `export-csv` writes them to `~/Downloads/letterboxd-backfill.csv` for
upload at `letterboxd.com/import`.

### Why it works this way (tested 2026-11-12)

- Headless Playwright loads pages (with a normal Chrome user agent) but
  Letterboxd gates logging in and logging a film behind a Cloudflare Turnstile
  check that fails under automation. A Chromium launched normally, which
  Playwright then attaches to over `--remote-debugging-port`, passes it.
- The login must be done by hand in a Chromium launched with
  `--use-mock-keychain` (the `login` command does this), with Remember me
  ticked. The mock keychain is also why the profile can be copied between Macs.
- Letterboxd's save request is invisible to Playwright's network events, so a
  push is verified by a new row on `/<user>/film/<slug>/diary/` with the right
  date. The same page makes it idempotent: a film already in the diary on that
  date is recorded as `already`, not logged twice.
- The diary modal is opened by clicking its Bootstrap trigger
  (`data-bs-target="#diary-entry-form-modal"`, compose mode) from script: a
  real click on `Log again...` gets intercepted by its dropdown.
- Film pages are reached by `letterboxd.com/tmdb/<id>/` (IMDb fallback), so no
  title matching.

### Troubleshooting

- `Letterboxd push failed` in Emacs (notification + echo area): read the
  ` *trakt-sync-after-log*` buffer; the push host's own copy of the output is
  in its `push.log`.
- `ABORT Letterboxd session is logged out`: that machine's login expired; redo
  setup step 1 there.
- `ABORT Cloudflare human check did not clear`: Cloudflare got stricter. Run
  `check` by hand; if it fails, the approach needs revisiting.
- `Locator.click: Timeout` on Save with the modal open and filled: Letterboxd
  animates the Save button (CSS transitions that stay `running`), so a
  Playwright click never sees it "stable". The script clicks it from page
  script for that reason (since 2026-11-17); do not switch back to a locator
  click.
- A failed film is retried on the next push, up to 5 times; `status` lists it.
  Fix it with `push-one`, then add its history id to `pushed` in the ledger.
- Trakt tokens last 7 days and are refreshed by whichever machine needs one
  first; `token.json` syncs via Syncthing. A refresh on two Macs within the
  same few seconds could leave one with a stale refresh token; re-auth with the
  Emacs `trakt-sync` if Trakt ever answers `invalid_grant`.

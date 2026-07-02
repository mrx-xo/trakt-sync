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

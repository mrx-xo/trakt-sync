# Trakt Sync

Syncs `~/roaming/notes/movies-watched.org` (the source of truth) to Trakt.tv as a mirror of watch history.

## Architecture

Two layers: Python backend (API + parsing) and Elisp frontend (Emacs UI).

### Python Scripts (this directory)

- `parse_org.py` — Parses `movies-watched.org` into `MovieEntry` dataclasses. Handles multiple date formats (`MM/DD/YY`, `MM/DD/YYYY`, `MM/DD`), year separators (`--- 2026 ---`), and rewatch markers (`(2)`, `(x2)`).
- `trakt_client.py` — Trakt API v2 client using only stdlib (`urllib`). Device code OAuth flow, movie search, watch history read/write. Needs `User-Agent` header to avoid Cloudflare 1010 blocks.
- `sync_emacs.py` — JSON interface for Emacs. Commands: `candidates` (parse + search + return matches), `save-mapping` (persist a title->movie choice), `search` (re-search Trakt), `commit` (push mapped entries to Trakt history).
- `letterboxd_push.py` — Mirrors new Trakt plays to the Letterboxd diary via a real Chromium window driven over CDP; started by `letterboxd-kick.sh`, which Emacs `log-watched` runs after a Trakt sync. Operation, rationale and troubleshooting: README `## Letterboxd mirror`.
- `sync.py` — Standalone CLI sync (dry-run by default, `--commit` to push). Doesn't use mappings — searches directly. Less reliable than the Emacs flow for ambiguous titles.

### Elisp (`~/.emacs.d/lisp/trakt-sync.el`)

- Full-screen picker UI with Evil integration (`suppress-keymap` + `evil-define-key` pattern).
- Poster images fetched synchronously from Trakt CDN (`media.trakt.tv`), cached in a hash table. Uses `ignore-errors` around `create-image` because some posters are HEIC format which Emacs may not support.
- JSON null values from `json-parse-string` come through as `:null` (truthy), not `nil`. All string fields from API data must be checked with `stringp` before `insert`.
- `M-x trakt-sync` — full sync flow. `M-x trakt-sync-test-ui` — mock data for UI iteration. `M-x trakt-sync-commit` — push mapped entries without re-matching.
- Saves/restores window configuration on entry/exit.

## Data Files

- `.env` — `TRAKT_CLIENT_ID` and `TRAKT_CLIENT_SECRET`. Loaded by `trakt_client.py` at import time.
- `token.json` — Cached OAuth access token. Delete to re-authenticate.
- `mappings.json` — Persisted title->movie mappings. Keys are lowercased org titles. Values are Trakt movie objects with `ids`, `title`, `year`, `slug`, `poster`, `overview`.

## Sync Flow

1. `parse_org.py` reads org file into `MovieEntry` list
2. Each entry is checked against `mappings.json` (already mapped?) and Trakt history (already synced?)
3. Unmapped entries get searched on Trakt; exact title matches auto-map
4. Remaining unmatched entries are presented in the Emacs picker for manual selection
5. On commit, mapped entries are pushed to Trakt via `POST /sync/history` with `watched_at` dates

## API Notes

- Trakt API requires `trakt-api-version: 2` and `trakt-api-key: <client_id>` headers on all requests.
- Search uses `extended=full` to get images and metadata in results.
- Poster URLs from Trakt don't include `https://` prefix — `sync_emacs.py:poster_url()` prepends it.
- Auth tokens expire after ~90 days. Delete `token.json` to re-auth.
- Free tier: 250 watchlist items, 100k history items.

## Known Issues / WIP

- Poster display in Emacs needs verification — sync fetch works but the render flow was being debugged.
- Some image formats (HEIC) from Trakt CDN crash `create-image` — handled with `ignore-errors` fallback.
- The standalone `sync.py` doesn't use mappings, so ambiguous titles (e.g., "X", "Him", "Sinners") match incorrectly. Use the Emacs flow for reliable matching.

## Common Commands

```bash
# Dry run from CLI
python3 sync.py

# Push from CLI (uses direct search, not mappings — less reliable)
python3 sync.py --commit

# Delete to re-authenticate
rm token.json

# Reset all mappings
rm mappings.json
```

```elisp
;; Full interactive sync
M-x trakt-sync

;; Test UI with mock data
M-x trakt-sync-test-ui

;; Push all mapped entries without re-matching
M-x trakt-sync-commit
```

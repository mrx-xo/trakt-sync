#!/usr/bin/env python3
"""Emacs-facing interface for trakt-sync.

Commands (passed as first arg):
    candidates           — parse org, search Trakt, output JSON with matches to confirm
    commit               — read mappings.json, sync confirmed entries to Trakt history
    search               — search Trakt for a query (arg2), output JSON results
    save-mapping         — persist a title->movie choice (mappings.json)
    watchlist-candidates — parse movies-to-watch.org, search Trakt, output JSON
    watchlist-commit     — push mapped entries to Trakt watchlist
    watchlist-save       — persist a title->movie choice (watchlist_mappings.json)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from parse_org import parse_org_file
from trakt_client import (
    authenticate, search_movies, get_watched_history, add_to_history,
    get_watchlist, add_to_watchlist, remove_from_watchlist, lookup_movie_by_slug,
)

SCRIPT_DIR = Path(__file__).parent
DEFAULT_ORG = (SCRIPT_DIR / "../../notes/movies-watched.org").resolve()
DEFAULT_WATCHLIST_ORG = (SCRIPT_DIR / "../../notes/movies-to-watch.org").resolve()
MAPPINGS_FILE = SCRIPT_DIR / "mappings.json"
WATCHLIST_MAPPINGS_FILE = SCRIPT_DIR / "watchlist_mappings.json"


def load_mappings(path: Path = MAPPINGS_FILE) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_mappings(mappings: dict, path: Path = MAPPINGS_FILE):
    path.write_text(json.dumps(mappings, indent=2))


def poster_url(movie: dict) -> str | None:
    images = movie.get("images", {})
    posters = images.get("poster", [])
    if posters:
        url = posters[0]
        if not url.startswith("http"):
            url = "https://" + url
        return url
    return None


def cmd_candidates(org_path: str):
    """Output JSON: list of org entries with search candidates and mapping status."""
    entries = parse_org_file(org_path)
    mappings = load_mappings()
    token = authenticate()

    # Get existing Trakt history
    watched = get_watched_history(token)
    watched_ids = set()
    for w in watched:
        tid = w.get("movie", {}).get("ids", {}).get("trakt")
        if tid:
            watched_ids.add(tid)

    output = []
    for entry in entries:
        key = entry.title.lower().strip()
        item = {
            "org_title": entry.title,
            "watched_date": str(entry.watched_date) if entry.watched_date else None,
            "rewatch": entry.rewatch,
            "rewatch_count": entry.rewatch_count,
        }

        # Already mapped?
        if key in mappings:
            mapped = mappings[key]
            item["status"] = "mapped"
            item["mapped_to"] = mapped
            # Check if already on Trakt
            if mapped.get("ids", {}).get("trakt") in watched_ids:
                item["status"] = "synced"
            output.append(item)
            continue

        # Search Trakt
        results = search_movies(entry.title, limit=5)
        candidates = []
        for movie in results:
            candidates.append({
                "title": movie.get("title", ""),
                "year": movie.get("year"),
                "overview": (movie.get("overview") or "")[:200],
                "poster": poster_url(movie),
                "ids": movie.get("ids", {}),
                "slug": movie.get("ids", {}).get("slug", ""),
            })

        # Auto-match: exact title match (case-insensitive)
        auto = None
        for c in candidates:
            if c["title"].lower().strip() == key:
                auto = c
                break

        if auto:
            item["status"] = "auto"
            item["mapped_to"] = auto
            # Save auto-match
            mappings[key] = auto
        else:
            item["status"] = "unmatched"

        item["candidates"] = candidates
        output.append(item)

    save_mappings(mappings)
    print(json.dumps(output, indent=2))


def cmd_save_mapping(org_title: str, movie_json: str):
    """Save a single mapping choice."""
    mappings = load_mappings()
    key = org_title.lower().strip()
    movie = json.loads(movie_json)
    mappings[key] = movie
    save_mappings(mappings)
    print(json.dumps({"ok": True, "title": org_title, "mapped_to": movie.get("title", "")}))


def cmd_search(query: str):
    """Search Trakt and output results as JSON."""
    authenticate()  # ensure token
    results = search_movies(query, limit=5)
    output = []
    for movie in results:
        output.append({
            "title": movie.get("title", ""),
            "year": movie.get("year"),
            "overview": (movie.get("overview") or "")[:200],
            "poster": poster_url(movie),
            "ids": movie.get("ids", {}),
            "slug": movie.get("ids", {}).get("slug", ""),
        })
    print(json.dumps(output, indent=2))


def cmd_lookup(url_or_slug: str):
    """Look up a movie by Trakt URL or slug, output as JSON candidate."""
    import re
    authenticate()
    # Extract slug from URL like trakt.tv/movies/threads-1984
    m = re.search(r'trakt\.tv/movies/([a-z0-9-]+)', url_or_slug)
    slug = m.group(1) if m else url_or_slug.strip().strip('/')
    movie = lookup_movie_by_slug(slug)
    if not movie:
        print(json.dumps([]))
        return
    print(json.dumps([{
        "title": movie.get("title", ""),
        "year": movie.get("year"),
        "overview": (movie.get("overview") or "")[:200],
        "poster": poster_url(movie),
        "ids": movie.get("ids", {}),
        "slug": movie.get("ids", {}).get("slug", ""),
    }], indent=2))


def cmd_commit(org_path: str):
    """Sync all mapped entries to Trakt."""
    entries = parse_org_file(org_path)
    mappings = load_mappings()
    token = authenticate()

    watched = get_watched_history(token)
    watched_ids = set()
    for w in watched:
        tid = w.get("movie", {}).get("ids", {}).get("trakt")
        if tid:
            watched_ids.add(tid)

    to_sync = []
    for entry in entries:
        key = entry.title.lower().strip()
        if key not in mappings:
            continue
        mapped = mappings[key]
        trakt_id = mapped.get("ids", {}).get("trakt")
        if trakt_id in watched_ids:
            continue

        hist = {"ids": mapped["ids"]}
        if entry.watched_date:
            dt = datetime(entry.watched_date.year, entry.watched_date.month,
                          entry.watched_date.day, 12, 0, 0, tzinfo=timezone.utc)
            hist["watched_at"] = dt.isoformat()
        to_sync.append(hist)

        if entry.rewatch and entry.rewatch_count:
            for _ in range(entry.rewatch_count - 1):
                to_sync.append(hist)

    if not to_sync:
        print(json.dumps({"added": 0, "message": "Nothing to sync"}))
        return

    result = add_to_history(to_sync, token)
    added = result.get("added", {}).get("movies", 0)
    print(json.dumps({"added": added, "message": f"Synced {added} movies to Trakt"}))


def parse_watchlist_org(org_path: str) -> list[dict]:
    """Parse movies-to-watch.org into a list of {title, year} dicts."""
    import re
    entries = []
    for line in Path(org_path).read_text().splitlines():
        line = line.strip()
        if not line.startswith("* "):
            continue
        raw = line[2:].strip()
        m = re.match(r'^(.+?)\s*\((\d{4})\)\s*$', raw)
        if m:
            entries.append({"title": m.group(1).strip(), "year": int(m.group(2))})
        else:
            entries.append({"title": raw, "year": None})
    return entries


def cmd_watchlist_candidates(org_path: str):
    """Output JSON: list of watchlist entries with search candidates."""
    entries = parse_watchlist_org(org_path)
    mappings = load_mappings(WATCHLIST_MAPPINGS_FILE)
    token = authenticate()

    existing = get_watchlist(token)
    existing_ids = set()
    for item in existing:
        tid = item.get("movie", {}).get("ids", {}).get("trakt")
        if tid:
            existing_ids.add(tid)

    output = []
    for entry in entries:
        key = entry["title"].lower().strip()
        item = {
            "org_title": entry["title"],
            "watched_date": None,
        }

        if key in mappings:
            mapped = mappings[key]
            item["status"] = "mapped"
            item["mapped_to"] = mapped
            if mapped.get("ids", {}).get("trakt") in existing_ids:
                item["status"] = "synced"
            output.append(item)
            continue

        results = search_movies(entry["title"], year=entry.get("year"), limit=5)
        candidates = []
        for movie in results:
            candidates.append({
                "title": movie.get("title", ""),
                "year": movie.get("year"),
                "overview": (movie.get("overview") or "")[:200],
                "poster": poster_url(movie),
                "ids": movie.get("ids", {}),
                "slug": movie.get("ids", {}).get("slug", ""),
            })

        auto = None
        for c in candidates:
            if c["title"].lower().strip() == key:
                auto = c
                break

        if auto:
            item["status"] = "auto"
            item["mapped_to"] = auto
            mappings[key] = auto
        else:
            item["status"] = "unmatched"

        item["candidates"] = candidates
        output.append(item)

    save_mappings(mappings, WATCHLIST_MAPPINGS_FILE)
    print(json.dumps(output, indent=2))


def cmd_watchlist_save(org_title: str, movie_json: str):
    """Save a single watchlist mapping choice."""
    mappings = load_mappings(WATCHLIST_MAPPINGS_FILE)
    key = org_title.lower().strip()
    movie = json.loads(movie_json)
    mappings[key] = movie
    save_mappings(mappings, WATCHLIST_MAPPINGS_FILE)
    print(json.dumps({"ok": True, "title": org_title, "mapped_to": movie.get("title", "")}))


def cmd_watchlist_commit(org_path: str):
    """Push all mapped watchlist entries to Trakt watchlist."""
    entries = parse_watchlist_org(org_path)
    mappings = load_mappings(WATCHLIST_MAPPINGS_FILE)
    token = authenticate()

    existing = get_watchlist(token)
    existing_ids = set()
    for item in existing:
        tid = item.get("movie", {}).get("ids", {}).get("trakt")
        if tid:
            existing_ids.add(tid)

    to_add = []
    for entry in entries:
        key = entry["title"].lower().strip()
        if key not in mappings:
            continue
        mapped = mappings[key]
        trakt_id = mapped.get("ids", {}).get("trakt")
        if trakt_id in existing_ids:
            continue
        to_add.append({"ids": mapped["ids"]})

    if not to_add:
        print(json.dumps({"added": 0, "message": "Nothing to sync"}))
        return

    result = add_to_watchlist(to_add, token)
    added = result.get("added", {}).get("movies", 0)
    print(json.dumps({"added": added, "message": f"Added {added} movies to watchlist"}))


def cmd_watchlist_unmapped(org_path: str):
    """List watchlist titles that have no mapping."""
    entries = parse_watchlist_org(org_path)
    mappings = load_mappings(WATCHLIST_MAPPINGS_FILE)
    unmapped = [e["title"] for e in entries if e["title"].lower().strip() not in mappings]
    print(json.dumps(unmapped))


def cmd_history_unmapped(org_path: str):
    """List history titles that have no mapping."""
    entries = parse_org_file(org_path)
    mappings = load_mappings()
    unmapped = [e.title for e in entries if e.title.lower().strip() not in mappings]
    print(json.dumps(unmapped))


def cmd_log_watched(movie_json: str, date_str: str):
    """Log a movie as watched: add to Trakt history, remove from watchlist if present."""
    movie = json.loads(movie_json)
    token = authenticate()

    # Add to Trakt history
    hist = {"ids": movie["ids"]}
    if date_str:
        parts = date_str.split("/")
        month, day = int(parts[0]), int(parts[1])
        year = int(parts[2])
        if year < 100:
            year += 2000
        dt = datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)
        hist["watched_at"] = dt.isoformat()

    result = add_to_history([hist], token)
    added = result.get("added", {}).get("movies", 0)

    # Remove from Trakt watchlist if present
    watchlist = get_watchlist(token)
    trakt_id = movie.get("ids", {}).get("trakt")
    on_watchlist = any(
        w.get("movie", {}).get("ids", {}).get("trakt") == trakt_id
        for w in watchlist
    )
    removed = 0
    if on_watchlist:
        rm_result = remove_from_watchlist([{"ids": movie["ids"]}], token)
        removed = rm_result.get("deleted", {}).get("movies", 0)

    # Save to history mappings
    title = movie.get("title", "")
    mappings = load_mappings()
    mappings[title.lower().strip()] = {
        "title": title,
        "year": movie.get("year"),
        "ids": movie.get("ids", {}),
        "slug": movie.get("ids", {}).get("slug", ""),
        "poster": movie.get("poster"),
        "overview": movie.get("overview"),
    }
    save_mappings(mappings)

    msg = "Logged as watched"
    if removed:
        msg += ", removed from watchlist"
    print(json.dumps({
        "added": added,
        "removed_from_watchlist": removed,
        "title": title,
        "message": msg,
    }))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "candidates"

    if cmd == "candidates":
        path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_ORG)
        cmd_candidates(path)
    elif cmd == "save-mapping":
        cmd_save_mapping(sys.argv[2], sys.argv[3])
    elif cmd == "search":
        cmd_search(sys.argv[2])
    elif cmd == "lookup":
        cmd_lookup(sys.argv[2])
    elif cmd == "commit":
        path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_ORG)
        cmd_commit(path)
    elif cmd == "watchlist-candidates":
        path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_WATCHLIST_ORG)
        cmd_watchlist_candidates(path)
    elif cmd == "watchlist-save":
        cmd_watchlist_save(sys.argv[2], sys.argv[3])
    elif cmd == "watchlist-commit":
        path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_WATCHLIST_ORG)
        cmd_watchlist_commit(path)
    elif cmd == "watchlist-unmapped":
        path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_WATCHLIST_ORG)
        cmd_watchlist_unmapped(path)
    elif cmd == "history-unmapped":
        path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_ORG)
        cmd_history_unmapped(path)
    elif cmd == "log-watched":
        cmd_log_watched(sys.argv[2], sys.argv[3])
    else:
        print(json.dumps({"error": f"Unknown command: {cmd}"}), file=sys.stderr)
        sys.exit(1)

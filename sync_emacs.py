#!/usr/bin/env python3
"""Emacs-facing interface for trakt-sync.

Commands (passed as first arg):
    candidates  — parse org, search Trakt, output JSON with matches to confirm
    commit      — read mappings.json, sync confirmed entries to Trakt
    search      — search Trakt for a query (arg2), output JSON results
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from parse_org import parse_org_file
from trakt_client import (
    authenticate, search_movies, get_watched_history, add_to_history
)

SCRIPT_DIR = Path(__file__).parent
DEFAULT_ORG = (SCRIPT_DIR / "../../notes/movies-watched.org").resolve()
MAPPINGS_FILE = SCRIPT_DIR / "mappings.json"


def load_mappings() -> dict:
    if MAPPINGS_FILE.exists():
        return json.loads(MAPPINGS_FILE.read_text())
    return {}


def save_mappings(mappings: dict):
    MAPPINGS_FILE.write_text(json.dumps(mappings, indent=2))


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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "candidates"

    if cmd == "candidates":
        path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_ORG)
        cmd_candidates(path)
    elif cmd == "save-mapping":
        cmd_save_mapping(sys.argv[2], sys.argv[3])
    elif cmd == "search":
        cmd_search(sys.argv[2])
    elif cmd == "commit":
        path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_ORG)
        cmd_commit(path)
    else:
        print(json.dumps({"error": f"Unknown command: {cmd}"}), file=sys.stderr)
        sys.exit(1)

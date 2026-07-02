#!/usr/bin/env python3
"""Sync movies-to-watch.org to Trakt watchlist.

Usage:
    python3 sync_watchlist.py              # dry run
    python3 sync_watchlist.py --commit     # push to Trakt
"""

import json
import re
import sys
from pathlib import Path

from trakt_client import authenticate, search_movies, get_watchlist, add_to_watchlist

SCRIPT_DIR = Path(__file__).parent
DEFAULT_ORG = (SCRIPT_DIR / "../../notes/movies-to-watch.org").resolve()
MAPPINGS_FILE = SCRIPT_DIR / "watchlist_mappings.json"


def parse_watchlist(org_path: str) -> list[dict]:
    """Parse movies-to-watch.org into a list of {title, year?} dicts."""
    entries = []
    for line in Path(org_path).read_text().splitlines():
        line = line.strip()
        if not line.startswith("* "):
            continue
        raw = line[2:].strip()
        # Extract year hint like "Taxi Driver (1976)"
        m = re.match(r'^(.+?)\s*\((\d{4})\)\s*$', raw)
        if m:
            entries.append({"title": m.group(1).strip(), "year": int(m.group(2))})
        else:
            entries.append({"title": raw, "year": None})
    return entries


def load_mappings() -> dict:
    if MAPPINGS_FILE.exists():
        return json.loads(MAPPINGS_FILE.read_text())
    return {}


def save_mappings(mappings: dict):
    MAPPINGS_FILE.write_text(json.dumps(mappings, indent=2))


def main():
    commit = "--commit" in sys.argv
    org_path = DEFAULT_ORG
    for arg in sys.argv[1:]:
        if arg != "--commit":
            org_path = arg

    entries = parse_watchlist(str(org_path))
    print(f"Parsed {len(entries)} movies from {org_path}\n")

    token = authenticate()
    mappings = load_mappings()

    # Get existing watchlist
    existing = get_watchlist(token)
    existing_ids = set()
    for item in existing:
        tid = item.get("movie", {}).get("ids", {}).get("trakt")
        if tid:
            existing_ids.add(tid)
    print(f"Already on Trakt watchlist: {len(existing_ids)} movies\n")

    to_add = []
    unmatched = []

    for entry in entries:
        key = entry["title"].lower().strip()

        # Already mapped?
        if key in mappings:
            mapped = mappings[key]
            trakt_id = mapped.get("ids", {}).get("trakt")
            if trakt_id in existing_ids:
                print(f"  [skip] {entry['title']} — already on watchlist")
                continue
            to_add.append(mapped)
            print(f"  [add]  {entry['title']} -> {mapped['title']} ({mapped.get('year', '?')})")
            continue

        # Search Trakt
        results = search_movies(entry["title"], year=entry.get("year"), limit=5)
        if not results:
            print(f"  [???]  {entry['title']} — no results found")
            unmatched.append(entry)
            continue

        # Auto-match: exact title match (case-insensitive)
        match = None
        for r in results:
            if r.get("title", "").lower().strip() == key:
                match = r
                break

        # Fall back to first result if no exact match
        if not match:
            match = results[0]
            print(f"  [~]    {entry['title']} -> {match['title']} ({match.get('year', '?')}) (best guess)")
        else:
            print(f"  [add]  {entry['title']} -> {match['title']} ({match.get('year', '?')})")

        # Save mapping
        mapping_data = {
            "title": match.get("title", ""),
            "year": match.get("year"),
            "ids": match.get("ids", {}),
            "slug": match.get("ids", {}).get("slug", ""),
        }
        mappings[key] = mapping_data

        trakt_id = mapping_data.get("ids", {}).get("trakt")
        if trakt_id in existing_ids:
            print(f"         ^ already on watchlist, skipping")
            continue

        to_add.append(mapping_data)

    save_mappings(mappings)

    if unmatched:
        print(f"\n{len(unmatched)} unmatched titles:")
        for u in unmatched:
            print(f"  - {u['title']}")

    print(f"\n{len(to_add)} movies to add to watchlist")

    if not to_add:
        print("Nothing to sync.")
        return

    if not commit:
        print("\nDry run — use --commit to push to Trakt")
        return

    movies_payload = [{"ids": m["ids"]} for m in to_add]
    result = add_to_watchlist(movies_payload, token)
    added = result.get("added", {}).get("movies", 0)
    existing_count = result.get("existing", {}).get("movies", 0)
    not_found = result.get("not_found", {}).get("movies", [])
    print(f"\nDone! Added: {added}, Already existed: {existing_count}")
    if not_found:
        print(f"Not found on Trakt: {len(not_found)}")


if __name__ == "__main__":
    main()

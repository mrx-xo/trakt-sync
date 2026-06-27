#!/usr/bin/env python3
"""Sync movies-watched.org → Trakt watch history.

Usage:
    python sync.py                          # dry run (default)
    python sync.py --commit                 # actually sync to Trakt
    python sync.py --file /path/to/file.org # custom org file path
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from parse_org import parse_org_file, MovieEntry
from trakt_client import authenticate, search_movie, get_watched_history, add_to_history

DEFAULT_ORG = Path(__file__).parent / "../../notes/movies-watched.org"


def find_trakt_movie(entry: MovieEntry) -> dict | None:
    """Search Trakt for a movie matching the org entry."""
    # Try with year from watched_date if available
    year = entry.watched_date.year if entry.watched_date else None
    # Don't pass watched year as movie release year — they're different
    movie = search_movie(entry.title)
    if movie:
        return movie
    return None


def is_already_synced(movie: dict, watched_set: set) -> bool:
    """Check if a trakt movie ID is already in the watched history."""
    trakt_id = movie.get("ids", {}).get("trakt")
    return trakt_id in watched_set


def build_history_entry(movie: dict, entry: MovieEntry) -> dict:
    """Build a Trakt sync/history movie object."""
    h = {"ids": movie["ids"]}
    if entry.watched_date:
        # Set watched_at to noon UTC on that date
        dt = datetime(entry.watched_date.year, entry.watched_date.month,
                      entry.watched_date.day, 12, 0, 0, tzinfo=timezone.utc)
        h["watched_at"] = dt.isoformat()
    return h


def sync(org_path: str, commit: bool = False):
    print(f"Parsing: {org_path}\n")
    entries = parse_org_file(org_path)
    print(f"Found {len(entries)} movies in org file.\n")

    # Authenticate
    token = authenticate()

    # Get existing watched history to avoid duplicates
    print("Fetching existing Trakt history...")
    watched = get_watched_history(token)
    watched_ids = set()
    for w in watched:
        trakt_id = w.get("movie", {}).get("ids", {}).get("trakt")
        if trakt_id:
            watched_ids.add(trakt_id)
    print(f"  {len(watched_ids)} movies already on Trakt.\n")

    # Match and prepare
    to_sync = []
    skipped = []
    not_found = []

    for entry in entries:
        movie = find_trakt_movie(entry)
        if not movie:
            not_found.append(entry)
            continue

        trakt_id = movie.get("ids", {}).get("trakt")
        title = movie.get("title", entry.title)
        year = movie.get("year", "")

        if is_already_synced(movie, watched_ids):
            skipped.append((entry, movie))
            continue

        hist = build_history_entry(movie, entry)
        to_sync.append((entry, movie, hist))

        # For rewatches, add an extra entry (the rewatch itself)
        if entry.rewatch and entry.rewatch_count:
            for _ in range(entry.rewatch_count - 1):
                to_sync.append((entry, movie, hist))

    # Report
    if not_found:
        print("NOT FOUND on Trakt:")
        for e in not_found:
            print(f"  - {e.title}")
        print()

    if skipped:
        print(f"ALREADY SYNCED ({len(skipped)}):")
        for entry, movie in skipped:
            print(f"  - {movie['title']} ({movie.get('year', '?')})")
        print()

    if to_sync:
        print(f"TO SYNC ({len(to_sync)}):")
        for entry, movie, _ in to_sync:
            date_str = str(entry.watched_date) if entry.watched_date else "no date"
            rewatch_str = " [rewatch]" if entry.rewatch else ""
            print(f"  + {movie['title']} ({movie.get('year', '?')}) — {date_str}{rewatch_str}")
        print()

    if not to_sync:
        print("Nothing new to sync!")
        return

    if not commit:
        print("DRY RUN — pass --commit to actually sync to Trakt.")
        return

    # Sync!
    print("Syncing to Trakt...")
    history_entries = [h for _, _, h in to_sync]
    result = add_to_history(history_entries, token)
    added = result.get("added", {}).get("movies", 0)
    print(f"Done! Added {added} movies to Trakt history.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync movies-watched.org to Trakt")
    parser.add_argument("--commit", action="store_true", help="Actually sync (default is dry run)")
    parser.add_argument("--file", default=str(DEFAULT_ORG.resolve()), help="Path to org file")
    args = parser.parse_args()
    sync(args.file, args.commit)

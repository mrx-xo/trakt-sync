"""Parse movies-watched.org into structured movie entries."""

import re
from dataclasses import dataclass
from datetime import date


@dataclass
class MovieEntry:
    title: str
    watched_date: date | None = None
    rewatch: bool = False
    rewatch_count: int | None = None

    def __repr__(self):
        parts = [f"'{self.title}'"]
        if self.watched_date:
            parts.append(str(self.watched_date))
        if self.rewatch:
            parts.append(f"rewatch(x{self.rewatch_count or 2})")
        return f"Movie({', '.join(parts)})"


def parse_date(raw: str, fallback_year: int) -> date | None:
    """Parse the various date formats found in the org file."""
    raw = raw.strip()
    if not raw:
        return None

    # Try common formats
    formats = [
        # 02/18/25, 8/13/25, 06/26/26
        (r"^(\d{1,2})/(\d{1,2})/(\d{2})$", lambda m: date(
            2000 + int(m.group(3)), int(m.group(1)), int(m.group(2))
        )),
        # 01/10/2026, 05/09/2026
        (r"^(\d{1,2})/(\d{1,2})/(\d{4})$", lambda m: date(
            int(m.group(3)), int(m.group(1)), int(m.group(2))
        )),
        # 07/07 (no year — use fallback)
        (r"^(\d{1,2})/(\d{1,2})$", lambda m: date(
            fallback_year, int(m.group(1)), int(m.group(2))
        )),
    ]

    for pattern, builder in formats:
        m = re.match(pattern, raw)
        if m:
            return builder(m)

    return None


def parse_org_file(filepath: str) -> list[MovieEntry]:
    """Parse the movies-watched.org file into MovieEntry objects."""
    entries = []
    fallback_year = 2025  # default until we see a year separator

    with open(filepath) as f:
        for line in f:
            line = line.strip()

            # Year separator line like "---- 2026 ----"
            year_match = re.search(r"\b(20\d{2})\b", line)
            if year_match and not line.startswith("*"):
                fallback_year = int(year_match.group(1))
                continue

            # Org heading = movie entry
            if not line.startswith("* "):
                continue

            text = line[2:].strip()

            # Split on " - " to separate title from date
            # But be careful with titles containing " - " (e.g. "Bad Lieutenant: Port of Call New Orleans")
            # The date part, if present, is always the last segment after " - "
            date_str = None
            title = text
            if " - " in text:
                parts = text.rsplit(" - ", 1)
                candidate = parts[1].strip()
                if re.match(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?$", candidate):
                    title = parts[0].strip()
                    date_str = candidate

            # Check for rewatch markers: (2), (x2), (x3), etc.
            rewatch = False
            rewatch_count = None
            rewatch_match = re.search(r"\((?:x)?(\d+)\)\s*$", title)
            if rewatch_match:
                num = int(rewatch_match.group(1))
                if num >= 2:
                    rewatch = True
                    rewatch_count = num
                    title = title[:rewatch_match.start()].strip()

            watched = parse_date(date_str, fallback_year) if date_str else None

            entries.append(MovieEntry(
                title=title,
                watched_date=watched,
                rewatch=rewatch,
                rewatch_count=rewatch_count,
            ))

    return entries


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "../../notes/movies-watched.org"
    for entry in parse_org_file(path):
        print(entry)

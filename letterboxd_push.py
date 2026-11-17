# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright==1.65.1"]
# ///
"""Mirror Trakt watch history to the Letterboxd diary.

Trakt is the source of truth (Emacs `log-watched` writes there). Each run
reads Trakt history, and logs every play not yet pushed as a Letterboxd
diary entry by driving a real Chromium window over the DevTools port.

Headless Playwright cannot do this: Letterboxd gates logging behind a
Cloudflare Turnstile check that fails under automation. A normally launched
Chromium that Playwright attaches to afterwards passes it.

Commands (run with `uv run letterboxd_push.py <command>`):
  login   open Chromium at the sign-in page; sign in with Remember me, then Cmd+Q
  seed    mark every play currently in Trakt history as already pushed
  run     push new plays (default). --dry-run lists them without a browser.
  status  show ledger counts and failures
  check   confirm the saved login works (read-only)
  export-csv  write seeded plays as a Letterboxd import CSV (one-time backfill)
  push-one --tmdb ID --date YYYY-MM-DD   log one film by hand (not ledgered)

Nothing runs on a timer: Emacs `log-watched` calls letterboxd-kick.sh after a
successful Trakt sync, which runs this on the push host (or locally if that
host is unreachable).

The pushed-plays ledger sits next to this script so it syncs (Syncthing) and
every machine agrees on what is already on Letterboxd. The browser profile,
lock and logs stay per machine in ~/.local/state/letterboxd-push/ (a live
browser profile must never be Syncthing-synced).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import trakt_client  # noqa: E402

STATE = Path.home() / ".local/state/letterboxd-push"
PROFILE = STATE / "profile"
LEDGER = Path(__file__).parent / "letterboxd_pushed.json"
LOG = STATE / "push.log"
LOCK = STATE / "lock"
MAX_ATTEMPTS = 5
BASE = "https://letterboxd.com"


class Abort(Exception):
    """Stop the whole run (login lost, Cloudflare challenge)."""


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


# --- Ledger ---------------------------------------------------------------

def load_ledger() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"pushed": {}, "failed": {}}


def save_ledger(ledger: dict) -> None:
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True))
    tmp.replace(LEDGER)


# --- Trakt ----------------------------------------------------------------

def trakt_history() -> list[dict]:
    """All movie plays, oldest first. Never starts an interactive auth."""
    if not trakt_client.TOKEN_FILE.exists():
        raise Abort("no Trakt token.json; run the Emacs trakt-sync once to authorise")
    token = trakt_client.authenticate()
    plays, page = [], 1
    while True:
        status, data = trakt_client._request(
            "GET", f"/sync/history/movies?page={page}&limit=1000", access_token=token)
        if status != 200:
            raise Abort(f"Trakt history request failed: {status} {data}")
        plays.extend(data or [])
        if not data or len(data) < 1000:
            break
        page += 1
    plays.sort(key=lambda h: h["watched_at"])
    return plays


def play_to_entry(play: dict, earlier_trakt_ids: set) -> dict:
    movie = play["movie"]
    watched = datetime.fromisoformat(play["watched_at"].replace("Z", "+00:00")).astimezone()
    return {
        "history_id": str(play["id"]),
        "title": movie["title"],
        "year": movie.get("year"),
        "tmdb": movie["ids"].get("tmdb"),
        "imdb": movie["ids"].get("imdb"),
        "date": watched.strftime("%Y-%m-%d"),
        "rewatch": movie["ids"]["trakt"] in earlier_trakt_ids,
    }


def pending_entries(plays: list[dict], ledger: dict) -> list[dict]:
    seen, out = set(), []
    for play in plays:
        entry = play_to_entry(play, seen)
        seen.add(play["movie"]["ids"]["trakt"])
        hid = entry["history_id"]
        if hid in ledger["pushed"]:
            continue
        if ledger["failed"].get(hid, {}).get("attempts", 0) >= MAX_ATTEMPTS:
            continue
        out.append(entry)
    return out


# --- Browser --------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chromium_path() -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        return p.chromium.executable_path


def launch_chromium(url: str, port: int | None = None) -> subprocess.Popen:
    """Start Chromium normally (no automation flags). Same mock keychain as
    Playwright so cookies written here stay readable either way."""
    args = [chromium_path(), f"--user-data-dir={PROFILE}", "--use-mock-keychain",
            "--no-first-run", "--no-default-browser-check", "--window-size=1400,1000"]
    if port:
        args.append(f"--remote-debugging-port={port}")
    args.append(url)
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_devtools(port: int, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
            return
        except OSError:
            time.sleep(0.5)
    raise Abort("Chromium DevTools port never came up")


def challenge_visible(pg) -> bool:
    return (pg.locator("iframe[src*='challenges.cloudflare.com']").count() > 0
            or pg.get_by_text("Verify you are human").count() > 0)


def wait_out_challenge(pg, seconds: int = 20) -> None:
    for _ in range(seconds):
        if not challenge_visible(pg):
            return
        pg.wait_for_timeout(1000)
    raise Abort("Cloudflare human check did not clear; try `login` again or run headed by hand")


def film_url(entry: dict) -> str | None:
    if entry["tmdb"]:
        return f"{BASE}/tmdb/{entry['tmdb']}/"
    if entry["imdb"]:
        return f"{BASE}/imdb/{entry['imdb']}/"
    return None


MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def open_film(pg, url: str) -> tuple[str, str]:
    """Load a film page; return (username, film slug). Aborts on lost login."""
    resp = pg.goto(url, wait_until="domcontentloaded")
    pg.wait_for_timeout(2000)
    wait_out_challenge(pg)
    if "logged-in" not in pg.evaluate("document.body.className").split():
        raise Abort("Letterboxd session is logged out; run `login` again")
    m = re.search(r"/film/([^/]+)/", pg.url)
    if (resp and resp.status == 404) or not m:
        raise ValueError(f"Letterboxd has no film for {url}")
    user = pg.evaluate("""() => { const a =
        [...document.querySelectorAll('nav a[href], .main-nav a[href]')].map(a => a.getAttribute('href'))
          .find(h => /^\\/[a-z0-9_]+\\/$/i.test(h) &&
                     !['/films/', '/lists/', '/members/', '/journal/'].includes(h));
        return a ? a.replaceAll('/', '') : null }""")
    if not user:
        raise Abort("could not read the Letterboxd username from the page")
    return user, m.group(1)


def diary_rows(pg, user: str, slug: str) -> dict[str, str]:
    """viewing id -> YYYY-MM-DD for this film's diary entries."""
    pg.goto(f"{BASE}/{user}/film/{slug}/diary/", wait_until="domcontentloaded")
    pg.wait_for_timeout(1500)
    wait_out_challenge(pg)
    raw = pg.evaluate("""() => [...document.querySelectorAll('tr.diary-entry-row')].map(r => [
        r.dataset.viewingId,
        (r.querySelector('.col-monthdate') || {}).innerText || '',
        (r.querySelector('.col-daydate') || {}).innerText || ''])""")
    rows, month, year = {}, None, None
    for vid, monthdate, day in raw:
        parts = monthdate.split()
        if len(parts) == 2:  # month/year only printed on the first row of a month
            month, year = MONTHS.get(parts[0].upper()[:3]), int(parts[1])
        if vid and month and year and day.strip().isdigit():
            rows[vid] = f"{year:04d}-{month:02d}-{int(day):02d}"
    return rows


def log_one(pg, entry: dict) -> str:
    """Log one diary entry. Returns 'logged' or 'already'. Verifies by diary rows,
    because Letterboxd's save call is not visible to Playwright's network events."""
    url = film_url(entry)
    if not url:
        raise ValueError("no TMDB or IMDb id")
    user, slug = open_film(pg, url)
    before = diary_rows(pg, user, slug)
    if entry["date"] in before.values():
        return "already"

    open_film(pg, url)
    # "Review or log..." and the "Log again..." submenu item are the same kind of
    # button: a Bootstrap trigger for the diary modal in compose mode.
    opened = pg.evaluate("""() => {
        const b = document.querySelector('button[data-bs-target="#diary-entry-form-modal"]' +
                                          '[data-diary-entry-form-options*="compose"]');
        if (b) b.click();
        return !!b }""")
    if not opened:
        raise ValueError("no Review or log button on the film page")
    form = pg.locator("form[action='/s/save-diary-entry']").filter(visible=True)
    form.wait_for(timeout=15000)
    pg.wait_for_function("""() => [...document.querySelectorAll("form[action='/s/save-diary-entry']")]
        .some(f => f.offsetParent && f.viewingableUid.value)""", timeout=15000)
    wait_out_challenge(pg)

    # Date and rewatch; rating and like stay empty (the logger records neither).
    form.first.evaluate("""(f, e) => {
        f.specifiedDate.checked = true;
        f.viewingDateStr.value = e.date;
        f.rewatch.checked = e.rewatch;
        f.rating.value = '0';
        if (f.liked) f.liked.checked = false;
    }""", entry)
    # Click Save from script, like the modal trigger above. Since 2026-11-15 the
    # button and the modal carry CSS transitions that never leave "running", so
    # Playwright's own click waits forever for the element to be "stable".
    saved = form.first.evaluate("""f => {
        const b = document.querySelector(`button[form="${f.id}"][type="submit"]`);
        if (!b || b.disabled || !b.offsetParent) return false;
        b.click();
        return true }""")
    if not saved:
        raise ValueError("Save button missing or disabled after filling the form")
    form.wait_for(state="hidden", timeout=20000)
    pg.wait_for_timeout(1500)

    after = diary_rows(pg, user, slug)
    new = {v: d for v, d in after.items() if v not in before}
    if entry["date"] not in new.values():
        raise ValueError(f"saved but no new diary row dated {entry['date']} (new rows: {new})")
    return "logged"


def push(entries: list[dict], ledger: dict) -> int:
    from playwright.sync_api import sync_playwright
    port = free_port()
    proc = launch_chromium("about:blank", port)
    failures = 0
    try:
        wait_for_devtools(port)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0]
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            for entry in entries:
                hid = entry["history_id"]
                label = f"{entry['title']} ({entry['year']}) {entry['date']}{' rewatch' if entry['rewatch'] else ''}"
                try:
                    outcome = log_one(pg, entry)
                except Abort:
                    raise
                except Exception as e:  # one film failing must not stop the rest
                    failures += 1
                    f = ledger["failed"].setdefault(hid, {"attempts": 0, **entry})
                    f["attempts"] += 1
                    f["error"] = str(e).splitlines()[0][:300]
                    save_ledger(ledger)
                    log(f"FAIL {label}: {f['error']} (attempt {f['attempts']}/{MAX_ATTEMPTS})")
                    continue
                ledger["failed"].pop(hid, None)
                ledger["pushed"][hid] = {**entry, "pushed_at": datetime.now().isoformat(timespec="seconds")}
                save_ledger(ledger)
                log(f"{outcome} {label}")
                pg.wait_for_timeout(3000)  # be gentle
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return failures


# --- Commands -------------------------------------------------------------

def cmd_login(_args) -> int:
    print("Sign in, tick Remember me, then quit the window with Cmd+Q.")
    launch_chromium(f"{BASE}/sign-in/").wait()
    print("Window closed. Check with: uv run letterboxd_push.py run --dry-run")
    return 0


def cmd_seed(_args) -> int:
    ledger = load_ledger()
    plays = trakt_history()
    seen, added = set(), 0
    for play in plays:
        entry = play_to_entry(play, seen)
        seen.add(play["movie"]["ids"]["trakt"])
        if entry["history_id"] not in ledger["pushed"]:
            ledger["pushed"][entry["history_id"]] = {**entry, "pushed_at": "seeded"}
            added += 1
    save_ledger(ledger)
    log(f"seeded {added} existing plays as pushed ({len(ledger['pushed'])} total)")
    return 0


def cmd_run(args) -> int:
    ledger = load_ledger()
    if not ledger["pushed"] and not args.force:
        print("Ledger is empty: this would push your whole Trakt history.\n"
              "Run `seed` first (then backfill old films by CSV), or pass --force.")
        return 2
    failures, attempted = 0, set()
    while True:  # re-read Trakt after pushing: a film logged mid-run gets picked up
        entries = [e for e in pending_entries(trakt_history(), ledger)
                   if e["history_id"] not in attempted]
        if args.limit:
            entries = entries[:args.limit]
        if not entries:
            if not attempted:
                print("nothing new")
            return 1 if failures else 0
        if args.dry_run:
            for e in entries:
                print(f"would log {e['title']} ({e['year']}) {e['date']}"
                      f"{' rewatch' if e['rewatch'] else ''} -> {film_url(e)}")
            return 0
        log(f"pushing {len(entries)} play(s)")
        attempted.update(e["history_id"] for e in entries)
        failures += push(entries, ledger)


def cmd_push_one(args) -> int:
    """Log one film outside Trakt history (testing, manual fixes). Not ledgered."""
    from playwright.sync_api import sync_playwright
    entry = {"history_id": "manual", "title": f"tmdb {args.tmdb}", "year": "",
             "tmdb": args.tmdb, "imdb": None, "date": args.date, "rewatch": args.rewatch}
    port = free_port()
    proc = launch_chromium("about:blank", port)
    try:
        wait_for_devtools(port)
        with sync_playwright() as p:
            ctx = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}").contexts[0]
            outcome = log_one(ctx.pages[0] if ctx.pages else ctx.new_page(), entry)
        log(f"{outcome} tmdb {args.tmdb} {args.date} (push-one)")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return 0


def cmd_check(_args) -> int:
    """Read-only: confirm the saved session is logged in and passes Cloudflare."""
    from playwright.sync_api import sync_playwright
    port = free_port()
    proc = launch_chromium("about:blank", port)
    try:
        wait_for_devtools(port)
        with sync_playwright() as p:
            ctx = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}").contexts[0]
            user, slug = open_film(ctx.pages[0] if ctx.pages else ctx.new_page(), f"{BASE}/tmdb/603/")
        print(f"ok: logged in as {user}, film page {slug} loads")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return 0


def cmd_export_csv(args) -> int:
    """Write the seeded (never pushed) plays as a Letterboxd import CSV.
    Upload at letterboxd.com/import; each row becomes a diary entry."""
    import csv
    ledger = load_ledger()
    rows = sorted((e for e in ledger["pushed"].values() if e.get("pushed_at") == "seeded"),
                  key=lambda e: e["date"])
    out = Path(args.out).expanduser()
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Year", "tmdbID", "imdbID", "WatchedDate", "Rewatch"])
        for e in rows:
            w.writerow([e["title"], e["year"] or "", e["tmdb"] or "", e["imdb"] or "",
                        e["date"], "true" if e["rewatch"] else "false"])
    print(f"wrote {len(rows)} plays to {out}")
    return 0


def cmd_status(_args) -> int:
    ledger = load_ledger()
    print(f"pushed: {len(ledger['pushed'])}  failed: {len(ledger['failed'])}")
    for f in ledger["failed"].values():
        print(f"  {f['title']} ({f['year']}) {f['date']}: {f['error']} "
              f"[{f['attempts']}/{MAX_ATTEMPTS}]")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("login")
    sub.add_parser("seed")
    sub.add_parser("status")
    sub.add_parser("check")
    exp = sub.add_parser("export-csv")
    exp.add_argument("--out", default="~/Downloads/letterboxd-backfill.csv")
    one = sub.add_parser("push-one")
    one.add_argument("--tmdb", type=int, required=True)
    one.add_argument("--date", required=True, help="YYYY-MM-DD")
    one.add_argument("--rewatch", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--force", action="store_true", help="allow a run with an empty ledger")
    args = ap.parse_args()
    if args.cmd is None:
        args = ap.parse_args(["run"])

    STATE.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another letterboxd_push is running")
            return 0
        try:
            return {"login": cmd_login, "seed": cmd_seed, "run": cmd_run,
                    "status": cmd_status, "check": cmd_check, "export-csv": cmd_export_csv, "push-one": cmd_push_one}[args.cmd](args)
        except Abort as e:
            log(f"ABORT {e}")
            return 3


if __name__ == "__main__":
    sys.exit(main())

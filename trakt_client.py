"""Trakt API client — device auth, search, and history sync."""

import json
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path

API_BASE = "https://api.trakt.tv"
TOKEN_FILE = Path(__file__).parent / "token.json"


def load_env():
    """Load .env file from script directory."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


load_env()

CLIENT_ID = os.environ.get("TRAKT_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("TRAKT_CLIENT_SECRET", "")


def _headers(access_token: str | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": CLIENT_ID,
        "User-Agent": "trakt-org-sync/1.0",
    }
    if access_token:
        h["Authorization"] = f"Bearer {access_token}"
    return h


def _request(method: str, path: str, body: dict | None = None,
             access_token: str | None = None) -> tuple[int, dict | list | None]:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=_headers(access_token), method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, {"error": raw}


# --- Auth ---

def device_code_request() -> dict:
    """Step 1: Get a device code + user verification URL."""
    status, data = _request("POST", "/oauth/device/code", {"client_id": CLIENT_ID})
    if status != 200:
        raise RuntimeError(f"Failed to get device code: {status} {data}")
    return data


def poll_for_token(device_code: str, interval: int, expires_in: int) -> dict:
    """Step 2: Poll until the user authorizes or timeout."""
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        status, data = _request("POST", "/oauth/device/token", {
            "code": device_code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })
        if status == 200:
            return data
        if status == 400:
            # Pending — user hasn't authorized yet
            continue
        if status == 404:
            raise RuntimeError("Invalid device code")
        if status == 409:
            raise RuntimeError("Code already approved")
        if status == 410:
            raise RuntimeError("Code expired — re-run the script")
        if status == 418:
            raise RuntimeError("User denied the authorization")
        if status == 429:
            time.sleep(interval)
            continue
    raise RuntimeError("Authorization timed out")


def authenticate() -> str:
    """Full device auth flow. Returns access token. Caches in token.json."""
    if TOKEN_FILE.exists():
        token_data = json.loads(TOKEN_FILE.read_text())
        return token_data["access_token"]

    print("=== Trakt Device Authorization ===")
    data = device_code_request()
    print(f"\n  1. Go to: {data['verification_url']}")
    print(f"  2. Enter code: {data['user_code']}\n")
    print("Waiting for authorization...")

    token_data = poll_for_token(data["device_code"], data["interval"], data["expires_in"])
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))
    print("Authorized! Token saved.\n")
    return token_data["access_token"]


# --- Search ---

def search_movie(title: str, year: int | None = None) -> dict | None:
    """Search for a movie by title. Returns the best match or None."""
    results = search_movies(title, year, limit=1)
    return results[0] if results else None


def search_movies(title: str, year: int | None = None, limit: int = 5) -> list[dict]:
    """Search for movies by title. Returns up to `limit` movie objects."""
    query = urllib.parse.quote(title)
    path = f"/search/movie?query={query}&limit={limit}&extended=full"
    if year:
        path += f"&years={year}"
    status, results = _request("GET", path)
    if status != 200 or not results:
        return []
    return [r["movie"] for r in results if "movie" in r]


# --- Sync ---

def get_watched_history(access_token: str) -> list[dict]:
    """Get all watched movies from Trakt."""
    status, data = _request("GET", "/sync/watched/movies", access_token=access_token)
    if status != 200:
        return []
    return data or []


def add_to_history(movies: list[dict], access_token: str) -> dict:
    """Add movies to watch history.

    Each movie dict should have:
        - ids: {trakt: int} or {imdb: str} or {tmdb: int}
        - watched_at: ISO 8601 string (optional)
        - title: str (optional, for display)
    """
    status, data = _request("POST", "/sync/history", {"movies": movies}, access_token=access_token)
    if status not in (200, 201):
        raise RuntimeError(f"Failed to sync history: {status} {data}")
    return data

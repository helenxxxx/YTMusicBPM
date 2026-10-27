"""Core logic: title clean-up, BPM lookup via GetSongBPM, caching and cadence matching.

Nothing in here knows about the web UI, so the CLI and the server share it.

Tempo data: GetSongBPM API, https://getsongbpm.com/api (its terms require a link back to getsongbpm.com).
HTTP: Requests, https://github.com/psf/requests (Apache-2.0)
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import requests

GETSONGBPM_URL = "https://api.getsong.co/search/"
REQUEST_PAUSE = 0.4  # seconds between lookups, to stay friendly with the API's rate limit


class BadApiKey(Exception):
    """GetSongBPM rejected the key."""


# --------------------------------------------------------------------------- data

@dataclass
class Track:
    video_id: str
    title: str
    artist: str
    duration: int = 0          # seconds
    bpm: int | None = None
    bpm_source: str = ""       # "getsongbpm", "manual" or ""
    genres: list[str] = field(default_factory=list)  # the artist's genres, from GetSongBPM

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.title}"

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ title cleanup

_NOISE = re.compile(
    r"""
    \s*[\(\[]\s*(?:
        official.*?|lyric.*?|audio|video|visuali[sz]er|hd|hq|4k|explicit|clean|
        .*?remaster.*?|.*?version|live.*?|mono|stereo|radio\s+edit|
        feat\.?.*?|ft\.?.*?|with\s.*?|prod\.?.*?
    )\s*[\)\]]
    """,
    re.IGNORECASE | re.VERBOSE,
)
_DASH_SUFFIX = re.compile(
    r"\s+-\s+(?:.*remaster.*|.*version|radio edit|single edit|live.*|mono|stereo)$", re.I
)
_INLINE_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.I)


def clean_title(title: str) -> str:
    t = _NOISE.sub("", title)
    t = _DASH_SUFFIX.sub("", t)
    t = _INLINE_FEAT.sub("", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def clean_artist(artist: str) -> str:
    """'Artist - Topic' channels and 'A, B & C' credits -> first main artist."""
    a = re.sub(r"\s*-\s*Topic$", "", artist, flags=re.I)
    a = re.split(r",|&| x | and | feat\.? | ft\.? ", a, maxsplit=1, flags=re.I)[0]
    return a.strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# --------------------------------------------------------------------- BPM cache

class BpmCache:
    """JSON file mapping video_id -> {"bpm": int|None, "source": str}. Thread-safe."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    def get(self, video_id: str) -> dict | None:
        return self._data.get(video_id)

    def __contains__(self, video_id: str) -> bool:
        return video_id in self._data

    def __len__(self) -> int:
        return len(self._data)

    def set(self, video_id: str, bpm: int | None, source: str, **extra) -> None:
        with self._lock:
            self._data[video_id] = {"bpm": bpm, "source": source, **extra}

    def update(self, video_id: str, **fields) -> None:
        """Change some fields of an entry, keeping the rest (e.g. keep genres when editing a BPM)."""
        with self._lock:
            self._data.setdefault(video_id, {"bpm": None, "source": ""}).update(fields)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)


# -------------------------------------------------------------------- BPM lookup

# A lookup takes (title, artist) and returns (bpm, genres), or None if the song isn't found.
# Genres are the artist's main genres as listed by GetSongBPM (it has no per-song genres).
LookupResult = "tuple[int | None, list[str]] | None"
LookupFn = Callable[[str, str], LookupResult]


def make_getsongbpm_lookup(api_key: str, session: requests.Session | None = None) -> LookupFn:
    session = session or requests.Session()

    def lookup(title: str, artist: str):
        return lookup_song(session, api_key, title, artist)

    return lookup


def clean_genres(raw) -> list[str]:
    out: list[str] = []
    for g in raw or []:
        if isinstance(g, str):
            g = re.sub(r"\s+", " ", g).strip().lower()
            if g and g not in out:
                out.append(g)
    return out


def lookup_song(session, api_key: str, title: str, artist: str):
    """Ask GetSongBPM for one song. Returns (bpm, genres), or None if not found."""
    params = {
        "api_key": api_key,
        "type": "both",
        "lookup": f"song:{title} artist:{artist}",
        "limit": 10,
    }
    for attempt in range(4):
        resp = session.get(GETSONGBPM_URL, params=params, timeout=20)
        if resp.status_code == 429:  # rate limited - back off and retry
            time.sleep(30 * (attempt + 1))
            continue
        if resp.status_code in (401, 403):
            raise BadApiKey("GetSongBPM rejected the API key.")
        resp.raise_for_status()
        break
    else:
        return None

    body = resp.json()
    results = body.get("search") if isinstance(body, dict) else None
    if not isinstance(results, list):  # API returns {"error": "no result"} when nothing matches
        return None

    want_title, want_artist = _norm(title), _norm(artist)
    best = None
    for r in results:
        if not r.get("tempo"):
            continue
        r_title = _norm(r.get("title", ""))
        r_artist = _norm((r.get("artist") or {}).get("name", ""))
        artist_ok = bool(want_artist and r_artist) and (want_artist in r_artist or r_artist in want_artist)
        score = (r_title == want_title) * 2 + artist_ok
        if best is None or score > best[0]:
            best = (score, r)
    if best is None or best[0] < 2:  # require at least an exact title match
        return None
    try:
        bpm = int(round(float(best[1]["tempo"])))
    except (TypeError, ValueError):
        return None
    return bpm, clean_genres((best[1].get("artist") or {}).get("genres"))


def lookup_bpm(session, api_key: str, title: str, artist: str) -> int | None:
    """Tempo only (kept for simple callers and tests)."""
    res = lookup_song(session, api_key, title, artist)
    return res[0] if res else None


ProgressFn = Callable[[int, int, "Track"], None]


def _needs_lookup(entry: dict | None) -> bool:
    if entry is None:
        return True
    # Songs found before genre support was added: fetch again once to pick up genres.
    return "genres" not in entry and entry.get("source") in ("getsongbpm", "manual")


def _normalise(res) -> tuple[int | None, list[str]]:
    if res is None:
        return None, []
    if isinstance(res, tuple):
        return res[0], clean_genres(res[1] if len(res) > 1 else [])
    return res, []  # plain int from a simple lookup function


def enrich(
    tracks: list[Track],
    cache: BpmCache,
    lookup: LookupFn,
    progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
    pause: float = REQUEST_PAUSE,
) -> None:
    """Fill in track.bpm and track.genres, looking up only songs that need it."""
    todo = [t for t in tracks if _needs_lookup(cache.get(t.video_id))]
    for i, t in enumerate(todo, 1):
        if should_stop and should_stop():
            break
        try:
            bpm, genres = _normalise(lookup(clean_title(t.title), clean_artist(t.artist)))
        except requests.RequestException:
            bpm = genres = None  # network hiccup: leave as is so the next scan tries again
        if genres is not None:
            old = cache.get(t.video_id)
            if old and old.get("source") == "manual":
                cache.update(t.video_id, genres=genres)  # keep the BPM you typed in
            else:
                cache.set(t.video_id, bpm, "getsongbpm" if bpm else "", title=t.title, artist=t.artist, genres=genres)
        if progress:
            progress(i, len(todo), t)
        if i % 20 == 0:
            cache.save()  # don't lose progress on long runs
        if pause:
            time.sleep(pause)
    cache.save()
    apply_cache(tracks, cache)


def apply_cache(tracks: list[Track], cache: BpmCache) -> None:
    for t in tracks:
        entry = cache.get(t.video_id) or {}
        t.bpm = entry.get("bpm")
        t.bpm_source = entry.get("source") or ""
        t.genres = list(entry.get("genres") or [])


def genre_matches(track_genres: list[str], wanted: list[str]) -> bool:
    """True if no genre filter is set, or the track has any of the wanted genres."""
    if not wanted:
        return True
    wanted = {w.strip().lower() for w in wanted}
    return any(g in wanted for g in track_genres)


# --------------------------------------------------------------------- matching

def match_kind(bpm: int, cadence: float, tol: float, half_time: bool) -> str | None:
    """Return 'full' or 'half' if the song fits the cadence, else None."""
    if abs(bpm - cadence) <= tol:
        return "full"
    if half_time and abs(bpm - cadence / 2) <= tol / 2:
        return "half"
    return None

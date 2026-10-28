"""Core logic: title clean-up, BPM lookup, caching and cadence matching.

Nothing in here knows about the web UI, so the CLI and the server share it.

Tempo data (all free): ReccoBeats API, https://reccobeats.com (no key), Deezer API,
https://developers.deezer.com (no key), and optionally the GetSongBPM API,
https://getsongbpm.com/api (its terms require a link back to getsongbpm.com).
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
DEEZER_URL = "https://api.deezer.com/"
RECCOBEATS_URL = "https://api.reccobeats.com/v1/"
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
    bpm_source: str = ""       # "getsongbpm", "reccobeats", "deezer", "manual" or ""
    genres: list[str] = field(default_factory=list)  # artist genres (GetSongBPM) or album genres (Deezer)

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

# A lookup takes (title, artist) and returns (bpm, genres) or (bpm, genres, source), or None if
# the song isn't found. GetSongBPM gives the artist's main genres (it has no per-song genres);
# Deezer gives the album's genres, which are broader ("Pop", "Rock", "Dance").
# A lookup may list the sources it asks in a `sources` attribute, so songs no source knew are
# tried again when a source is added.
LookupResult = "tuple[int | None, list[str]] | tuple[int | None, list[str], str] | None"
LookupFn = Callable[[str, str], LookupResult]


def make_getsongbpm_lookup(api_key: str, session: requests.Session | None = None) -> LookupFn:
    session = session or requests.Session()

    def lookup(title: str, artist: str):
        return lookup_song(session, api_key, title, artist)

    lookup.sources = ("getsongbpm",)
    return lookup


def make_lookup(api_key: str = "", session: requests.Session | None = None) -> LookupFn:
    """GetSongBPM first when there's a key (its genres are finer), then ReccoBeats, then Deezer.

    ReccoBeats has Spotify's tempo data, which covers far more recent songs than Deezer, but it
    can't search by name: it's looked up by the ISRC code of the songs Deezer finds.
    """
    session = session or requests.Session()
    albums: dict[int, list[str]] = {}

    def lookup(title: str, artist: str):
        if api_key:
            res = lookup_song(session, api_key, title, artist)
            if res:
                return res[0], res[1], "getsongbpm"
        candidates = deezer_candidates(session, title, artist)
        res = lookup_reccobeats(session, candidates, albums)
        if res:
            return res[0], res[1], "reccobeats"
        res = lookup_deezer(session, title, artist, albums, candidates=candidates)
        return (res[0], res[1], "deezer") if res else None

    lookup.sources = (("getsongbpm",) if api_key else ()) + ("reccobeats", "deezer")
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


def _deezer_get(session, path: str, params: dict | None = None) -> dict | None:
    for attempt in range(4):
        resp = session.get(DEEZER_URL + path, params=params, timeout=20)
        resp.raise_for_status()
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if not err:
            return body
        if err.get("code") == 4:  # quota: 50 requests per 5 seconds - wait and retry
            time.sleep(5 * (attempt + 1))
            continue
        return None
    return None


def deezer_candidates(session, title: str, artist: str) -> list[dict]:
    """Deezer search results whose title and artist match, best first.

    Deezer's 'artist:"…" track:"…"' search syntax currently finds nothing, so this uses a plain search.
    """
    body = _deezer_get(session, "search", {"q": f"{artist} {title}", "limit": 15})
    want_title, want_artist = _norm(title), _norm(artist)
    candidates = []
    for r in (body or {}).get("data") or []:
        r_titles = {_norm(r.get("title_short") or ""), _norm(clean_title(r.get("title") or ""))}
        r_artist = _norm((r.get("artist") or {}).get("name", ""))
        artist_ok = bool(want_artist and r_artist) and (want_artist in r_artist or r_artist in want_artist)
        if want_title in r_titles and artist_ok:
            candidates.append(r)
    return candidates


def lookup_reccobeats(session, candidates: list[dict], albums: dict | None = None):
    """Ask ReccoBeats for the tempo of the best few Deezer matches, by ISRC, in one request.

    Returns (bpm, album genres from Deezer), or None. If ReccoBeats is down this returns None,
    so Deezer still gets a go.
    """
    candidates = [r for r in candidates[:3] if r.get("isrc")]
    if not candidates:
        return None
    ids = ",".join(dict.fromkeys(r["isrc"] for r in candidates))
    try:
        for attempt in range(3):
            resp = session.get(RECCOBEATS_URL + "audio-features", params={"ids": ids}, timeout=20)
            if resp.status_code == 429:  # rate limited - back off and retry
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            break
        else:
            return None
        body = resp.json()
    except (requests.RequestException, ValueError):
        return None
    tempos = {}
    for f in (body.get("content") if isinstance(body, dict) else None) or []:
        if f.get("isrc") and f.get("tempo"):
            tempos.setdefault(f["isrc"], f["tempo"])
    for r in candidates:
        try:
            bpm = int(round(float(tempos.get(r["isrc"]) or 0)))
        except (TypeError, ValueError):
            bpm = 0
        if bpm:
            return bpm, _deezer_album_genres(session, (r.get("album") or {}).get("id"), albums)
    return None


def lookup_deezer(session, title: str, artist: str, albums: dict | None = None,
                  candidates: list[dict] | None = None):
    """Ask Deezer for one song. Returns (bpm, album genres), or None if not found.

    Search results don't include tempo, so this fetches the best few matches one by one: many
    tracks have bpm 0 (unknown), and another release of the same song often has it.
    """
    if candidates is None:
        candidates = deezer_candidates(session, title, artist)
    for r in candidates[:3]:
        track = _deezer_get(session, f"track/{r['id']}") or {}
        try:
            bpm = int(round(float(track.get("bpm") or 0)))
        except (TypeError, ValueError):
            bpm = 0
        if bpm:
            return bpm, _deezer_album_genres(session, (track.get("album") or {}).get("id"), albums)
    return None


def _deezer_album_genres(session, album_id, albums: dict | None) -> list[str]:
    if not album_id:
        return []
    if albums is not None and album_id in albums:
        return albums[album_id]
    album = _deezer_get(session, f"album/{album_id}") or {}
    genres = clean_genres(g.get("name") for g in (album.get("genres") or {}).get("data") or [])
    if albums is not None:
        albums[album_id] = genres
    return genres


def lookup_bpm(session, api_key: str, title: str, artist: str) -> int | None:
    """Tempo only (kept for simple callers and tests)."""
    res = lookup_song(session, api_key, title, artist)
    return res[0] if res else None


ProgressFn = Callable[[int, int, "Track"], None]


def _needs_lookup(entry: dict | None, sources: tuple[str, ...] = ()) -> bool:
    if entry is None:
        return True
    # Songs found before genre support was added: fetch again once to pick up genres.
    if "genres" not in entry and entry.get("source") in ("getsongbpm", "manual"):
        return True
    # Songs no source knew: try again once a new source is available (Deezer, or a new key).
    if entry.get("bpm") is None and entry.get("source") != "manual":
        return any(s not in _tried(entry) for s in sources)
    return False


def _tried(entry: dict | None) -> list[str]:
    """Sources already asked about a cached song. Older entries were only looked up on GetSongBPM."""
    if entry is None:
        return []
    return list(entry.get("tried", ["getsongbpm"]))


def _normalise(res) -> tuple[int | None, list[str], str]:
    if res is None:
        return None, [], ""
    if isinstance(res, tuple):
        source = res[2] if len(res) > 2 else "getsongbpm"
        return res[0], clean_genres(res[1] if len(res) > 1 else []), source
    return res, [], "getsongbpm"  # plain int from a simple lookup function


def enrich(
    tracks: list[Track],
    cache: BpmCache,
    lookup: LookupFn,
    progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
    pause: float = REQUEST_PAUSE,
) -> None:
    """Fill in track.bpm and track.genres, looking up only songs that need it."""
    sources = tuple(getattr(lookup, "sources", ()))
    todo = [t for t in tracks if _needs_lookup(cache.get(t.video_id), sources)]
    for i, t in enumerate(todo, 1):
        if should_stop and should_stop():
            break
        try:
            bpm, genres, source = _normalise(lookup(clean_title(t.title), clean_artist(t.artist)))
        except requests.RequestException:
            bpm = genres = None  # network hiccup: leave as is so the next scan tries again
        if genres is not None:
            old = cache.get(t.video_id)
            if old and old.get("source") == "manual":
                cache.update(t.video_id, genres=genres)  # keep the BPM you typed in
            else:
                tried = _tried(old) + [s for s in sources if s not in _tried(old)]
                cache.set(t.video_id, bpm, source if bpm else "", title=t.title, artist=t.artist,
                          genres=genres, tried=tried)
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

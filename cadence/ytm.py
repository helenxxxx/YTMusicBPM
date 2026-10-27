"""Thin wrapper around ytmusicapi (the unofficial YouTube Music library)."""

from __future__ import annotations

from pathlib import Path

from .core import Track


class NotConnected(Exception):
    pass


def save_browser_auth(auth_file: Path, headers_raw: str) -> None:
    """Turn request headers copied from music.youtube.com into an auth file.

    Raises ValueError with a readable message if the headers look wrong.
    """
    from ytmusicapi import setup

    if "cookie" not in headers_raw.lower():
        raise ValueError(
            "Those headers don't include a cookie line. Copy the request headers from a "
            "'browse' request on music.youtube.com while you're signed in."
        )
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        setup(filepath=str(auth_file), headers_raw=headers_raw)
    except Exception as e:  # ytmusicapi raises a variety of errors here
        raise ValueError(f"YouTube Music didn't accept those headers ({e}).") from e


def connect(auth_file: Path):
    from ytmusicapi import YTMusic

    if not Path(auth_file).exists():
        raise NotConnected("YouTube Music isn't connected yet.")
    return YTMusic(str(auth_file))


def list_sources(yt) -> list[dict]:
    """Liked songs plus the user's library playlists."""
    sources = [{"id": "liked", "title": "Liked songs", "count": None}]
    for p in yt.get_library_playlists(limit=None) or []:
        pid = p.get("playlistId")
        if not pid or pid == "LM":  # LM is liked music, already listed
            continue
        sources.append({"id": pid, "title": p.get("title", pid), "count": p.get("count")})
    return sources


def fetch_tracks(yt, source: str, limit: int | None = 2000) -> list[Track]:
    data = yt.get_liked_songs(limit=limit) if source == "liked" else yt.get_playlist(source, limit=limit)
    tracks, seen = [], set()
    for item in (data or {}).get("tracks", []):
        vid = item.get("videoId")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        artists = ", ".join(a["name"] for a in (item.get("artists") or []) if a.get("name"))
        tracks.append(
            Track(
                video_id=vid,
                title=item.get("title", ""),
                artist=artists or "Unknown",
                duration=int(item.get("duration_seconds") or 0),
            )
        )
    return tracks


def create_playlist(yt, name: str, description: str, video_ids: list[str], privacy: str = "PRIVATE") -> str:
    result = yt.create_playlist(name, description, privacy_status=privacy, video_ids=video_ids)
    if not isinstance(result, str):
        raise RuntimeError(f"YouTube Music returned an unexpected response: {result}")
    return result

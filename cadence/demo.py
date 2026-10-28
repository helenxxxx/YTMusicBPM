"""Demo mode: a made-up music library so you can try the UI without any accounts.

Run with:  python run.py --demo
"""

from __future__ import annotations

import random

_ARTISTS = [
    "Neon Harbour", "The Paper Kites Club", "Juno Vale", "Static Bloom", "Marlowe & Finch",
    "Kitelines", "Velvet Arcade", "Ruby Tramline", "Northbound Echo", "Cleo Sparks",
    "The Midnight Allotment", "Glass Pylon", "Harbour Lights FC", "Sunday Amps", "Ivy Racket",
]
_WORDS = [
    "Run", "Gold", "Wild", "Heart", "City", "Fire", "Ocean", "Signal", "Lights", "Electric",
    "Summer", "Static", "Dancing", "Rooftop", "Faster", "Echo", "Velvet", "Chasing", "Night",
    "Satellite", "Pulse", "Sunrise", "Stereo", "Paper", "Rocket", "Shadows", "Golden", "Rain",
]


def _library(n: int = 140, seed: int = 7, prefix: str = "demo"):
    rng = random.Random(seed)
    songs = []
    for i in range(n):
        title = " ".join(rng.sample(_WORDS, rng.choice([1, 2, 2, 3])))
        artist = rng.choice(_ARTISTS)
        r = rng.random()
        if r < 0.12:
            bpm = None                              # not in the database
        elif r < 0.38:
            bpm = rng.randint(160, 182)             # running tempo
        elif r < 0.52:
            bpm = rng.randint(80, 92)               # half-time candidates
        else:
            bpm = rng.randint(95, 150)
        songs.append(
            {
                "videoId": f"{prefix}{i:04d}",
                "title": title,
                "artists": [{"name": artist}],
                "duration_seconds": rng.randint(150, 290),
                "_bpm": bpm,
            }
        )
    return songs


LIBRARY = _library()
ELSEWHERE = _library(120, seed=11, prefix="new")  # songs not in your library, for Discover


class FakeYTMusic:
    def __init__(self):
        self.created: list[dict] = []

    def get_watch_playlist(self, videoId=None, radio=False, limit=25, **_):
        rng = random.Random(videoId)
        picks = rng.sample(LIBRARY, 5) + rng.sample(ELSEWHERE, limit - 5)  # radios mix in songs you know
        return {"tracks": [
            {"videoId": s["videoId"], "title": s["title"], "artists": s["artists"],
             "length": f"{s['duration_seconds'] // 60}:{s['duration_seconds'] % 60:02d}"}
            for s in picks
        ]}

    def get_liked_songs(self, limit=None):
        return {"tracks": LIBRARY[:limit] if limit else LIBRARY}

    def get_library_playlists(self, limit=None):
        return [
            {"playlistId": "PLdemoGym", "title": "Gym bangers", "count": 40},
            {"playlistId": "PLdemoCar", "title": "Car singalongs", "count": 35},
        ]

    def get_playlist(self, playlist_id, limit=None):
        start = 20 if playlist_id == "PLdemoGym" else 70
        return {"tracks": LIBRARY[start:start + 40]}

    def create_playlist(self, title, description, privacy_status="PRIVATE", video_ids=None, **_):
        pid = f"PLdemo{len(self.created) + 1:03d}"
        self.created.append({"id": pid, "title": title, "videos": list(video_ids or [])})
        return pid


_GENRES = {
    "Neon Harbour": ["synth-pop", "pop"], "The Paper Kites Club": ["indie", "rock"],
    "Juno Vale": ["pop", "dance pop"], "Static Bloom": ["indie", "electronic"],
    "Marlowe & Finch": ["folk", "indie"], "Kitelines": ["electronic", "dance"],
    "Velvet Arcade": ["rock", "alternative"], "Ruby Tramline": ["pop"],
    "Northbound Echo": ["hip hop", "rap"], "Cleo Sparks": ["pop", "r&b"],
    "The Midnight Allotment": ["indie", "rock"], "Glass Pylon": ["electronic", "house"],
    "Harbour Lights FC": ["rock", "punk"], "Sunday Amps": ["rock", "alternative"],
    "Ivy Racket": ["punk", "rock"],
}


def fake_lookup(title: str, artist: str):
    # The real lookup receives a cleaned-up artist ("Marlowe & Finch" -> "Marlowe"), so match on prefix.
    for s in LIBRARY + ELSEWHERE:
        name = s["artists"][0]["name"]
        if s["title"] == title and name.startswith(artist):
            return (s["_bpm"], _GENRES.get(name, [])) if s["_bpm"] else None
    return None

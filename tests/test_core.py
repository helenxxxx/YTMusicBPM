import time
from unittest import mock

import pytest

from cadence import core, ytm
from cadence.core import BpmCache, Track
from cadence.demo import FakeYTMusic, fake_lookup
from cadence.server import create_app


# ------------------------------------------------------------------ cleanup

@pytest.mark.parametrize("raw, clean", [
    ("Mr. Brightside (Official Video)", "Mr. Brightside"),
    ("Levitating (feat. DaBaby)", "Levitating"),
    ("Dreams - 2004 Remaster", "Dreams"),
    ("Blinding Lights [Official Audio]", "Blinding Lights"),
    ("Titanium ft. Sia", "Titanium"),
    ("Don't Stop Me Now (Remastered 2011)", "Don't Stop Me Now"),
])
def test_clean_title(raw, clean):
    assert core.clean_title(raw) == clean


@pytest.mark.parametrize("raw, clean", [
    ("The Killers - Topic", "The Killers"),
    ("Dua Lipa, DaBaby", "Dua Lipa"),
    ("Calvin Harris & Ellie Goulding", "Calvin Harris"),
])
def test_clean_artist(raw, clean):
    assert core.clean_artist(raw) == clean


# ----------------------------------------------------------------- matching

@pytest.mark.parametrize("bpm, kind", [
    (168, "full"), (173, "full"), (174, None), (85, "half"), (86, "half"), (87, None), (120, None),
])
def test_match_kind(bpm, kind):
    assert core.match_kind(bpm, 170, 3, True) == kind


def test_match_kind_without_half_time():
    assert core.match_kind(85, 170, 3, False) is None


# ------------------------------------------------------------------- lookup

class Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


def test_lookup_picks_exact_title_and_artist():
    s = mock.Mock()
    s.get.return_value = Resp(200, {"search": [
        {"title": "Brightside", "tempo": "100", "artist": {"name": "Other"}},
        {"title": "Mr. Brightside", "tempo": "148", "artist": {"name": "The Killers"}},
    ]})
    assert core.lookup_bpm(s, "k", "Mr. Brightside", "The Killers") == 148


def test_lookup_no_result():
    s = mock.Mock()
    s.get.return_value = Resp(200, {"search": {"error": "no result"}})
    assert core.lookup_bpm(s, "k", "Nope", "Nobody") is None


def test_lookup_rejects_fuzzy_title():
    s = mock.Mock()
    s.get.return_value = Resp(200, {"search": [{"title": "Something Else", "tempo": "120", "artist": {"name": "X"}}]})
    assert core.lookup_bpm(s, "k", "My Song", "X") is None


def test_lookup_bad_key():
    s = mock.Mock()
    s.get.return_value = Resp(401, {})
    with pytest.raises(core.BadApiKey):
        core.lookup_bpm(s, "bad", "a", "b")


def deezer_session(responses):
    """Fake session answering Deezer paths (e.g. 'search', 'track/1') from a dict."""
    s = mock.Mock()
    s.get.side_effect = lambda url, params=None, timeout=None: Resp(200, responses[url.removeprefix(core.DEEZER_URL)])
    return s


DEEZER_SEARCH = {"data": [
    {"id": 1, "title": "Eye of the Tiger (Live)", "title_short": "Eye of the Tiger", "artist": {"name": "Survivor"}},
    {"id": 2, "title": "Eye of the Tiger", "title_short": "Eye of the Tiger", "artist": {"name": "Survivor"}},
    {"id": 3, "title": "Eye of the Tiger", "title_short": "Eye of the Tiger", "artist": {"name": "Cover Band"}},
]}


def test_deezer_skips_releases_without_bpm():
    s = deezer_session({
        "search": DEEZER_SEARCH,
        "track/1": {"bpm": 0, "album": {"id": 10}},
        "track/2": {"bpm": 108.8, "album": {"id": 20}},
        "album/20": {"genres": {"data": [{"name": "Rock"}, {"name": "Pop"}]}},
    })
    assert core.lookup_deezer(s, "Eye of the Tiger", "Survivor") == (109, ["rock", "pop"])


def test_deezer_ignores_other_artists_and_unknown_tempo():
    s = deezer_session({
        "search": DEEZER_SEARCH,
        "track/1": {"bpm": 0}, "track/2": {"bpm": 0},
    })
    assert core.lookup_deezer(s, "Eye of the Tiger", "Survivor") is None
    assert core.lookup_deezer(deezer_session({"search": {"data": []}}), "Nope", "Nobody") is None


def test_make_lookup_order(monkeypatch):
    monkeypatch.setattr(core, "deezer_candidates", lambda s, title, artist: [])
    monkeypatch.setattr(core, "lookup_song", lambda s, key, title, artist: None)
    monkeypatch.setattr(core, "lookup_reccobeats", lambda s, candidates, albums=None: None)
    monkeypatch.setattr(core, "lookup_deezer", lambda s, title, artist, albums=None, candidates=None: (120, ["pop"]))
    assert core.make_lookup("key")("T", "A") == (120, ["pop"], "deezer")
    monkeypatch.setattr(core, "lookup_reccobeats", lambda s, candidates, albums=None: (130, ["rock"]))
    assert core.make_lookup("key")("T", "A") == (130, ["rock"], "reccobeats")
    monkeypatch.setattr(core, "lookup_song", lambda s, key, title, artist: (150, ["rock"]))
    assert core.make_lookup("key")("T", "A") == (150, ["rock"], "getsongbpm")
    assert core.make_lookup("")("T", "A") == (130, ["rock"], "reccobeats")  # no key: GetSongBPM skipped
    assert core.make_lookup("").sources == ("reccobeats", "deezer")


def test_reccobeats_by_isrc():
    s = mock.Mock()

    def get(url, params=None, timeout=None):
        if url.startswith(core.RECCOBEATS_URL):
            assert params == {"ids": "A1,B2"}
            return Resp(200, {"content": [{"isrc": "B2", "tempo": 130.027}, {"isrc": "B2", "tempo": 65.0}]})
        return Resp(200, {"genres": {"data": [{"name": "Rock"}]}})  # Deezer album
    s.get.side_effect = get
    candidates = [{"isrc": "A1", "album": {"id": 1}}, {"isrc": "B2", "album": {"id": 2}}, {"album": {"id": 3}}]
    assert core.lookup_reccobeats(s, candidates) == (130, ["rock"])
    assert core.lookup_reccobeats(s, [{"album": {"id": 3}}]) is None  # no ISRC: nothing to ask


def test_reccobeats_down_falls_through():
    s = mock.Mock()
    s.get.side_effect = core.requests.ConnectionError("down")
    assert core.lookup_reccobeats(s, [{"isrc": "A1"}]) is None


def test_songs_nobody_knew_are_retried_once_with_a_new_source(tmp_path):
    cache = BpmCache(tmp_path / "c.json")
    cache.set("a", None, "", genres=[])        # GetSongBPM didn't know it (before Deezer existed)
    tracks = [Track("a", "A", "X")]
    calls = []

    def lookup(title, artist):
        calls.append(title)
        return None
    lookup.sources = ("deezer",)

    core.enrich(tracks, cache, lookup, pause=0)
    core.enrich(tracks, cache, lookup, pause=0)
    assert calls == ["A"]  # asked Deezer once, then remembered it doesn't know it either
    lookup.sources = ("getsongbpm", "deezer")
    core.enrich(tracks, cache, lookup, pause=0)
    assert calls == ["A"]  # GetSongBPM had already been tried


# -------------------------------------------------------------------- cache

def test_enrich_uses_cache(tmp_path):
    cache = BpmCache(tmp_path / "c.json")
    tracks = [Track("a", "Song A", "Art"), Track("b", "Song B", "Art")]
    calls = []

    def lookup(title, artist):
        calls.append(title)
        return 170 if title == "Song A" else None

    core.enrich(tracks, cache, lookup, pause=0)
    assert [t.bpm for t in tracks] == [170, None]
    core.enrich(tracks, BpmCache(tmp_path / "c.json"), lookup, pause=0)
    assert calls == ["Song A", "Song B"]  # second run hit the cache only


def test_fetch_tracks_dedupes():
    yt = mock.Mock()
    yt.get_liked_songs.return_value = {"tracks": [
        {"videoId": "x", "title": "T", "artists": [{"name": "A"}, {"name": "B"}], "duration_seconds": 200},
        {"videoId": "x", "title": "T", "artists": [{"name": "A"}]},
        {"videoId": None, "title": "unavailable"},
    ]}
    out = ytm.fetch_tracks(yt, "liked")
    assert len(out) == 1 and out[0].artist == "A, B" and out[0].duration == 200


# --------------------------------------------------------------- web server

@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, demo=True)
    return app.test_client(), app.config["STATE"]


def wait_for_scan(c):
    for _ in range(400):
        job = c.get("/api/scan").get_json()
        if job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("scan never finished")


def test_full_flow_in_demo_mode(client):
    c, state = client
    st = c.get("/api/status").get_json()
    assert st["demo"] and st["ytm_connected"] and st["api_key_set"]

    sources = c.get("/api/sources").get_json()["sources"]
    assert sources[0]["id"] == "liked" and len(sources) == 3

    assert c.post("/api/scan", json={"source": "liked", "source_title": "Liked songs"}).status_code == 200
    job = wait_for_scan(c)
    assert job["state"] == "done", job

    tracks = c.get("/api/tracks").get_json()["tracks"]
    assert len(tracks) == 140
    known = [t for t in tracks if t["bpm"]]
    assert 100 < len(known) < 140  # some deliberately missing

    # edit a BPM
    missing = next(t for t in tracks if t["bpm"] is None)
    r = c.post(f"/api/tracks/{missing['video_id']}/bpm", json={"bpm": 171})
    assert r.get_json()["bpm"] == 171
    t = next(t for t in c.get("/api/tracks").get_json()["tracks"] if t["video_id"] == missing["video_id"])
    assert t["bpm"] == 171 and t["bpm_source"] == "manual"
    assert c.post(f"/api/tracks/{missing['video_id']}/bpm", json={"bpm": "fast"}).status_code == 400

    # create a playlist
    ids = [t["video_id"] for t in known[:5]]
    r = c.post("/api/playlist", json={"name": "Test run", "video_ids": ids, "privacy": "PRIVATE"})
    assert r.status_code == 200, r.get_json()
    assert state.yt().created[-1]["videos"] == ids
    assert c.post("/api/playlist", json={"name": "", "video_ids": ids}).status_code == 400
    assert c.post("/api/playlist", json={"name": "x", "video_ids": []}).status_code == 400


def test_library_and_prefs_persist(tmp_path):
    c = create_app(tmp_path, demo=True).test_client()
    c.post("/api/settings", json={"prefs": {"cadence": 165}})
    c.post("/api/scan", json={"source": "PLdemoGym", "source_title": "Gym bangers"})
    wait_for_scan(c)
    c2 = create_app(tmp_path, demo=True).test_client()
    st = c2.get("/api/status").get_json()
    assert st["prefs"]["cadence"] == 165
    assert st["library"]["source_title"] == "Gym bangers"
    assert len(c2.get("/api/tracks").get_json()["tracks"]) == 40


def test_real_mode_needs_setup(tmp_path):
    c = create_app(tmp_path, demo=False).test_client()
    st = c.get("/api/status").get_json()
    assert not st["ytm_connected"] and not st["api_key_set"]
    assert c.post("/api/scan", json={}).status_code == 409  # no GetSongBPM key needed, but YouTube Music is
    assert c.get("/api/sources").status_code == 409
    r = c.post("/api/connect", json={"headers": "accept: */*"})
    assert r.status_code == 400 and "cookie" in r.get_json()["error"]


CHROME_CURL = r"""curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \
  -H 'accept: */*' \
  -H 'authorization: SAPISIDHASH 123_abc' \
  -H 'content-type: application/json' \
  -b 'VISITOR_INFO1_LIVE=xyz; SAPISID=abc/def; __Secure-3PAPISID=abc/def' \
  -H 'x-goog-authuser: 0' \
  -H 'x-origin: https://music.youtube.com' \
  --data-raw $'{"context":{"client":{"hl":"en"}},"browseId":"FEmusic_liked_videos"}'"""


def test_headers_from_chrome_curl():
    lines = ytm.headers_from_curl(CHROME_CURL).splitlines()
    assert "authorization: SAPISIDHASH 123_abc" in lines
    assert "cookie: VISITOR_INFO1_LIVE=xyz; SAPISID=abc/def; __Secure-3PAPISID=abc/def" in lines
    assert "x-goog-authuser: 0" in lines
    assert not any("browseId" in l for l in lines)  # the request body isn't a header
    assert ytm.headers_from_curl("cookie: a=b") == "cookie: a=b"  # plain headers pass through


def test_connect_accepts_curl(tmp_path):
    ytm.save_browser_auth(tmp_path / "browser.json", CHROME_CURL)
    saved = (tmp_path / "browser.json").read_text(encoding="utf-8").lower()
    assert "sapisid=abc/def" in saved and "x-goog-authuser" in saved


# ------------------------------------------------------------------- genres

def test_lookup_returns_artist_genres():
    s = mock.Mock()
    s.get.return_value = Resp(200, {"search": [
        {"title": "Mr. Brightside", "tempo": "148",
         "artist": {"name": "The Killers", "genres": ["Rock", " alternative rock ", "rock"]}},
    ]})
    assert core.lookup_song(s, "k", "Mr. Brightside", "The Killers") == (148, ["rock", "alternative rock"])


def test_genre_matches():
    assert core.genre_matches(["rock", "punk"], [])
    assert core.genre_matches(["rock", "punk"], ["Punk"])
    assert not core.genre_matches(["pop"], ["rock"])
    assert not core.genre_matches([], ["rock"])


def test_old_cache_entries_are_backfilled_with_genres(tmp_path):
    cache = BpmCache(tmp_path / "c.json")
    cache.set("a", 170, "getsongbpm")          # found before genres existed
    cache.set("b", None, "")                  # not found before: don't ask again
    cache.set("c", 85, "manual")              # typed in by hand: keep the BPM
    tracks = [Track("a", "A", "X"), Track("b", "B", "X"), Track("c", "C", "X")]
    calls = []

    def lookup(title, artist):
        calls.append(title)
        return (999, ["rock"])

    core.enrich(tracks, cache, lookup, pause=0)
    assert calls == ["A", "C"]
    assert (tracks[0].bpm, tracks[0].genres) == (999, ["rock"])
    assert (tracks[2].bpm, tracks[2].bpm_source, tracks[2].genres) == (85, "manual", ["rock"])
    calls.clear()
    core.enrich(tracks, cache, lookup, pause=0)
    assert calls == []  # nothing left to backfill


def test_manual_bpm_edit_keeps_genres(client):
    c, state = client
    c.post("/api/scan", json={"source": "liked"})
    wait_for_scan(c)
    t = next(t for t in c.get("/api/tracks").get_json()["tracks"] if t["genres"])
    c.post(f"/api/tracks/{t['video_id']}/bpm", json={"bpm": 172})
    after = next(x for x in c.get("/api/tracks").get_json()["tracks"] if x["video_id"] == t["video_id"])
    assert after["bpm"] == 172 and after["genres"] == t["genres"]


# ------------------------------------------------------------------ discover

def test_discover_finds_new_songs_at_your_cadence(client):
    c, state = client
    assert c.post("/api/discover", json={"cadence": 170}).status_code == 200
    job = wait_for_scan(c)
    assert job["state"] == "error" and "Scan your songs first" in job["error"]  # nothing to start from

    c.post("/api/scan", json={"source": "liked"})
    wait_for_scan(c)
    assert c.post("/api/discover", json={"cadence": 170, "tolerance": 3, "half": True}).status_code == 200
    job = wait_for_scan(c)
    assert job["state"] == "done" and job["found"] > 0
    body = c.get("/api/tracks").get_json()
    mine = {t["video_id"] for t in body["tracks"]}
    found = body["discover"]
    assert len(found) == job["found"]
    assert not mine & {t["video_id"] for t in found}         # only songs you don't have
    assert any(t["bpm"] for t in found)                      # with tempos looked up
    assert len({t["video_id"] for t in found}) == len(found)  # no duplicates

    c.post("/api/discover", json={"cadence": 170})          # "Find more" adds to the list
    wait_for_scan(c)
    again = c.get("/api/tracks").get_json()["discover"]
    assert len(again) >= len(found) and len({t["video_id"] for t in again}) == len(again)


def test_fetch_radio_parses_tracks():
    yt = mock.Mock()
    yt.get_watch_playlist.return_value = {"tracks": [
        {"videoId": "seed", "title": "Seed", "artists": [{"name": "A"}], "length": "3:00"},
        {"videoId": "x1", "title": "New One", "artists": [{"name": "B"}, {"name": "C"}], "length": "1:02:03"},
        {"videoId": None, "title": "Unavailable"},
    ]}
    out = ytm.fetch_radio(yt, "seed")
    assert [(t.video_id, t.artist, t.duration) for t in out] == [("x1", "B, C", 3723)]

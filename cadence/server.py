"""Local web server for the Cadence Playlist UI.

Only listens on 127.0.0.1, so nothing outside your computer can reach it.
Everything it saves lives in the data/ folder next to the project.
"""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import core, ytm
from .core import BadApiKey, BpmCache, Track

STATIC = Path(__file__).parent / "static"


class AppState:
    def __init__(self, data_dir: Path, demo: bool = False):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.demo = demo
        self.config_file = self.data_dir / "config.json"
        self.auth_file = self.data_dir / "browser.json"
        self.library_file = self.data_dir / ("demo_library.json" if demo else "library.json")
        self.cache = BpmCache(self.data_dir / ("demo_bpm_cache.json" if demo else "bpm_cache.json"))
        self.config = self._read_json(self.config_file) or {}
        self.library = self._read_json(self.library_file) or {}
        self.discover_file = self.data_dir / ("demo_discover.json" if demo else "discover.json")
        self.discover = self._read_json(self.discover_file) or {}
        self._yt = None
        self._fake_yt = None
        self.job = {"state": "idle", "done": 0, "total": 0, "current": "", "error": ""}
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ persistence
    @staticmethod
    def _read_json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_json(path: Path, data) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def save_config(self) -> None:
        self._write_json(self.config_file, self.config)

    def save_library(self) -> None:
        self._write_json(self.library_file, self.library)

    # --------------------------------------------------------------- services
    @property
    def api_key(self) -> str:
        return self.config.get("api_key", "")

    def yt(self):
        if self.demo:
            if self._fake_yt is None:
                from .demo import FakeYTMusic
                self._fake_yt = FakeYTMusic()
            return self._fake_yt
        if self._yt is None:
            self._yt = ytm.connect(self.auth_file)
        return self._yt

    def ytm_connected(self) -> bool:
        return self.demo or self.auth_file.exists()

    def lookup(self):
        if self.demo:
            from .demo import fake_lookup
            return fake_lookup
        return core.make_lookup(self.api_key)

    def tracks(self) -> list[Track]:
        out = [Track(**t) for t in self.library.get("tracks", [])]
        core.apply_cache(out, self.cache)
        return out

    def discovered(self) -> list[Track]:
        out = [Track(**t) for t in self.discover.get("tracks", [])]
        core.apply_cache(out, self.cache)
        return out

    # ------------------------------------------------------------------- scan
    def _start_job(self, target, args, current: str) -> None:
        with self._lock:
            if self.job["state"] == "running":
                raise RuntimeError("A scan is already running.")
            self._stop.clear()
            self.job = {"state": "running", "done": 0, "total": 0, "current": current, "error": ""}
        threading.Thread(target=target, args=args, daemon=True).start()

    def start_scan(self, source: str, source_title: str) -> None:
        self._start_job(self._scan, (source, source_title), "Reading your songs…")

    def start_discover(self, cadence: float, tolerance: float, half: bool) -> None:
        self._start_job(self._discover, (cadence, tolerance, half), "Asking YouTube Music for similar songs…")

    def stop_scan(self) -> None:
        self._stop.set()

    def _scan(self, source: str, source_title: str) -> None:
        try:
            tracks = ytm.fetch_tracks(self.yt(), source)
            todo = sum(1 for t in tracks if t.video_id not in self.cache)
            self.job.update(total=todo, current=f"Found {len(tracks)} songs")

            def progress(i, n, t):
                self.job.update(done=i, total=n, current=t.label)

            core.enrich(
                tracks, self.cache, self.lookup(), progress=progress,
                should_stop=self._stop.is_set, pause=0.02 if self.demo else core.REQUEST_PAUSE,
            )
            self.library = {
                "source": source,
                "source_title": source_title,
                "scanned_at": int(time.time()),
                "tracks": [t.to_dict() for t in tracks],
            }
            self.save_library()
            self.job.update(state="stopped" if self._stop.is_set() else "done", current="")
        except BadApiKey:
            self.job.update(state="error", error="GetSongBPM rejected your API key. Check it in Settings.")
        except ytm.NotConnected as e:
            self.job.update(state="error", error=str(e))
        except Exception as e:  # surface anything else in the UI rather than dying silently
            self.job.update(state="error", error=f"{type(e).__name__}: {e}")

    # --------------------------------------------------------------- discover
    DISCOVER_SEEDS = 8       # songs of yours to start YouTube Music radios from
    DISCOVER_PER_SEED = 25   # suggestions taken from each radio

    def _discover(self, cadence: float, tolerance: float, half: bool) -> None:
        """Find songs you don't have yet, from YouTube Music radios for your songs at this cadence.

        Seeding from songs that already fit the cadence keeps both your taste and the tempo close.
        New suggestions are added to the ones found before, so "Find more" keeps growing the list.
        """
        try:
            library = self.tracks()
            fits = [t for t in library if t.bpm and core.match_kind(t.bpm, cadence, tolerance, half)]
            pool = fits or [t for t in library if t.bpm] or library
            if not pool:
                raise RuntimeError("Scan your songs first, so there's something to find similar songs to.")
            seeds = random.sample(pool, min(self.DISCOVER_SEEDS, len(pool)))

            known = {t.video_id for t in library} | {t["video_id"] for t in self.discover.get("tracks", [])}
            found: list[Track] = []
            for i, seed in enumerate(seeds, 1):
                if self._stop.is_set():
                    break
                self.job.update(current=f"Finding songs like {seed.label} ({i} of {len(seeds)})")
                for t in ytm.fetch_radio(self.yt(), seed.video_id, self.DISCOVER_PER_SEED):
                    if t.video_id not in known:
                        known.add(t.video_id)
                        found.append(t)

            def progress(i, n, t):
                self.job.update(done=i, total=n, current=t.label)

            core.enrich(
                found, self.cache, self.lookup(), progress=progress,
                should_stop=self._stop.is_set, pause=0.02 if self.demo else core.REQUEST_PAUSE,
            )
            self.discover = {
                "found_at": int(time.time()),
                "tracks": self.discover.get("tracks", []) + [t.to_dict() for t in found],
            }
            self._write_json(self.discover_file, self.discover)
            self.job.update(state="stopped" if self._stop.is_set() else "done", current="", found=len(found))
        except BadApiKey:
            self.job.update(state="error", error="GetSongBPM rejected your API key. Check it in Settings.")
        except (ytm.NotConnected, RuntimeError) as e:
            self.job.update(state="error", error=str(e))
        except Exception as e:  # surface anything else in the UI rather than dying silently
            self.job.update(state="error", error=f"{type(e).__name__}: {e}")


def create_app(data_dir: Path, demo: bool = False) -> Flask:
    app = Flask(__name__, static_folder=None)
    state = AppState(data_dir, demo=demo)
    app.config["STATE"] = state

    def err(msg: str, code: int = 400):
        return jsonify({"error": msg}), code

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(STATIC, name)

    @app.get("/api/status")
    def status():
        return jsonify(
            demo=state.demo,
            ytm_connected=state.ytm_connected(),
            api_key_set=state.demo or bool(state.api_key),
            cached=len(state.cache),
            library={k: state.library.get(k) for k in ("source", "source_title", "scanned_at")},
            prefs=state.config.get("prefs", {}),
        )

    @app.post("/api/settings")
    def settings():
        body = request.get_json(force=True) or {}
        if "api_key" in body:
            state.config["api_key"] = (body["api_key"] or "").strip()
        if "prefs" in body and isinstance(body["prefs"], dict):
            state.config["prefs"] = body["prefs"]
        state.save_config()
        return jsonify(ok=True)

    @app.post("/api/connect")
    def connect():
        if state.demo:
            return jsonify(ok=True)
        headers = (request.get_json(force=True) or {}).get("headers", "")
        try:
            ytm.save_browser_auth(state.auth_file, headers)
            state._yt = None
            ytm.list_sources(state.yt())  # prove it works
        except ValueError as e:
            return err(str(e))
        except Exception as e:
            state.auth_file.unlink(missing_ok=True)
            state._yt = None
            return err(f"Couldn't reach your YouTube Music library with those headers ({e}).")
        return jsonify(ok=True)

    @app.post("/api/disconnect")
    def disconnect():
        state.auth_file.unlink(missing_ok=True)
        state._yt = None
        return jsonify(ok=True)

    @app.get("/api/sources")
    def sources():
        try:
            return jsonify(sources=ytm.list_sources(state.yt()))
        except ytm.NotConnected as e:
            return err(str(e), 409)
        except Exception as e:
            return err(f"Couldn't load your playlists ({e}). Your YouTube Music sign-in may have expired.", 502)

    @app.post("/api/scan")
    def scan():
        body = request.get_json(force=True) or {}
        if not state.ytm_connected():
            return err("Connect YouTube Music first.", 409)
        try:
            state.start_scan(body.get("source", "liked"), body.get("source_title", "Liked songs"))
        except RuntimeError as e:
            return err(str(e), 409)
        return jsonify(ok=True)

    @app.post("/api/scan/stop")
    def scan_stop():
        state.stop_scan()
        return jsonify(ok=True)

    @app.get("/api/scan")
    def scan_status():
        return jsonify(state.job)

    @app.get("/api/tracks")
    def tracks():
        return jsonify(tracks=[t.to_dict() for t in state.tracks()],
                       discover=[t.to_dict() for t in state.discovered()])

    @app.post("/api/discover")
    def discover():
        body = request.get_json(force=True) or {}
        if not state.ytm_connected():
            return err("Connect YouTube Music first.", 409)
        try:
            cadence = float(body.get("cadence", 170))
            tolerance = float(body.get("tolerance", 3))
        except (TypeError, ValueError):
            return err("Cadence must be a number.")
        try:
            state.start_discover(cadence, tolerance, bool(body.get("half", True)))
        except RuntimeError as e:
            return err(str(e), 409)
        return jsonify(ok=True)

    @app.post("/api/tracks/<video_id>/bpm")
    def set_bpm(video_id):
        bpm = (request.get_json(force=True) or {}).get("bpm")
        if bpm in ("", None):
            state.cache.update(video_id, bpm=None, source="manual")
        else:
            try:
                bpm = int(round(float(bpm)))
            except (TypeError, ValueError):
                return err("BPM must be a number.")
            if not 30 <= bpm <= 300:
                return err("BPM should be between 30 and 300.")
            state.cache.update(video_id, bpm=bpm, source="manual")
        state.cache.save()
        return jsonify(ok=True, bpm=bpm)

    @app.post("/api/playlist")
    def make_playlist():
        body = request.get_json(force=True) or {}
        name = (body.get("name") or "").strip()
        ids = [v for v in body.get("video_ids", []) if isinstance(v, str)]
        privacy = body.get("privacy", "PRIVATE")
        if not name:
            return err("Give the playlist a name.")
        if not ids:
            return err("Pick at least one song.")
        if privacy not in ("PRIVATE", "UNLISTED", "PUBLIC"):
            return err("Unknown privacy setting.")
        desc = (body.get("description") or "").strip() or "Made with Cadence Playlist."
        desc += " BPM data: ReccoBeats, Deezer and getsongbpm.com"
        try:
            pid = ytm.create_playlist(state.yt(), name, desc, ids, privacy)
        except Exception as e:
            return err(f"YouTube Music didn't create the playlist ({e}).", 502)
        return jsonify(ok=True, playlist_id=pid, url=f"https://music.youtube.com/playlist?list={pid}")

    return app

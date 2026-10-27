#!/usr/bin/env python3
"""Command-line version (the web app in run.py does the same thing with a UI).

    python cadence_playlist.py --cadence 170            # preview
    python cadence_playlist.py --cadence 170 --create   # create the playlist
    python cadence_playlist.py --cadence 170 --genre rock --genre punk

Uses the same data/ folder as the web app, so connect YouTube Music and add your
GetSongBPM key in the web app once and this works too. GETSONGBPM_API_KEY in the
environment overrides the saved key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cadence import core, ytm

DATA = Path(__file__).resolve().parent / "data"


def main() -> None:
    p = argparse.ArgumentParser(description="Build a YouTube Music playlist matched to your running cadence.")
    p.add_argument("--cadence", type=float, required=True, help="target steps per minute, e.g. 170")
    p.add_argument("--tolerance", type=float, default=3, help="allowed BPM difference (default 3)")
    p.add_argument("--no-half-time", action="store_true", help="don't include half-tempo songs")
    p.add_argument("--genre", action="append", default=[], metavar="GENRE",
                   help="only songs whose artist is tagged with this genre (repeat to allow several)")
    p.add_argument("--list-genres", action="store_true", help="show the genres found and stop")
    p.add_argument("--source", default="liked", help="'liked' (default) or a YouTube Music playlist ID")
    p.add_argument("--limit", type=int, default=2000, help="max songs to read from the source")
    p.add_argument("--name", help="playlist name (default: 'Running <cadence> spm')")
    p.add_argument("--create", action="store_true", help="create the playlist (otherwise preview only)")
    args = p.parse_args()

    try:
        saved = json.loads((DATA / "config.json").read_text(encoding="utf-8")).get("api_key", "")
    except (FileNotFoundError, json.JSONDecodeError):
        saved = ""
    api_key = os.environ.get("GETSONGBPM_API_KEY") or saved
    if not api_key:
        sys.exit("No GetSongBPM API key. Add it in the web app (python run.py) or set GETSONGBPM_API_KEY.")

    try:
        yt = ytm.connect(DATA / "browser.json")
    except ytm.NotConnected:
        sys.exit("YouTube Music isn't connected. Connect it in the web app first (python run.py).")

    print(f"Reading songs from {'your liked songs' if args.source == 'liked' else args.source}...")
    tracks = ytm.fetch_tracks(yt, args.source, args.limit)
    print(f"Found {len(tracks)} songs.")
    if not tracks:
        return

    cache = core.BpmCache(DATA / "bpm_cache.json")

    def progress(i, n, t):
        print(f"  [{i}/{n}] {t.label}")

    try:
        core.enrich(tracks, cache, core.make_getsongbpm_lookup(api_key), progress=progress)
    except core.BadApiKey:
        sys.exit("GetSongBPM rejected the API key.")

    if args.list_genres:
        from collections import Counter
        counts = Counter(g for t in tracks for g in t.genres)
        print("\nGenres in these songs:")
        for g, n in counts.most_common():
            print(f"  {n:>4}  {g}")
        return

    half = not args.no_half_time
    matches = [(k, t) for t in tracks
               if t.bpm and core.genre_matches(t.genres, args.genre)
               and (k := core.match_kind(t.bpm, args.cadence, args.tolerance, half))]
    matches.sort(key=lambda m: (m[0] != "full", m[1].bpm))

    lo, hi = args.cadence - args.tolerance, args.cadence + args.tolerance
    print(f"\nBPM known for {sum(1 for t in tracks if t.bpm)}/{len(tracks)} songs.")
    print(f"Matches for {args.cadence:g} spm ({lo:g}-{hi:g} BPM"
          + (f", or {lo/2:g}-{hi/2:g} half-time" if half else "")
          + (f"; genres: {', '.join(args.genre)}" if args.genre else "") + f"): {len(matches)}\n")
    for kind, t in matches:
        genres = f"  [{', '.join(t.genres[:3])}]" if t.genres else ""
        print(f"  {t.bpm:>3} BPM {'(half-time)' if kind == 'half' else '            '}  {t.label}{genres}")

    if not matches:
        print("Nothing matched. Try a wider --tolerance, or point --source at a bigger playlist.")
        return
    if not args.create:
        print("\nPreview only. Re-run with --create to make this playlist in YouTube Music.")
        return

    name = args.name or f"Running {args.cadence:g} spm" + (f" · {' & '.join(g.title() for g in args.genre)}" if args.genre else "")
    desc = (f"Songs at {lo:g}-{hi:g} BPM" + (" (plus half-time)" if half else "")
            + (f". Genres: {', '.join(args.genre)}" if args.genre else "") + ". BPM data: getsongbpm.com")
    pid = ytm.create_playlist(yt, name, desc, [t.video_id for _, t in matches])
    print(f"\nCreated '{name}' with {len(matches)} songs: https://music.youtube.com/playlist?list={pid}")


if __name__ == "__main__":
    main()

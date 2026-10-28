# Cadence 🏃‍♀️🎵

Build YouTube Music playlists that match your running cadence.

YouTube Music doesn't know how fast songs are, so Cadence reads your liked songs (or any of your playlists), looks up each song's tempo on [ReccoBeats](https://reccobeats.com) and [Deezer](https://www.deezer.com) (and optionally the free [GetSongBPM](https://getsongbpm.com) database), and shows you the ones that match your steps per minute. Untick anything you don't fancy, then save it straight to YouTube Music as a playlist.

- **Cadence slider** with wiggle room (± BPM), so you can see instantly what fits 165, 170 or 180 spm
- **Half-time songs**: an 85 BPM song works for 170 spm (two steps per beat), which roughly doubles your options
- **Genre filter**: pick one or more genres (rock, pop, house…) to narrow the matches. Each genre shows how many songs it has at your cadence
- **Fix tempos by hand**: click any BPM to edit it, with ×2 / ÷2 buttons for songs the database has at double or half speed. Songs the database doesn't know go in the *Needs a BPM* tab
- **Remembers everything**: each song is only looked up once, so later scans are quick
- Runs **only on your computer**. Nothing is sent anywhere except YouTube Music, ReccoBeats, Deezer and (if you add a key) GetSongBPM

## Get started

You need Python 3.10 or newer.

```bash
git clone https://github.com/helenxxxx/YTMusicBPM.git
cd YTMusicBPM
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Your browser opens at <http://127.0.0.1:5170>, and the app walks you through setup:

1. **Connect YouTube Music.** There's no official YouTube Music API, so the app uses [ytmusicapi](https://github.com/sigma67/ytmusicapi), which borrows your signed-in browser session. The app shows step-by-step instructions for copying a request from music.youtube.com with **Copy as cURL (bash)** (plain copied request headers work too).
2. **GetSongBPM API key (optional).** Tempos come from ReccoBeats and Deezer, which need no account, but they don't know every song. A free key from <https://getsongbpm.com/api> fills in more tempos and finer genres. You can skip this and add it later in ⚙️ Settings.

Next time, just `cd` into the folder, activate the virtual environment and run `python run.py`.

**Want a look first?** `python run.py --demo` runs with a made-up library, with no accounts needed.

## Using it

1. Pick where your songs come from (*Liked songs* or one of your playlists) and press **Scan for tempos**. The first scan takes a while for a big library (about half a second per song); after that it only looks up new songs.
2. Set your cadence and wiggle room. The *Matches* tab updates as you go.
3. Optionally pick one or more genres. Songs matching any of them are kept, and the playlist name picks up the genre (e.g. *Running 170 spm · Rock*).
4. Untick any songs you don't want, choose the order (by tempo, library order or shuffled), name the playlist and press **Save to YouTube Music**. Playlists are private unless you choose otherwise.

Tips:
- Running at 170 spm? Try **± 3** to start. Your stride adjusts to a slightly off beat without you noticing.
- Tempo databases sometimes list a song at double or half its real speed. If something sounds wrong, click its BPM and use ×2 / ÷2.
- Genres are a rough guide: Deezer tags the **album** with broad genres (used for ReccoBeats tempos too) (pop, rock, dance), and GetSongBPM lists the **artist's** main genres. Songs neither knows have no genre, so they drop out while a genre filter is on.
- Songs neither source knew are tried again once when a new source becomes available, such as when you add a GetSongBPM key.
- Scanned songs before genres were added? Press **Scan again** once and it fetches their genres.
- If scans start failing with sign-in errors, your YouTube Music session has expired. Open ⚙️ Settings → **Reconnect** and paste fresh headers.

## Command line

The original script still works and shares the same settings and tempo cache:

```bash
python cadence_playlist.py --cadence 170            # preview
python cadence_playlist.py --cadence 170 --create   # save the playlist
python cadence_playlist.py --cadence 170 --list-genres          # see which genres you have
python cadence_playlist.py --cadence 170 --genre rock --genre punk
python cadence_playlist.py --help
```

## Your data

Everything is stored in the `data/` folder, which git ignores:

| File | What it is |
|---|---|
| `config.json` | Your GetSongBPM key (if any) and slider and genre settings |
| `browser.json` | Your YouTube Music session. **Treat it like a password.** |
| `bpm_cache.json` | Every tempo and genre looked up so far, plus your manual edits |
| `library.json` | The songs from your last scan |

Delete the folder to start fresh.

## Project layout

```
run.py                  start the web app
cadence_playlist.py     command-line version
cadence/core.py         title clean-up, BPM lookup, caching, cadence matching
cadence/ytm.py          YouTube Music wrapper (ytmusicapi)
cadence/server.py       local Flask server and JSON API
cadence/demo.py         made-up library for --demo
cadence/static/         the web page (HTML, CSS, JS)
tests/                  pytest tests (run: pip install -r requirements-dev.txt && pytest)
```

## Built with

| Project | What it does here | Licence |
|---|---|---|
| [ytmusicapi](https://github.com/sigma67/ytmusicapi) | Reads your YouTube Music library and creates playlists | MIT |
| [Flask](https://github.com/pallets/flask) | Runs the local web server | BSD-3-Clause |
| [Requests](https://github.com/psf/requests) | Talks to the ReccoBeats, Deezer and GetSongBPM APIs | Apache-2.0 |
| [pytest](https://github.com/pytest-dev/pytest) | Runs the tests (development only) | MIT |

Tempo data comes from [ReccoBeats](https://reccobeats.com), [Deezer](https://developers.deezer.com) and [GetSongBPM](https://getsongbpm.com). ytmusicapi is unofficial and not affiliated with Google or YouTube.

[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) has full credits, licence details and related projects.

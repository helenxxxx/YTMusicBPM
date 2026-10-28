# Third-party software and services

Cadence is built on the open-source projects and services below. None of their code is copied into this repository. They are installed separately from PyPI (see `requirements.txt` and `requirements-dev.txt`), and each stays under its own licence.

## Python libraries

| Project | Used for | Licence | Repository |
|---|---|---|---|
| **ytmusicapi** (sigma67) | Reading your YouTube Music library and creating playlists | MIT | <https://github.com/sigma67/ytmusicapi> · [docs](https://ytmusicapi.readthedocs.io) |
| **Flask** (Pallets) | The local web server behind the UI | BSD-3-Clause | <https://github.com/pallets/flask> |
| **Requests** (Python Software Foundation) | Calling the Deezer and GetSongBPM APIs | Apache-2.0 | <https://github.com/psf/requests> |
| **pytest** (development only) | Running the tests | MIT | <https://github.com/pytest-dev/pytest> |

Flask also installs its own dependencies from Pallets, such as Werkzeug (BSD-3-Clause, <https://github.com/pallets/werkzeug>) and Jinja (BSD-3-Clause, <https://github.com/pallets/jinja>). These are credited in their own projects.

Licences checked against the package metadata of ytmusicapi 1.12.3, Flask 3.1.3, Requests 2.34.2 and pytest 9.1.1.

## Services

- **[Deezer](https://developers.deezer.com)** provides tempo and album genre data through its public API, which needs no key. The app's footer, every playlist description and this README credit Deezer.
- **[GetSongBPM](https://getsongbpm.com)** optionally provides tempo and artist genre data when you add an API key. Its [API terms](https://getsongbpm.com/api) ask apps to link back to the site, so the app's footer, every playlist description and this README all include a link to getsongbpm.com.
- **YouTube Music** is accessed through ytmusicapi, which is unofficial and not affiliated with or endorsed by Google or YouTube. Use it in line with [YouTube's Terms of Service](https://www.youtube.com/t/terms).

## Related projects

These were looked at while planning Cadence. No code was taken from either.

- **yt-dj-mcp** by didtjd59 (<https://github.com/didtjd59/yt-dj-mcp>, MIT) builds DJ playlists from YouTube Music liked songs using audio analysis.
- **Running cadence playlist script** by Matt Steele (<https://www.steele.blue/running-cadence-playlist>) builds cadence playlists from local music files for Garmin watches.

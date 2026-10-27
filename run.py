#!/usr/bin/env python3
"""Start Cadence Playlist and open it in your browser.

    python run.py          # normal mode
    python run.py --demo   # try it with a made-up library, no accounts needed
"""

import argparse
import threading
import webbrowser
from pathlib import Path

from cadence.server import create_app

HERE = Path(__file__).resolve().parent


def main() -> None:
    p = argparse.ArgumentParser(description="Cadence Playlist web app")
    p.add_argument("--port", type=int, default=5170)
    p.add_argument("--demo", action="store_true", help="use a made-up library instead of YouTube Music")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab automatically")
    p.add_argument("--data", default=str(HERE / "data"), help="where settings and caches are kept")
    args = p.parse_args()

    app = create_app(Path(args.data), demo=args.demo)
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  Cadence Playlist is running at {url}" + ("  (demo mode)" if args.demo else ""))
    print("  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

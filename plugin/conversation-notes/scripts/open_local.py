#!/usr/bin/env python3
"""User-invoked fallback launcher; MCP panels do not need this process."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import DATA


def running_url():
    try:
        url = json.loads((DATA / 'preview.json').read_text())['url']
        if not url.startswith('http://127.0.0.1:'):
            return None
        info = json.loads(urllib.request.urlopen(url + '/health', timeout=1).read())
        return url if info.get('app') == 'conversation-notes' else None
    except (OSError, ValueError, KeyError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    url = running_url()
    if not url:
        with (DATA / 'preview.log').open('a') as log:
            subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--web'], stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, start_new_session=True, cwd=ROOT)
        for _ in range(40):
            time.sleep(.1)
            url = running_url()
            if url:
                break
    if not url:
        raise SystemExit('墨记未能启动，请查看 ~/.codex/conversation-notes/preview.log')
    print(url)
    if not args.no_open:
        webbrowser.open(url)


if __name__ == '__main__':
    main()

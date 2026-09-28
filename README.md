# P2P File Sharing (Day-1 MVP) — BitTorrent-like, cross-network

Stdlib-only Python. Two devices on **different networks** share files via a public tracker+relay. No port forwarding.

## 1. Host the tracker once (public URL)

Render free web service:
- Start command: `python tracker.py --host 0.0.0.0 --port $PORT`
- Health check: `/health`
- Or Docker (`Dockerfile` included), Fly.io, Replit, or `ngrok http 8000` as fallback.

Check: `curl https://<your-app>.onrender.com/health` → `{"ok": true}`

Local test: `python tracker.py --port 8000` → open `http://localhost:8000/` for the test UI, `curl localhost:8000/health` for API.

## Phone / browser testing (same host, no install)

The tracker also serves a 1-page test UI (phone-friendly, `static/index.html`):
- Laptop: `python tracker.py --port 8000` → open `http://localhost:8000/`
- Public: open `https://<your-app>.onrender.com/` on your phone browser.
- **Download tab**: paste/upload `.torrent.json` → Download via relay → save link appears after SHA-1 verify. Uses the same `/announce` + `/relay/*` API as `peer.py`.
- **Share tab**: pick a file → Make torrent JSON → send JSON to the other device → Start seeding (keep tab foreground).
- E2E with phone: seed via laptop CLI, download via phone UI on mobile data (or reverse).

## 2. Share a file (Device A, any network)

```bash
python make_torrent.py ./data/notes.pdf --tracker https://<your-app>.onrender.com --out notes.torrent.json
python peer.py seed ./data/notes.pdf --torrent notes.torrent.json --mode relay
# send notes.torrent.json to Device B (WhatsApp/email)
```

## 3. Download (Device B, different network)

```bash
python peer.py download notes.torrent.json --out ./downloads/notes.pdf --mode relay
sha256sum ./data/notes.pdf ./downloads/notes.pdf  # must match
```

Downloader keeps seeding after (Ctrl-C to stop). `--mode direct` = LAN fast path; `--mode relay` (default path in `auto`) = cross-NAT.

## Tests

```bash
python tests/test_pieces.py
python tests/test_relay_e2e.py
bash scripts/demo.sh
```

## Limits (Day 1)
20MB via relay, 128KB pieces, base64 relay overhead (~33%), in-memory tracker. See `plan.md` §8 for next steps (hole-punching, rarest-first, choking).

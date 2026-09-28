# P2P File Sharing — BitTorrent-like, cross-network, no terminal needed

Share files between devices on **different networks** with just a link. No account, no port forwarding. Python stdlib only.

Live app: `https://p2p-file-sharing-pjgm.onrender.com/` (test UI + tracker + relay in one process)

## Quickstart — no terminal (phone-friendly)

**Share (Device A, any browser):**
1. Open the app → **Share a file** → pick a file → **Share this file**.
2. Copy the link (or send via WhatsApp button). Keep the tab open while sharing.

**Get (Device B, any browser, different network):**
1. Open the link → file name/size shows up → **Download**.
2. Save the file when the green link appears. Optionally keep seeding for a third device.

That's it — no `.torrent.json` files, no commands. Links look like `https://<host>/?m=<info_hash>` and work for 24h.

## CLI (optional, same protocol)

```bash
# tracker locally
python tracker.py --port 8000  # UI at http://localhost:8000/

# seed a file (auto-publishes; prints link + magnet)
python make_torrent.py ./data/notes.pdf --tracker https://<host> --out notes.torrent.json
python peer.py seed ./data/notes.pdf --torrent notes.torrent.json --mode relay

# download via magnet/link (no JSON file needed)
python peer.py download --magnet "magnet:?xt=urn:p2p:<info_hash>&tr=https://<host>" --out ./notes.pdf --mode relay --no-seed-after
# or the classic way:
python peer.py download notes.torrent.json --out ./notes.pdf --mode relay
```

`--mode direct` = LAN fast path (same WiFi, needs reachable ports); `--mode relay` (default) = cross-NAT via tracker. Downloader keeps seeding unless `--no-seed-after`.

## Deploy your own (Render free)

- New Web Service from this repo, start command `python tracker.py --host 0.0.0.0 --port $PORT`, health check `/health`.
- Cold starts take ~30–60s after idle; first load wakes it.
- Keep-alive: `.github/workflows/keepalive.yml` pings `/health` every 10 min (override URL via `P2P_URL` repo secret; default is the live URL above).

## Tests

```bash
python tests/test_pieces.py
python tests/test_relay_e2e.py
python tests/test_magnet_e2e.py
bash scripts/demo.sh
```

## Limits
20MB via relay, 128KB pieces, base64 relay overhead (~33%), in-memory tracker (metainfo kept 24h, pieces 60s). See `plan.md` §8 for next steps (hole-punching, rarest-first, choking).

# P2P File-Sharing Client (BitTorrent-like) — Project Plan

## 1. Goal & Context
Build a simplified BitTorrent-like P2P client for a Computer Networks class.
- Demonstrate core CN concepts: TCP sockets, application-layer protocol design, peer discovery (tracker), NAT traversal, piece-based transfer, concurrency, integrity checking.
- **Prototype must be hostable and working Day 1 across different networks**: e.g. laptop on hostel WiFi + laptop on mobile data share a file with zero router config. Both peers make **outbound-only** connections to a public tracker/relay.
- Keep stdlib-only so any lab machine with Python 3.10+ runs it with zero install.

Non-goals for Day 1: DHT, GUI, encryption, real `.torrent` interop, tit-for-tat economics, true NAT hole-punching (that comes later — Day 1 uses relay).

### Key design decision (cross-network Day 1)
Direct TCP `leecher → seeder` fails across NATs/CG-NAT/college firewall. So Day-1 architecture is:
- **Public tracker + relay (one process, one public URL)** + **direct-P2P fast path with relay fallback**.
- `auto` mode: try direct TCP if both peers report reachable LAN/public addresses; otherwise (default across networks) relay piece traffic through the tracker over outbound HTTP. This is the same idea as TURN in WebRTC, simplified to HTTP polling so it stays stdlib-only and hostable on any free Python host.

## 2. Success Criteria

### Day-1 Demo (MVP must pass)
1. `python tracker.py` runs locally AND deploys as-is to one free public host (Render/Fly/Replit/VPS) with a public `https://<app>/` URL.
2. `python peer.py seed <file>` from Network A (e.g. hostel WiFi) registers with the **public** tracker URL, no port-forwarding, no `--port` exposure needed.
3. `python peer.py download <metainfo.json> --out <path>` from Network B (e.g. mobile hotspot / home WiFi) uses only outbound HTTP to tracker/relay, downloads all pieces, verifies SHA-1 per piece, reassembles byte-identical file (`sha256sum` matches).
4. Works in all three topologies: localhost, same LAN, **different networks (NAT→NAT via relay)**. Different-network is the graded path.
5. Handles 1 seeder + 2-3 leechers, files 1KB–20MB via relay Day 1 (relay cap; larger files = use direct mode on LAN), clean failure message on tracker down / seeder offline.

### Final Submission (stretch)
Throughput graphs (direct vs relay), rarest-first, resume, choking, UDP hole-punching to bypass relay, DHT, Web UI.

## 3. Tech Stack (Day 1)

- **Language: Python 3.10+ stdlib only** — `socket`, `threading`, `hashlib`, `json`, `base64`, `http.server`, `urllib`, `argparse`.
  - Why: no `pip install`, runs on college lab PCs and free hosts, easy to explain sockets in viva/report.
- **Transport Day 1: outbound HTTPS/HTTP to tracker+relay for discovery AND data.** Direct TCP peer wire kept as optional fast path on LAN (same code, `--mode direct`), but default `--mode auto` uses relay so different-network works with no inbound ports.
- **No DB**: tracker keeps in-memory `dict[info_hash -> peers]` + `dict[request_id -> piece payload]` with TTL + optional `state.json` dump.
- **CLI only Day 1**: `tracker.py`, `peer.py`, `make_torrent.py`. No GUI.
- **Hosting (required for cross-network)**:
  - Tracker/relay: one public instance. Pick one: Render free Web Service (`python tracker.py --host 0.0.0.0 --port $PORT`), Fly.io, Replit, Oracle Cloud free VM, or any lab PC with public IP + `ngrok http 8000` / `tailscale funnel` as fallback. No Docker required Day 1, one `Dockerfile` provided for hosts that need it.
  - Peers: any machine with Python + internet. No inbound firewall rules, no router login.

## 4. Architecture (MVP)

```
[make_torrent.py] --> file.torrent.json (name, size, piece_len, pieces[sha1], info_hash, tracker_url=https://...)

Network A (seeder, behind NAT)               Public host                Network B (leecher, behind NAT)
  peer.py seed ── outbound POST /announce ──► tracker:8000 ◄── POST /announce ── peer.py download
  peer.py seed ── outbound GET /relay/inbox (long-poll 25s) ◄─┐
                 ◄── relay dispatches REQUEST ────────────────┤── POST /relay/request (piece N to seeder)
  peer.py seed ── outbound POST /relay/send (piece N bytes) ─► relay queue ──► GET /relay/fetch (poll) ── leecher
  ───────── direct TCP fast path (LAN only, optional): leecher dials seeder ip:port if reachable ─────────
```

### 4.1 Metainfo format (`file.torrent.json`)
```json
{
  "name": "notes.pdf",
  "size": 1234567,
  "piece_length": 131072,
  "pieces": ["<sha1 hex piece0>", "..."],
  "info_hash": "<sha1 of concatenated piece hashes>",
  "tracker_url": "https://p2p-tracker-xyz.onrender.com"
}
```
Note: default `piece_length` 128KB (not 256KB) Day 1 — smaller pieces fit relay JSON/base64 + free-host memory limits better. Created offline by owner with `make_torrent.py`. Shared out-of-band (USB/WhatsApp/email) — same as real torrents. `tracker_url` must be the **public** URL, baked in at creation time.

### 4.2 Tracker + Relay API (HTTP, port 8000 locally / `$PORT` on host)
Discovery (same as before, but public):
- `POST /announce` `{info_hash, peer_id, mode: auto|direct|relay, direct_ip?, direct_port?, event: started|completed|stopped}` → `{peers: [{peer_id, mode, direct_ip, direct_port}], interval: 30, relay: true}`
- `GET /peers?info_hash=<hex>` → peer list (debugging).
- `GET /health` → `ok`.

Relay (new, Day-1 required for NAT→NAT):
- `POST /relay/request` `{info_hash, from_peer_id, to_peer_id, piece_index}` → `{request_id}` (leecher asks relay to ask seeder).
- `GET /relay/inbox?peer_id=<id>&timeout=25` → long-poll, returns `[{request_id, from_peer_id, info_hash, piece_index}]` (seeder blocks here; no inbound needed).
- `POST /relay/send` `{request_id, to_peer_id, piece_index, data_b64}` → `{ok: true}` (seeder uploads piece; relay stores ≤60s, ≤512MB total cap with LRU evict).
- `GET /relay/fetch?peer_id=<id>&request_id=<rid>&timeout=25` → long-poll, returns `{piece_index, data_b64}` or `408` on timeout (leecher polls here).
- Logic: expire peers after `3 * interval`; expire undelivered relay payloads after 60s; cap piece to 128KB → ~170KB base64, safe for free hosts.
- Concurrency: `http.server.ThreadingHTTPServer`. Base64-over-JSON is intentionally simple/debuggable with `curl`; note ~33% overhead in report, optimize to raw bytes/WebSocket in Phase 3.

### 4.3 Peer Wire Protocol
Two paths, same piece format (`HANDSHAKE`/`REQUEST`/`PIECE` semantics preserved):
1. **Relay path (default, works across networks)**: exactly the 4 relay endpoints above + local SHA-1 verify. Leecher: fetch peer list → `POST /relay/request` per missing piece (parallel, 4–8 workers) → `GET /relay/fetch` poll → verify → write `.part` → rename on complete → `POST /announce event=completed` (becomes seeder). Seeder: loop `GET /relay/inbox` → read piece from disk by `seek()` → `POST /relay/send`.
2. **Direct path (LAN fast path, optional)**: length-prefixed JSON + raw bytes over TCP (`HANDSHAKE {"peer_id","info_hash"}` → `REQUEST {piece_index}` → `PIECE + raw bytes`), `ThreadingTCPServer` on `--direct-port 6881`. Used only if `--mode direct` or `auto` probe succeeds within 3s; else falls back to relay silently.
- Day-1 selection: `--mode auto` (default) → try direct 3s → relay. `--mode relay` forces cross-network path (use for grading demo). `--mode direct` forces LAN path.

### 4.4 CLI Spec (Day 1, cross-network)
```bash
# ONE TIME: host the tracker publicly (pick one), e.g. Render:
#   start command: python tracker.py --host 0.0.0.0 --port $PORT
#   health check:  GET /health
# suppose public URL is https://p2p-tracker-xyz.onrender.com

# seeder on Network A (hostel WiFi, no port forwarding)
python make_torrent.py ./data/notes.pdf --tracker https://p2p-tracker-xyz.onrender.com --piece-length 131072 --out ./notes.torrent.json
python peer.py seed ./notes.pdf --torrent ./notes.torrent.json --mode relay
# (send notes.torrent.json to leecher via WhatsApp/email)

# leecher on Network B (mobile data / home WiFi, no port forwarding)
python peer.py download ./notes.torrent.json --out ./downloads/notes.pdf --mode relay
sha256sum ./data/notes.pdf ./downloads/notes.pdf  # must match

# same-LAN fast path (optional, same commands with --mode direct --direct-port 6881)
```

## 5. Repo Layout (Day 1)
```
/p2p/
  plan.md
  README.md              # 1-page run instructions + public-host deploy steps
  tracker.py             # ~200 lines: announce + peers + relay queues + expiry
  peer.py                # ~450 lines: seed + download, relay + direct, verify
  make_torrent.py        # ~60 lines
  common.py              # hashing, framing, b64 helpers, config (~100 lines)
  Procfile / render.yaml # one-click public deploy (start cmd + health check)
  Dockerfile             # optional, for Fly/Render-alternative hosts
  tests/
    test_pieces.py       # split/hash/verify round-trip
    test_relay_e2e.py    # spawn tracker + seeder thread, download via relay (localhost), compare bytes; also timeout test
  data/
  scripts/demo.sh        # localhost relay smoke test + direct-mode test
```

## 6. Day-1 Build Plan (5–7 hours, single sitting)

1. **Scaffold (30m)**: `common.py` (piece hash, `recv_exact`, b64 helpers), arg parsing stubs (`--mode auto|relay|direct`).
2. **Metainfo (30m)**: `make_torrent.py` with public `tracker_url`, 128KB default; manual `cat` check.
3. **Tracker+relay (120m)**: `ThreadingHTTPServer`, `/announce`, `/peers`, `/relay/{request,inbox,send,fetch}` with long-poll + TTL + caps; test all 4 with `curl`.
4. **Relay seeder+leecher (120m)**: seeder inbox loop + send; leecher request/fetch pool (4 workers), SHA-1 verify, `.part` assemble, `announce(completed)`.
5. **Direct fast path (60m, thin)**: reuse old TCP HANDSHAKE/REQUEST/PIECE behind `--mode direct`; `auto` = 3s direct probe → relay fallback.
6. **E2E + cross-network test (60m)**: `scripts/demo.sh` localhost relay → deploy tracker to Render → phone on mobile data vs laptop on WiFi transfer, `sha256sum` check, screenshot both terminals + `curl /health` on public URL for report.

Definition of done: public tracker URL live + `demo.sh` green + photo of two different-network terminals producing matching `sha256sum`.

## 7. Demo Script for Class (5 min, different networks)
1. Show public tracker: `curl https://<app>/health` → `ok` (prove hostable).
2. Seeder on Network A (e.g. phone hotspot): `peer.py seed --mode relay`, show `.torrent.json` with public `tracker_url`.
3. Leecher on Network B (college WiFi): `peer.py download --mode relay`, show piece log (`[7/16 pieces] 43% via relay @ 800 KB/s`) + Wireshark/`tcpdump` note: only outbound TLS/HTTP to tracker, no inbound — that's why NAT doesn't block it.
4. `sha256sum` match on both machines + kill original seeder, second leecher downloads from first leecher via relay (still P2P swarm, just relayed).
5. Talking points: NAT/CG-NAT + why direct dial fails; tracker vs relay (discovery vs TURN-like forwarding); why pieces (parallelism/resume); per-piece hash (integrity); relay cost (~33% b64 overhead + server bandwidth) → motivates hole-punching in Phase 3.

## 8. Improvements After Day 1 (prioritized)

### Phase 2 — Correctness & Robustness (next 1 week)
- [ ] Bitfield + `HAVE` broadcast (via relay inbox) so peers know who has what.
- [ ] Rarest-first piece selection (replaces round-robin; big win with 3+ peers).
- [ ] Retry with backoff, peer timeout/blacklist, checksum-fail re-request; resume via `.part` + bitmap.
- [ ] Raise relay limits safely: raw `application/octet-stream` upload/download (drop b64), 256KB pieces, per-swarm rate caps.
- [ ] Basic stats: per-peer speed, ETA, progress bar; split relay vs direct counters.
- [ ] `docker-compose.yml` (tracker + seeder + leecher) for reproducible grading.

### Phase 3 — True P2P Across NATs (week 2–3, remove relay dependency)
- [ ] TCP/UDP hole-punching with relay-assisted rendezvous (exchange candidates via `/relay`, then STUN-style reflexive address discovery, simultaneous-open).
- [ ] Choking / unchoking + tit-for-tat (top-4 + optimistic unchoke) — key CN topic (fairness/incentives).
- [ ] Historic `bencode` + compat with real `.torrent` files (parse-only first).
- [ ] Endgame mode (request last pieces from all peers); parallel sharding.
- [ ] Optional WebSocket relay (replace polling) for lower latency.

### Phase 4 — Decentralized & Production-ish (stretch)
- [ ] Trackerless: DHT (Kademlia-lite over UDP), PEX, magnet-link style `info_hash` join.
- [ ] UPnP / manual port-forward guide; UDP tracker protocol.
- [ ] Web dashboard (peer graph, piece heatmap, direct-vs-relay throughput plot for report).
- [ ] Security: allowlist, rate-limit tracker, piece-hash DoS guard, optional TLS for tracker.
- [ ] Benchmarks: 50MB direct-LAN vs relayed cross-network throughput plot.

## 9. Testing & Evaluation
- Unit: piece split/hash, b64 round-trip, tracker expiry, relay TTL/evict (fake clock).
- Integration: `test_relay_e2e.py` — ephemeral ports, random 2MB file via relay path, assert byte-identical + corrupt-piece rejection + inbox/fetch timeout paths.
- Manual (graded): public-URL health check + 2-network transfer (different ISPs if possible: hostel WiFi + mobile data), record pcap showing outbound-only flows for appendix.
- Grading map: app-layer design (§4), TCP/HTTP + NAT (§4.2–4.3, §7), concurrency, reliability/integrity (SHA-1), performance (direct vs relay overhead).

## 10. Risks & Mitigations
| Risk | Mitigation |
|---|---|
| Free host sleeps / bandwidth caps | Use Render/Fly with health-check + keep-alive ping during demo; cap Day-1 files to 20MB via relay; direct mode for large LAN files |
| College firewall blocks even outbound | Relay uses standard 443/HTTPS so it passes where raw ports don't; fallback to phone hotspot; `curl` test first |
| Relay latency/throughput low | 128KB pieces + 4 workers + long-poll (not tight loop); document b64 overhead honestly; hole-punching in Phase 3 removes relay from data path |
| Scope creep (DHT/GUI) kills Day-1 | Freeze Day-1 to §4–§6; everything else goes to §8 backlog |
| Relay storage abuse | 60s TTL + 512MB cap + per-`info_hash` rate limit; no persistence |

## 11. What to Show the TA Tomorrow
- Public tracker URL + `curl /health` + this `plan.md` + `README` quickstart (3 commands per side, `--mode relay`) + two-terminal `sha256sum` match across different networks. That proves NAT-aware networking understanding without over-engineering.

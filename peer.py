#!/usr/bin/env python3
"""Peer: seed or download. Modes: relay (cross-NAT default), direct (LAN), auto."""
import argparse
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import uuid
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import b64d, b64e, http_get_json, http_post_json, load_torrent

STOP = threading.Event()


def announce(base, info_hash, peer_id, mode, event, direct_ip=None, direct_port=None):
    try:
        return http_post_json(f"{base}/announce", {
            "info_hash": info_hash, "peer_id": peer_id, "mode": mode,
            "direct_ip": direct_ip, "direct_port": direct_port, "event": event})
    except Exception as e:
        print(f"[tracker] announce failed: {e}", flush=True)
        return {"peers": [], "interval": 30}


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def heartbeat(base, info_hash, peer_id, mode, direct_ip, direct_port):
    while not STOP.is_set():
        time.sleep(25)
        if STOP.is_set():
            break
        announce(base, info_hash, peer_id, mode, "started", direct_ip, direct_port)


# ---------- direct TCP fast path ----------
def send_msg(conn, obj):
    b = json.dumps(obj).encode()
    conn.sendall(struct.pack("!I", len(b)) + b)


def recv_msg(conn):
    hdr = b""
    while len(hdr) < 4:
        c = conn.recv(4 - len(hdr))
        if not c:
            raise ConnectionError("closed")
        hdr += c
    n = struct.unpack("!I", hdr)[0]
    buf = b""
    while len(buf) < n:
        c = conn.recv(min(65536, n - len(buf)))
        if not c:
            raise ConnectionError("closed")
        buf += c
    return json.loads(buf.decode())


def run_direct_server(path, info_hash, piece_length, port):
    import socketserver

    class H(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                hs = recv_msg(self.request)
                if hs.get("info_hash") != info_hash:
                    send_msg(self.request, {"status": "bad-info-hash"})
                    return
                send_msg(self.request, {"status": "ok"})
                with open(path, "rb") as f:
                    while True:
                        try:
                            req = recv_msg(self.request)
                        except ConnectionError:
                            return
                        idx = req.get("piece_index")
                        f.seek(idx * piece_length)
                        data = f.read(piece_length)
                        send_msg(self.request, {"piece_index": idx, "length": len(data)})
                        self.request.sendall(data)
            except Exception:
                return

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
    srv = S(("0.0.0.0", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def fetch_direct(ip, port, info_hash, peer_id, idx, timeout=3):
    s = socket.create_connection((ip, port), timeout=timeout)
    s.settimeout(10)
    try:
        send_msg(s, {"peer_id": peer_id, "info_hash": info_hash})
        r = recv_msg(s)
        if r.get("status") != "ok":
            raise ConnectionError(r)
        send_msg(s, {"piece_index": idx})
        hdr = recv_msg(s)
        n = hdr["length"]
        buf = b""
        while len(buf) < n:
            c = s.recv(min(65536, n - len(buf)))
            if not c:
                raise ConnectionError("truncated")
            buf += c
        return buf
    finally:
        s.close()


# ---------- relay path ----------
def read_piece(path, idx, piece_length):
    with open(path, "rb") as f:
        f.seek(idx * piece_length)
        return f.read(piece_length)


def seed_loop(base, meta, file_path, peer_id, mode, direct_ip, direct_port):
    info_hash = meta["info_hash"]
    announce(base, info_hash, peer_id, mode, "started", direct_ip, direct_port)
    threading.Thread(target=heartbeat,
                     args=(base, info_hash, peer_id, mode, direct_ip, direct_port),
                     daemon=True).start()
    if mode in ("direct", "auto"):
        try:
            run_direct_server(file_path, info_hash, meta["piece_length"], direct_port)
            print(f"[seed] direct TCP on :{direct_port}", flush=True)
        except OSError as e:
            print(f"[seed] direct server failed ({e}), relay-only", flush=True)
    print(f"[seed] {peer_id} serving '{meta['name']}' "
          f"({len(meta['pieces'])} pieces) via {mode} — Ctrl-C to stop", flush=True)
    while not STOP.is_set():
        try:
            items = http_get_json(f"{base}/relay/inbox",
                                  {"peer_id": peer_id, "timeout": 25})
        except Exception:
            time.sleep(1)
            continue
        for req in items:
            try:
                data = read_piece(file_path, req["piece_index"], meta["piece_length"])
                http_post_json(f"{base}/relay/send", {
                    "request_id": req["request_id"], "to_peer_id": req["from_peer_id"],
                    "piece_index": req["piece_index"], "data_b64": b64e(data)}, timeout=30)
            except Exception as e:
                print(f"[seed] serve piece {req.get('piece_index')} failed: {e}", flush=True)


def download_piece_relay(base, meta, from_choices, my_id, idx, tries=6):
    expect = meta["pieces"][idx]
    last_err = "no seeder"
    for attempt in range(tries):
        target = from_choices[(idx + attempt) % len(from_choices)] if from_choices else None
        if not target:
            time.sleep(1)
            continue
        try:
            r = http_post_json(f"{base}/relay/request", {
                "info_hash": meta["info_hash"], "from_peer_id": my_id,
                "to_peer_id": target, "piece_index": idx}, timeout=15)
            got = http_get_json(f"{base}/relay/fetch",
                                {"peer_id": my_id, "request_id": r["request_id"],
                                 "timeout": 25}, timeout=35)
            data = b64d(got["data_b64"])
            if hashlib.sha1(data).hexdigest() != expect:
                last_err = "hash mismatch"
                continue
            return data
        except urllib.error.HTTPError as e:
            last_err = f"fetch {e.code}"
            time.sleep(1)
        except Exception as e:
            last_err = str(e)
            time.sleep(1)
    raise RuntimeError(f"piece {idx} failed after {tries} tries ({last_err}); "
                       "is the seeder still online?")


def cmd_seed(args):
    meta = load_torrent(args.torrent)
    base = (args.tracker or meta["tracker_url"]).rstrip("/")
    peer_id = uuid.uuid4().hex[:12]
    direct_ip = args.direct_ip or get_local_ip()
    try:
        seed_loop(base, meta, args.file, peer_id, args.mode, direct_ip, args.direct_port)
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        announce(base, meta["info_hash"], peer_id, args.mode, "stopped")


def cmd_download(args):
    meta = load_torrent(args.torrent)
    base = (args.tracker or meta["tracker_url"]).rstrip("/")
    info_hash = meta["info_hash"]
    my_id = uuid.uuid4().hex[:12]
    n = len(meta["pieces"])
    my_ip = args.direct_ip or get_local_ip()

    resp = announce(base, info_hash, my_id, args.mode, "started", my_ip, args.direct_port)
    if args.mode == "direct":
        seeders = [p for p in resp.get("peers", []) if p.get("direct_ip")]
        if not seeders:
            print("ERROR: no direct-capable peers. Seeder must run with "
                  "--mode direct and reachable ip:port.", flush=True)
            sys.exit(1)
        print(f"[dl] direct mode, {len(seeders)} seeder(s)", flush=True)
        t0 = time.time()
        out = [None] * n
        def one(i):
            s = seeders[i % len(seeders)]
            for _ in range(3):
                try:
                    data = fetch_direct(s["direct_ip"], s["direct_port"],
                                        info_hash, my_id, i)
                    if hashlib.sha1(data).hexdigest() != meta["pieces"][i]:
                        continue
                    return i, data
                except Exception:
                    time.sleep(0.5)
            raise RuntimeError(f"direct fetch of piece {i} failed")
        with ThreadPoolExecutor(max_workers=8) as ex:
            for i, data in ex.map(one, range(n)):
                out[i] = data
                print(f"[{i+1}/{n}] direct {100*(i+1)/n:.0f}%", flush=True)
        blob = b"".join(out)[:meta["size"]]
    else:
        # relay (or auto -> relay fallback): poll peer list until a seeder appears
        seeders = []
        for _ in range(12):
            r = announce(base, info_hash, my_id, args.mode, "started")
            seeders = [p["peer_id"] for p in r.get("peers", []) if p["peer_id"] != my_id]
            if seeders:
                break
            print("[dl] waiting for seeder... (start peer.py seed on the other device)",
                  flush=True)
            time.sleep(5)
        if not seeders:
            print("ERROR: no seeder found. Is the seeder online with the same "
                  "torrent/tracker?", flush=True)
            sys.exit(1)
        print(f"[dl] {len(seeders)} seeder(s) via relay, {n} pieces", flush=True)
        t0 = time.time()
        out = [None] * n
        done = [0]
        def one(i):
            data = download_piece_relay(base, meta, seeders, my_id, i)
            out[i] = data
            done[0] += 1
            el = time.time() - t0
            done_bytes = sum(len(x) for x in out if x)
            print(f"[{done[0]}/{n}] {100*done[0]/n:.0f}% relay "
                  f"@ {done_bytes/max(el,0.01)/1024:.0f} KB/s",
                  flush=True)
            return i
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(one, range(n)))
        blob = b"".join(out)[:meta["size"]]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    tmp = args.out + ".part"
    with open(tmp, "wb") as f:
        f.write(blob)
    # final full-verify
    for i in range(n):
        chunk = blob[i*meta["piece_length"]:(i+1)*meta["piece_length"]]
        assert hashlib.sha1(chunk).hexdigest() == meta["pieces"][i], f"final verify failed @{i}"
    os.replace(tmp, args.out)
    el = time.time() - t0
    print(f"done: {args.out} ({len(blob)} B, {n} pieces, {el:.1f}s) sha1-verified",
          flush=True)
    announce(base, info_hash, my_id, args.mode, "completed")
    if not args.no_seed_after:
        print("[dl] now seeding for others — Ctrl-C to stop", flush=True)
        try:
            seed_loop(base, meta, args.out, my_id, args.mode,
                      args.direct_ip or get_local_ip(), args.direct_port)
        except KeyboardInterrupt:
            pass
        finally:
            STOP.set()
            announce(base, info_hash, my_id, args.mode, "stopped")


def main():
    p = argparse.ArgumentParser(description="P2P peer (stdlib only)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="Share a file")
    s.add_argument("file")
    s.add_argument("--torrent", required=True)
    s.add_argument("--tracker", default=None)
    s.add_argument("--mode", choices=["auto", "relay", "direct"], default="relay")
    s.add_argument("--direct-port", type=int, default=6881)
    s.add_argument("--direct-ip", default=None,
                   help="advertised direct IP (default: auto-detect)")

    d = sub.add_parser("download", help="Download via torrent file")
    d.add_argument("torrent")
    d.add_argument("--out", required=True)
    d.add_argument("--tracker", default=None)
    d.add_argument("--mode", choices=["auto", "relay", "direct"], default="relay")
    d.add_argument("--direct-port", type=int, default=6882)
    d.add_argument("--direct-ip", default=None)
    d.add_argument("--workers", type=int, default=4)
    d.add_argument("--no-seed-after", action="store_true",
                   help="exit after download instead of seeding")
    a = p.parse_args()
    if a.cmd == "seed":
        cmd_seed(a)
    else:
        cmd_download(a)


if __name__ == "__main__":
    main()

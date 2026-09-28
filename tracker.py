#!/usr/bin/env python3
"""Public tracker + relay. Stdlib only. Outbound-only peers, NAT-safe.

Endpoints:
  GET  /            test UI (static/index.html, phone-friendly)
  GET  /health
  POST /announce  {info_hash, peer_id, mode, direct_ip, direct_port, event}
  GET  /peers?info_hash=
  POST /publish   {name, size, piece_length, pieces[], info_hash}  (magnet store)
  GET  /meta?info_hash=   (fetch metainfo for a magnet link)
  POST /relay/request {info_hash, from_peer_id, to_peer_id, piece_index}
  GET  /relay/inbox?peer_id=&timeout=   (long-poll, seeder side)
  POST /relay/send    {request_id, to_peer_id, piece_index, data_b64}
  GET  /relay/fetch?peer_id=&request_id=&timeout=  (long-poll, leecher side)
"""
import argparse
import hashlib
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ANNOUNCE_INTERVAL = 30
PEER_TTL = 3 * ANNOUNCE_INTERVAL
RELAY_TTL = 60
RELAY_MAX_BYTES = 512 * 1024 * 1024
META_TTL = 24 * 3600
META_MAX_PIECES = 2000

lock = threading.Lock()
peers: dict[str, dict[str, dict]] = {}          # info_hash -> peer_id -> record
inboxes: dict[str, list[dict]] = {}             # seeder peer_id -> pending requests
payloads: dict[str, dict] = {}                  # request_id -> {to_peer_id, piece_index, data_b64, ts, size}
metas: dict[str, dict] = {}                     # info_hash -> {metainfo..., ts}
relay_bytes = 0


def now():
    return time.time()


def prune():
    global relay_bytes
    with lock:
        t = now()
        for ih in list(peers):
            for pid in list(peers[ih]):
                if t - peers[ih][pid]["last_seen"] > PEER_TTL:
                    del peers[ih][pid]
            if not peers[ih]:
                del peers[ih]
        for rid in list(payloads):
            if t - payloads[rid]["ts"] > RELAY_TTL:
                relay_bytes -= payloads[rid]["size"]
                del payloads[rid]
        for ih in list(metas):
            if t - metas[ih]["ts"] > META_TTL:
                del metas[ih]


def valid_meta(body: dict) -> str | None:
    """Return error string, or None if the published metainfo is acceptable."""
    try:
        pieces = body["pieces"]
        if not isinstance(pieces, list) or not pieces or len(pieces) > META_MAX_PIECES:
            return f"pieces must be 1..{META_MAX_PIECES} hashes"
        if any(not isinstance(h, str) or len(h) != 40 for h in pieces):
            return "each piece must be a 40-char sha1 hex"
        if not isinstance(body.get("size"), int) or body["size"] < 0:
            return "bad size"
        if not isinstance(body.get("piece_length"), int) or not 1024 <= body["piece_length"] <= 8 * 1024 * 1024:
            return "bad piece_length"
        if hashlib.sha1("".join(pieces).encode()).hexdigest() != body.get("info_hash"):
            return "info_hash does not match piece hashes"
    except (KeyError, TypeError, AttributeError):
        return "missing/invalid fields"
    return None


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class Handler(BaseHTTPRequestHandler):
    server_version = "p2p-tracker/1.0"

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser/phone cancelled a long-poll; harmless

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client went away during long-poll; harmless

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return {}
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except (ValueError, OSError):
            return {}

    def _send_file(self, filename, ctype):
        try:
            with open(os.path.join(STATIC_DIR, filename), "rb") as f:
                body = f.read()
        except FileNotFoundError:
            return self._send(404, {"error": "no UI built"})
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        prune()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._send_file("index.html", "text/html; charset=utf-8")
        if u.path == "/health":
            return self._send(200, {"ok": True})
        if u.path == "/peers":
            ih = q.get("info_hash", [""])[0]
            with lock:
                plist = [{"peer_id": pid, "mode": r.get("mode", "relay"),
                          "direct_ip": r.get("direct_ip"), "direct_port": r.get("direct_port")}
                         for pid, r in peers.get(ih, {}).items()]
            return self._send(200, {"peers": plist, "interval": ANNOUNCE_INTERVAL})
        if u.path == "/meta":
            ih = q.get("info_hash", [""])[0]
            with lock:
                m = metas.get(ih)
                meta = {k: v for k, v in m.items() if k != "ts"} if m else None
            if not meta:
                return self._send(404, {"error": "unknown torrent — ask the sharer to re-share (links expire after 24h)"})
            return self._send(200, meta)
        if u.path == "/relay/inbox":
            pid = q.get("peer_id", [""])[0]
            timeout = min(float(q.get("timeout", ["25"])[0]), 30)
            deadline = now() + timeout
            while now() < deadline:
                with lock:
                    items = inboxes.get(pid, [])
                    if items:
                        inboxes[pid] = []
                        return self._send(200, items)
                time.sleep(0.25)
            return self._send(200, [])
        if u.path == "/relay/fetch":
            pid = q.get("peer_id", [""])[0]
            rid = q.get("request_id", [""])[0]
            timeout = min(float(q.get("timeout", ["25"])[0]), 30)
            deadline = now() + timeout
            while now() < deadline:
                with lock:
                    p = payloads.get(rid)
                    if p and p["to_peer_id"] == pid:
                        del payloads[rid]
                        global relay_bytes
                        relay_bytes -= p["size"]
                        return self._send(200, {"piece_index": p["piece_index"],
                                               "data_b64": p["data_b64"]})
                time.sleep(0.25)
            try:
                self.send_response(408)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"timeout"}')
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        prune()
        u = urlparse(self.path)
        if u.path == "/announce":
            body = self._read_json()
            ih, pid = body.get("info_hash", ""), body.get("peer_id", "")
            if not ih or not pid:
                return self._send(400, {"error": "info_hash and peer_id required"})
            if body.get("event") == "stopped":
                with lock:
                    peers.get(ih, {}).pop(pid, None)
                return self._send(200, {"peers": [], "interval": ANNOUNCE_INTERVAL})
            with lock:
                peers.setdefault(ih, {})[pid] = {
                    "mode": body.get("mode", "relay"),
                    "direct_ip": body.get("direct_ip"),
                    "direct_port": body.get("direct_port"),
                    "last_seen": now(),
                }
                plist = [{"peer_id": k, "mode": v.get("mode", "relay"),
                          "direct_ip": v.get("direct_ip"), "direct_port": v.get("direct_port")}
                         for k, v in peers[ih].items() if k != pid]
            return self._send(200, {"peers": plist, "interval": ANNOUNCE_INTERVAL,
                                    "relay": True})
        if u.path == "/publish":
            body = self._read_json()
            err = valid_meta(body)
            if err:
                return self._send(400, {"error": err})
            with lock:
                metas[body["info_hash"]] = {
                    "name": body.get("name", ""), "size": body["size"],
                    "piece_length": body["piece_length"], "pieces": body["pieces"],
                    "info_hash": body["info_hash"], "ts": now()}
            return self._send(200, {"ok": True})
        if u.path == "/relay/request":
            body = self._read_json()
            rid = uuid.uuid4().hex[:16]
            req = {"request_id": rid, "from_peer_id": body.get("from_peer_id", ""),
                   "info_hash": body.get("info_hash", ""),
                   "piece_index": body.get("piece_index")}
            with lock:
                inboxes.setdefault(body.get("to_peer_id", ""), []).append(req)
            return self._send(200, {"request_id": rid})
        if u.path == "/relay/send":
            global relay_bytes
            body = self._read_json()
            rid = body.get("request_id", "")
            data_b64 = body.get("data_b64", "")
            size = len(data_b64)
            with lock:
                if relay_bytes + size > RELAY_MAX_BYTES:
                    return self._send(503, {"error": "relay full"})
                payloads[rid] = {"to_peer_id": body.get("to_peer_id", ""),
                                 "piece_index": body.get("piece_index"),
                                 "data_b64": data_b64, "ts": now(), "size": size}
                relay_bytes += size
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    a = p.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"tracker+relay on {a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

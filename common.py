"""Shared helpers: hashing, metainfo, HTTP JSON client. Stdlib only."""
import base64
import hashlib
import json
import os
import urllib.request
import urllib.parse

DEFAULT_PIECE_LENGTH = 131072
ANNOUNCE_INTERVAL = 30


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def split_pieces(data: bytes, piece_length: int):
    return [data[i:i + piece_length] for i in range(0, len(data), piece_length)]


def compute_info_hash(piece_hashes: list[str]) -> str:
    return hashlib.sha1("".join(piece_hashes).encode()).hexdigest()


def build_metainfo(name: str, size: int, piece_length: int,
                   pieces: list[str], tracker_url: str) -> dict:
    return {
        "name": name,
        "size": size,
        "piece_length": piece_length,
        "pieces": pieces,
        "info_hash": compute_info_hash(pieces),
        "tracker_url": tracker_url,
    }


def load_torrent(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save_torrent(meta: dict, path: str):
    with open(path, "w") as f:
        json.dump(meta, f, indent=2)


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode()


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode())


# ---------- magnet links ----------
# Web form (phone-friendly, opens the test UI):  {tracker_url}/?m={info_hash}
# Text form (CLI paste):  magnet:?xt=urn:p2p:{info_hash}&tr={tracker_url}&dn={name}

def build_magnet(info_hash: str, tracker_url: str, name: str = "") -> str:
    q = f"xt=urn:p2p:{info_hash}&tr={tracker_url}"
    if name:
        q += "&dn=" + urllib.parse.quote(name)
    return "magnet:?" + q


def web_link(tracker_url: str, info_hash: str) -> str:
    return f"{tracker_url.rstrip('/')}/?m={info_hash}"


def parse_magnet(s: str) -> dict:
    s = s.strip().strip("\"'")
    if s.startswith("magnet:?"):
        q = urllib.parse.parse_qs(s[len("magnet:?"):])
        xt = (q.get("xt") or [""])[0]
        if not xt.startswith("urn:p2p:"):
            raise ValueError("bad magnet: missing xt=urn:p2p:<info_hash>")
        return {"info_hash": xt[len("urn:p2p:"):],
                "tracker_url": (q.get("tr") or [""])[0],
                "name": (q.get("dn") or [""])[0]}
    if "/?m=" in s or s.startswith("?m="):
        u = urllib.parse.urlparse(s if "://" in s else "http://x/" + s.lstrip("/"))
        ih = (urllib.parse.parse_qs(u.query).get("m") or [""])[0]
        base = "" if s.startswith("?m=") else f"{u.scheme}://{u.netloc}"
        if not ih:
            raise ValueError("bad link: missing ?m=<info_hash>")
        return {"info_hash": ih, "tracker_url": base, "name": ""}
    raise ValueError("not a magnet or share link")


def publish_meta(base: str, meta: dict, timeout: int = 30) -> dict:
    return http_post_json(f"{base.rstrip('/')}/publish", {
        "name": meta["name"], "size": meta["size"],
        "piece_length": meta["piece_length"], "pieces": meta["pieces"],
        "info_hash": meta["info_hash"]}, timeout=timeout)


def fetch_meta(base: str, info_hash: str, timeout: int = 30) -> dict:
    return http_get_json(f"{base.rstrip('/')}/meta", {"info_hash": info_hash},
                         timeout=timeout)


def http_post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
        return json.loads(body) if body else {}


def http_get_json(url: str, params: dict | None = None, timeout: int = 30) -> dict | list:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout + 5) as r:
        body = r.read().decode()
        return json.loads(body) if body else {}

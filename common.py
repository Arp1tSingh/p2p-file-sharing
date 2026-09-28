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

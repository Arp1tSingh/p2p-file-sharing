#!/usr/bin/env python3
"""Create a .torrent.json metainfo file. Stdlib only."""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DEFAULT_PIECE_LENGTH, build_metainfo, save_torrent


def main():
    p = argparse.ArgumentParser(description="Create torrent metainfo")
    p.add_argument("file", help="File to share")
    p.add_argument("--tracker", required=True, help="Public tracker URL, e.g. https://...onrender.com")
    p.add_argument("--piece-length", type=int, default=DEFAULT_PIECE_LENGTH)
    p.add_argument("--out", required=True, help="Output .torrent.json path")
    a = p.parse_args()

    tracker = a.tracker.rstrip("/")
    with open(a.file, "rb") as f:
        data = f.read()
    pieces = []
    for i in range(0, len(data) if data else 1, a.piece_length):
        chunk = data[i:i + a.piece_length] if data else b""
        pieces.append(hashlib.sha1(chunk).hexdigest())
        if not data:
            break
    meta = build_metainfo(os.path.basename(a.file), len(data),
                          a.piece_length, pieces, tracker)
    save_torrent(meta, a.out)
    print(f"wrote {a.out}: {len(pieces)} pieces, info_hash={meta['info_hash']}")


if __name__ == "__main__":
    main()

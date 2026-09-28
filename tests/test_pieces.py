import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common import split_pieces, sha1_hex, build_metainfo, compute_info_hash
import hashlib

data = os.urandom(300_000)
pl = 131072
chunks = split_pieces(data, pl)
assert b"".join(chunks) == data
hashes = [sha1_hex(c) for c in chunks]
assert compute_info_hash(hashes) == hashlib.sha1("".join(hashes).encode()).hexdigest()
meta = build_metainfo("f.bin", len(data), pl, hashes, "http://x")
assert meta["info_hash"] == compute_info_hash(hashes)
# empty file edge
assert split_pieces(b"", pl) == []
print("test_pieces OK")

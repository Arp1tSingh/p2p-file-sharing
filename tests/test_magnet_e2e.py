"""E2E over magnet: seed publishes meta, leecher downloads via --magnet (no JSON)."""
import os, sys, tempfile, threading, time, subprocess, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common import http_get_json, build_magnet
import tracker
from http.server import ThreadingHTTPServer

srv = ThreadingHTTPServer(("127.0.0.1", 0), tracker.Handler)
srv.daemon_threads = True
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
assert http_get_json(f"{base}/health") == {"ok": True}

tmp = tempfile.mkdtemp()
src = os.path.join(tmp, "orig.bin")
data = os.urandom(300_000)
open(src, "wb").write(data)
torrent = os.path.join(tmp, "f.torrent.json")
out = os.path.join(tmp, "got.bin")
ROOT = os.path.join(os.path.dirname(__file__), "..")

subprocess.run([sys.executable, "make_torrent.py", src, "--tracker", base,
                "--out", torrent], cwd=ROOT, check=True, capture_output=True)
import json
meta = json.load(open(torrent))
magnet = build_magnet(meta["info_hash"], base, meta["name"])

seed = subprocess.Popen([sys.executable, "peer.py", "seed", src,
                         "--torrent", torrent, "--mode", "relay"], cwd=ROOT,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(2)
try:
    # seeder must have published metainfo for the magnet to resolve
    got = http_get_json(f"{base}/meta", {"info_hash": meta["info_hash"]})
    assert got["pieces"] == meta["pieces"], "published meta mismatch"
    r = subprocess.run([sys.executable, "peer.py", "download", "--magnet", magnet,
                        "--out", out, "--mode", "relay", "--no-seed-after"],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert open(out, "rb").read() == data, "byte mismatch"
    print("test_magnet_e2e OK")
finally:
    seed.terminate()
    seed.wait(timeout=10)
    srv.shutdown()

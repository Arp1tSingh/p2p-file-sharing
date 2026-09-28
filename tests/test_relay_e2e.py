"""E2E via real tracker+relay on localhost. Spawns tracker thread, seeds in thread."""
import os, sys, tempfile, threading, time, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common import http_get_json
import tracker
from http.server import ThreadingHTTPServer

srv = ThreadingHTTPServer(("127.0.0.1", 0), tracker.Handler)
srv.daemon_threads = True
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
assert http_get_json(f"http://127.0.0.1:{port}/health") == {"ok": True}
base = f"http://127.0.0.1:{port}"

tmp = tempfile.mkdtemp()
src = os.path.join(tmp, "orig.bin")
data = os.urandom(400_000)
open(src, "wb").write(data)
torrent = os.path.join(tmp, "f.torrent.json")
out = os.path.join(tmp, "got.bin")
ROOT = os.path.join(os.path.dirname(__file__), "..")

r = subprocess.run([sys.executable, "make_torrent.py", src, "--tracker", base,
                    "--out", torrent], cwd=ROOT, capture_output=True, text=True)
assert r.returncode == 0, r.stderr

seed = subprocess.Popen([sys.executable, "peer.py", "seed", src,
                         "--torrent", torrent, "--mode", "relay"], cwd=ROOT,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(2)
try:
    r = subprocess.run([sys.executable, "peer.py", "download", torrent,
                        "--out", out, "--mode", "relay", "--no-seed-after"],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert open(out, "rb").read() == data, "byte mismatch"
    print("test_relay_e2e OK")
finally:
    seed.terminate()
    seed.wait(timeout=10)
    srv.shutdown()

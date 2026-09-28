#!/bin/bash
# Localhost relay smoke test: tracker + seeder + leecher, byte compare.
set -e
cd "$(dirname "$0")/.."
PORT=${PORT:-8000}
BASE="http://127.0.0.1:$PORT"
mkdir -p data downloads
head -c 1048576 /dev/urandom > data/sample.bin
python3 tracker.py --port $PORT & TRK=$!
sleep 1
curl -sf $BASE/health > /dev/null
python3 make_torrent.py data/sample.bin --tracker $BASE --out sample.torrent.json
python3 peer.py seed data/sample.bin --torrent sample.torrent.json --mode relay & SEED=$!
sleep 2
python3 peer.py download sample.torrent.json --out downloads/sample.bin --mode relay --no-seed-after
sha256sum data/sample.bin downloads/sample.bin
test "$(sha256sum < data/sample.bin)" = "$(sha256sum < downloads/sample.bin)" && echo "DEMO OK: files identical"
kill $SEED $TRK

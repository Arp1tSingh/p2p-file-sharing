FROM python:3.12-slim
WORKDIR /app
COPY common.py tracker.py peer.py make_torrent.py ./
COPY static/ ./static/
CMD ["python", "tracker.py", "--host", "0.0.0.0", "--port", "8000"]
EXPOSE 8000

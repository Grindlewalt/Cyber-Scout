# Cyber-Scout worker: polls TBA, downloads match videos, tracks robots, writes site JSON.
FROM python:3.12-slim
COPY --from=denoland/deno:bin-2.5.6 /deno /usr/local/bin/deno
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# ffmpeg CLI: live-stream clip cutting / remux (live/livestream.py, recorder service) and URL frame grabs (layout.py)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install "onnxruntime-gpu[cuda,cudnn]==1.31.0" "opencv-python-headless==5.0.0.93" "av==19.0.1" \
        "numpy==2.5.4" "scipy==1.18.1" "networkx==3.7" "pupil-apriltags==1.0.4.post11" "yt-dlp[default]"
# ^ line kept byte-identical so the 3 GB layer stays cached (the server's root disk is small); the hash-pinned
#   install below replaces that yt-dlp with the pinned version.
# yt-dlp + its deps pinned by version AND sha256 (no unpinned / runtime `pip install -U`: security audit finding 3).
# YouTube breaks old yt-dlp now and then: bump live/requirements-ytdlp.txt (instructions inside) and rebuild.
COPY live/requirements-ytdlp.txt /tmp/requirements-ytdlp.txt
RUN pip install --require-hashes --no-deps -r /tmp/requirements-ytdlp.txt && rm /tmp/requirements-ytdlp.txt
WORKDIR /app
COPY src/ src/
COPY live/ live/
COPY events/ events/
ENV DATA=/data
ENTRYPOINT ["sh", "/app/live/entrypoint.sh"]

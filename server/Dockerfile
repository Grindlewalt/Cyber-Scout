# Cyber-Scout platform API (accounts, settings, shared scouting data). Build context: repo root.

# Version for GET /app/v1/info (clients refresh their copy of the web app when it changes); the image has no .git.
# Build arg GIT_SHA (compose passes ${GIT_SHA}: `GIT_SHA=$(git -C app rev-parse --short HEAD) docker compose build`),
# else a VERSION file in the repo root, else "src-<hash of the shipped code>" so any code change changes it.
FROM python:3.12-slim AS version
ARG GIT_SHA=""
WORKDIR /v
COPY server/ server/
COPY web/ web/
COPY src/ src/
COPY live/*.py live/
COPY server/requirements.txt VERSIO[N] meta/
RUN v="$(printf %s "$GIT_SHA" | tr -cd 'A-Za-z0-9._-' | cut -c1-40)"; \
    [ -n "$v" ] || v="$(cat meta/VERSION 2>/dev/null | head -n1 | tr -cd 'A-Za-z0-9._-' | cut -c1-40)"; \
    [ -n "$v" ] || v="src-$(find server web src live -type f ! -name '*.pyc' ! -path '*/__pycache__/*' ! -path 'server/tests/*' -print0 \
                             | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-10)"; \
    echo "$v" > /VERSION && cat /VERSION

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DATA=/data
WORKDIR /srv
COPY server/requirements.txt server/requirements.txt
RUN pip install -r server/requirements.txt
COPY server/ server/
COPY live/sources.py live/layout.py live/
RUN rm -rf server/tests
COPY --from=version /VERSION /srv/VERSION
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/app/v1/health', timeout=4)"
ENTRYPOINT ["sh", "/srv/server/entrypoint.sh"]

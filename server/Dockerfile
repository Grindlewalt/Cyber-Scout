# Cyber-Scout platform API (accounts, settings, shared scouting data). Build context: repo root.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DATA=/data
WORKDIR /srv
COPY server/requirements.txt server/requirements.txt
RUN pip install -r server/requirements.txt
COPY server/ server/
RUN rm -rf server/tests
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/app/v1/health', timeout=4)"
ENTRYPOINT ["sh", "/srv/server/entrypoint.sh"]

# Cyber-Scout server on a Mac (no Docker, no sudo)

Used for the 2026wagg offseason when atomosserver was down: the M2 MacBook (`ssh m2`) runs api + web + worker +
recorder as launchd LaunchAgents under `~/CyberScout/server/`, reachable at `http://<tailscale-ip>:7560`
(nginx listens on the Tailscale address + loopback only).

One-time prerequisites in `~/CyberScout/server/`: `bin/` (uv, deno, static ffmpeg + ffprobe), `venv/` (uv Python 3.12
with `server/requirements.txt` + the worker packages of the root `Dockerfile`, CPU `onnxruntime`), `nginx/` (nginx
built from source: `--with-http_realip_module --with-http_auth_request_module --with-http_gzip_static_module`).

- Deploy / update: `deploy/macos/deploy.sh [host]` (git archive HEAD -> `app/`, then `install-server.sh`).
- Logs: `~/CyberScout/server/logs/` (`com.cyberscout.{api,web,worker,recorder}.log`, nginx access/error).
- Restart one: `launchctl kickstart -k gui/$(id -u)/com.cyberscout.worker`.
- Extra environment (e.g. `PAUSE_PROCESSING=1`, `LIVE_ANY_DATE=1`): `~/CyberScout/server/server.env`.
- Inference: `FORCE_CPU=1` (no CUDA) + `ORT_COREML=1` (detector on the Apple GPU / Neural Engine via Core ML).
- Model: `data/models/robot.onnx` (see `live/train/README.md`).

"""Video sources / layouts / calibration endpoints -- OWNED BY AGENT B (stub created by the platform agent).

Mounted by server/main.py under /app/v1 (so `@router.get("/events/{ev}/sources")` -> /app/v1/events/{ev}/sources).
Conventions (see server/deps.py and server/README.md):
  * auth: `user: User = Depends(require_role("admin"))` (video setup is admin; reads can be "viewer").
    require_role also enforces the X-CSRF-Token header on POST/PUT/PATCH/DELETE.
  * event key: `ev: str = Depends(valid_event)`.
  * DB: `db = Depends(get_db)` (sqlite3 connection, Row factory); `with tx(db): ...` for writes;
    own tables via `add_schema("CREATE TABLE IF NOT EXISTS sources (...)")` at import time.
  * files for the worker: `atomic_write_json(os.path.join(cfg.DATA, "events", ev, "layout.json"), obj)`.
"""
from fastapi import APIRouter

router = APIRouter(tags=["sources"])

import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone

import config


def _session_dir(session_id: str) -> str:
    safe_id = "".join(c for c in session_id if c.isalnum() or c == "-")
    return os.path.join(config.SESSION_DIR, safe_id)


def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


class Session:
    def __init__(self, session_id: str):
        self.id = session_id
        self.dir = _session_dir(session_id)
        self.pages_dir = os.path.join(self.dir, "pages")

    # -- lifecycle --------------------------------------------------
    def exists(self) -> bool:
        return os.path.isdir(self.dir)

    def create(self, checklist_lines, line_states, meta):
        os.makedirs(self.pages_dir, exist_ok=True)
        _write_json(os.path.join(self.dir, "checklist.json"), checklist_lines)
        _write_json(os.path.join(self.dir, "lines.json"), line_states)
        _write_json(os.path.join(self.dir, "pages.json"), [])
        meta = dict(meta)
        meta["created_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(os.path.join(self.dir, "meta.json"), meta)

    def touch(self):
        meta = self.meta()
        meta["last_active_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(os.path.join(self.dir, "meta.json"), meta)

    def delete(self):
        if self.exists():
            shutil.rmtree(self.dir, ignore_errors=True)

    # -- data access --------------------------------------------------
    def meta(self):
        return _read_json(os.path.join(self.dir, "meta.json"), {})

    def checklist(self):
        return _read_json(os.path.join(self.dir, "checklist.json"), [])

    def line_states(self):
        return _read_json(os.path.join(self.dir, "lines.json"), {})

    def save_line_states(self, states):
        _write_json(os.path.join(self.dir, "lines.json"), states)

    def pages(self):
        return _read_json(os.path.join(self.dir, "pages.json"), [])

    def save_pages(self, pages):
        _write_json(os.path.join(self.dir, "pages.json"), pages)

    def page_image_path(self, page_id: str, variant: str = "processed") -> str:
        return os.path.join(self.pages_dir, f"{page_id}_{variant}.jpg")


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def get_session(session_id: str) -> Session:
    return Session(session_id)


def purge_expired():
    """Delete session directories older than SESSION_TTL_HOURS. Call this
    periodically (e.g. from a scheduled beat, or opportunistically on
    each new-session request) — there is no long-running worker in this
    single-process app by default."""
    if not os.path.isdir(config.SESSION_DIR):
        return
    cutoff = time.time() - config.SESSION_TTL_HOURS * 3600
    for name in os.listdir(config.SESSION_DIR):
        path = os.path.join(config.SESSION_DIR, name)
        if not os.path.isdir(path):
            continue
        meta_path = os.path.join(path, "meta.json")
        try:
            mtime = os.path.getmtime(meta_path) if os.path.exists(meta_path) else os.path.getmtime(path)
        except OSError:
            continue
        if mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)

"""Reproducible dataset for the combined-filter / paging / export exercise.

The default ``seed`` catalog has no "harbor" keyword and no ``approved`` /
``campaign`` / ``internal`` tags, so the business scenario in the task cannot be
reproduced against it. This module builds a small, deterministic catalog whose
expected results are known in advance. Media is generated in-memory with Pillow
(no bundled binaries and no dedup collisions: every asset gets a unique image).

Use it two ways::

    python -m keepstack seed-quiz          # build it in the configured data dir
    from keepstack import quiz_data
    info = quiz_data.build_dataset()       # used by the automated tests

The returned ``info`` dict exposes the collection ids, the UUIDs of key assets
and the exact expected match count, so tests never hard-code database ids.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from . import ingest
from .audit import now_iso
from .auth import hash_password
from .db import get_conn, init_db

# --- The scenario -----------------------------------------------------------
# keyword "harbor" + collection "Harbor Campaign" + include(approved,campaign)
# + exclude(internal) + status=active. Exactly these eight assets match:
_MATCHES = [
    ("m1", "Harbor sunrise overview", ["approved", "campaign"], "2026-01-01T09:00:00Z"),
    ("m2", "Harbor cranes at dawn", ["approved", "campaign", "external"], "2026-01-02T09:00:00Z"),
    ("m3", "Harbor ferry terminal", ["campaign", "approved"], "2026-01-03T09:00:00Z"),
    ("m4", "Harbor night lights", ["approved", "campaign"], "2026-01-04T12:00:00Z"),
    ("m5", 'Harbor, the "old" dock, revisited', ["approved", "campaign"], "2026-01-05T09:00:00Z"),
    ("m6", "海港调研 Harbor survey", ["approved", "campaign"], "2026-01-06T09:00:00Z"),
    ("m7", "Harbor warehouse\nrow two", ["approved", "campaign"], "2026-01-07T09:00:00Z"),
    # m8 shares created_at with m4 on purpose: the paging tests rely on it.
    ("m8", "Harbor final selection", ["approved", "campaign"], "2026-01-04T12:00:00Z"),
]

# (key, title, tags, created_at, collection, extra kwargs applied to the row)
_NEGATIVES = [
    ("n_internal", "Harbor internal briefing", ["approved", "campaign", "internal"],
     "2026-01-08T09:00:00Z", "main", {}),
    ("n_no_campaign", "Harbor draft", ["approved"], "2026-01-09T09:00:00Z", "main", {}),
    ("n_no_approved", "Harbor draft two", ["campaign"], "2026-01-10T09:00:00Z", "main", {}),
    ("n_other_col", "Harbor other collection", ["approved", "campaign"],
     "2026-01-11T09:00:00Z", "other", {}),
    ("n_deleted", "Harbor deleted asset", ["approved", "campaign"],
     "2026-01-12T09:00:00Z", "main", {"status": "deleted"}),
    ("n_no_keyword", "Mountain trail poster", ["approved", "campaign"],
     "2026-01-13T09:00:00Z", "main", {}),
]

# A non-image asset that would match the scenario in every other way; it is
# placed in the *other* collection so the main scenario stays at 8 matches.
_AUDIO = ("a_audio", "Harbor audio clip", ["approved", "campaign"],
          "2026-01-14T09:00:00Z")

_SPECIAL_DESCRIPTION = 'Quoted "value", a comma, and a\nsecond line — 中文说明'


def _image_bytes(seed: int) -> bytes:
    """A distinct small JPEG per seed so content-addressable dedup never fires."""
    img = Image.new("RGB", (320 + seed % 17, 200 + seed % 13), (seed % 256, (seed * 7) % 256, (seed * 13) % 256))
    ImageDraw.Draw(img).rectangle([10, 10, 80, 80], fill=((seed * 3) % 256, 90, 140))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _set_tags(conn, asset_id: int, names: list[str]) -> None:
    for name in names:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        tid = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source) VALUES (?,?, 'manual')",
                     (asset_id, tid))


def build_dataset() -> dict:
    """Create the deterministic catalog and return ids/uuids for assertions.

    Idempotent-ish: it creates its own collections each call; call it once per
    fresh database (as the tests do).
    """
    init_db()
    conn = get_conn()
    if not conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
        conn.execute(
            "INSERT INTO users (username, email, full_name, password_hash, role, is_active, created_at) "
            "VALUES ('admin','admin@example.org','Administrator',?, 'admin', 1, ?)",
            (hash_password("admin"), now_iso()))
        conn.commit()
    admin = dict(conn.execute("SELECT * FROM users WHERE username='admin'").fetchone())

    cols = {}
    for name in ("Harbor Campaign", "Other Collection"):
        import uuid as uuidlib
        cur = conn.execute(
            "INSERT INTO collections (uuid, name, description, kind, created_by, created_at) "
            "VALUES (?,?,?,'folder',?,?)",
            (str(uuidlib.uuid4()), name, "quiz dataset", admin["id"], now_iso()))
        cols[name] = cur.lastrowid
    conn.commit()

    info = {
        "collection_id": cols["Harbor Campaign"],
        "other_collection_id": cols["Other Collection"],
        "expected_match_count": len(_MATCHES),
        "uuids": {},
    }

    seed = 0

    def _make(title, tags, created_at, collection_id, row_updates=None, description=None):
        nonlocal seed
        seed += 1
        asset = ingest.ingest_stream(
            io.BytesIO(_image_bytes(seed)), f"quiz_{seed}.jpg",
            user=admin, run_ai=False, title=title)
        updates = dict(row_updates or {})
        updates["created_at"] = created_at
        updates["updated_at"] = created_at
        if description is not None:
            updates["description"] = description
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE assets SET {sets} WHERE id = ?", [*updates.values(), asset["id"]])
        _set_tags(conn, asset["id"], tags)
        conn.execute(
            "INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) VALUES (?,?,?)",
            (collection_id, asset["id"], now_iso()))
        conn.commit()
        ingest.reindex_fts(asset["id"])
        return asset

    for key, title, tags, created in _MATCHES:
        desc = _SPECIAL_DESCRIPTION if key == "m5" else None
        asset = _make(title, tags, created, cols["Harbor Campaign"], description=desc)
        info["uuids"][key] = asset["uuid"]

    for key, title, tags, created, col, updates in _NEGATIVES:
        col_id = cols["Harbor Campaign"] if col == "main" else cols["Other Collection"]
        asset = _make(title, tags, created, col_id, updates)
        info["uuids"][key] = asset["uuid"]

    asset = _make(_AUDIO[1], _AUDIO[2], _AUDIO[3], cols["Other Collection"])
    conn.execute("UPDATE assets SET media_type = 'audio', mime_type = 'audio/mpeg', ext = 'mp3' WHERE id = ?",
                 (asset["id"],))
    conn.commit()
    ingest.reindex_fts(asset["id"])
    info["uuids"][_AUDIO[0]] = asset["uuid"]

    return info

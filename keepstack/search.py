"""Search and discovery.

Three complementary modes over one catalog:

  * keyword  - FTS5 full-text across title, description, tags, metadata
  * filter   - combined structured filters (collection, include/exclude tags,
               media type) intersected with the keyword condition
  * semantic - embedding cosine ranking ("natural language" / more-like-this)

``_query_parts`` is the single source of truth for the WHERE/JOIN/ORDER used by
both the paged list (:func:`search`) and the full-result export
(:func:`search_all`). Keeping one builder guarantees that "export all matches"
is identical to the list except for the LIMIT/OFFSET window.
"""
from __future__ import annotations

import re
from typing import Iterator, Optional

from . import ai
from .db import get_conn

MAX_LIMIT = 200
DEFAULT_LIMIT = 60
DEFAULT_SORT = "newest"

# Every order ends with a unique tie-breaker (a.id). Without it, assets sharing
# a created_at can swap places between two queries and be skipped or repeated
# when paging. julianday() normalises ISO offsets (Z vs +00:00), so two stamps
# for the same instant tie and fall through to the id tie-breaker instead of
# sorting by their raw text. An expression index covers this sort (see db.py).
SORTS = {
    "newest": "julianday(a.created_at) DESC, a.id DESC",
    "oldest": "julianday(a.created_at) ASC, a.id ASC",
    "name": "a.title COLLATE NOCASE ASC, a.id ASC",
    "largest": "a.size DESC, a.id DESC",
    # Optional: relevance ranking for keyword queries (not used by the UI).
    "relevance": "bm25(asset_fts) ASC, a.id ASC",
}


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query ('' if nothing usable).

    Punctuation-only input sanitises to no terms; emitting an empty MATCH is a
    syntax error in FTS5. Returning '' lets callers treat it as no restriction.
    """
    terms = [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in q).split() if t]
    return " ".join(f'"{t}"*' for t in terms)


def _norm_tags(values) -> list[str]:
    """Lowercase (matching how tags are stored), de-duplicate, drop blanks.

    Accepts a string, a list of strings, or comma-separated strings (both the
    ASCII comma and the full-width Chinese comma) so repeated query params and
    a single delimited value both work.
    """
    out: list[str] = []
    if not values:
        return out
    if isinstance(values, str):
        values = [values]
    for value in values:
        if value is None:
            continue
        for part in re.split(r"[,，]", str(value)):
            name = part.strip().lower()
            if name and name not in out:
                out.append(name)
    return out


def _clip_page(limit, offset) -> tuple[int, int]:
    """Coerce untrusted pagination values into a safe window."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    return max(1, min(limit, MAX_LIMIT)), max(0, offset)


def _query_parts(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    include_tags=None,
    exclude_tags=None,
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: str = "newest",
) -> tuple[str, str, list, str]:
    """Build the shared JOIN / WHERE / params / ORDER clauses."""
    where = ["a.status = ?"]
    params: list = [status]
    join = ""
    # Placeholders inside JOIN ... ON appear BEFORE the WHERE placeholders in
    # the final SQL text, so their bound values must be returned first. The
    # original implementation appended the collection id last, which shifted
    # every condition by one and made collection filtering match nothing.
    join_params: list = []

    fts = _fts_query(q) if q and q.strip() else ""
    if fts:
        join += " JOIN asset_fts ON asset_fts.rowid = a.id"
        where.append("asset_fts MATCH ?")
        params.append(fts)

    if media_type:
        where.append("a.media_type = ?")
        params.append(media_type)

    # The legacy single ``tag`` parameter is folded into the include set.
    include = _norm_tags(include_tags)
    if tag:
        for name in _norm_tags([tag]):
            if name not in include:
                include.insert(0, name)
    exclude = _norm_tags(exclude_tags)

    # "Contain ALL include tags": one correlated EXISTS per tag, which avoids
    # the duplicate rows a plain JOIN would create for multi-tag assets.
    for name in include:
        where.append(
            "EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            "WHERE at.asset_id = a.id AND t.name = ?)"
        )
        params.append(name)

    # "Exclude if ANY exclude tag": a single NOT EXISTS over the whole set.
    if exclude:
        marks = ",".join("?" for _ in exclude)
        where.append(
            "NOT EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            f"WHERE at.asset_id = a.id AND t.name IN ({marks}))"
        )
        params.extend(exclude)

    if collection_id:
        try:
            cid = int(collection_id)
        except (TypeError, ValueError):
            cid = 0  # an invalid collection simply matches nothing
        join += " JOIN collection_assets ca ON ca.asset_id = a.id AND ca.collection_id = ?"
        join_params.append(cid)

    order = SORTS.get(sort, SORTS[DEFAULT_SORT])
    # Relevance only has meaning when an FTS match is part of the query.
    if order.startswith("bm25") and not fts:
        order = SORTS[DEFAULT_SORT]
    return join, " AND ".join(where), [*join_params, *params], order


def search(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    include_tags=None,
    exclude_tags=None,
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: str = "newest",
    mode: str = "keyword",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    conn = get_conn()

    # Semantic mode ranks the candidate pool by embedding similarity.
    if mode == "semantic" and q and q.strip():
        return _semantic_search(q, media_type, status, limit, offset)

    join, where_sql, params, order = _query_parts(
        q=q, media_type=media_type, tag=tag, include_tags=include_tags,
        exclude_tags=exclude_tags, collection_id=collection_id,
        status=status, sort=sort,
    )
    limit, offset = _clip_page(limit, offset)

    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM assets a{join} WHERE {where_sql}", params
    ).fetchone()["c"]

    rows = [dict(r) for r in conn.execute(
        f"""SELECT a.* FROM assets a{join} WHERE {where_sql}
            ORDER BY {order} LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()]
    # Attach tags in one batched query so cards can show why an asset matched.
    tags = _tag_names_by_asset([r["id"] for r in rows]) if rows else {}
    for r in rows:
        r["tags"] = tags.get(r["id"], [])

    return {
        "total": total,
        "items": [_card(r) for r in rows],
        "facets": _facets(status),
        "mode": mode,
        "limit": limit,
        "offset": offset,
        "sort": sort,
    }


ROW_BATCH = 500  # export reads rows in bounded batches, well under SQLite's var limit


def _tag_names_by_asset(asset_ids: list[int]) -> dict[int, list[str]]:
    """Fetch tag names for a batch of asset ids, chunked and order-stable."""
    out: dict[int, list[str]] = {}
    conn = get_conn()
    for start in range(0, len(asset_ids), 400):
        chunk = asset_ids[start:start + 400]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT at.asset_id, t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            f"WHERE at.asset_id IN ({marks}) ORDER BY t.name", chunk,
        ):
            out.setdefault(row["asset_id"], []).append(row["name"])
    return out


def count_matches(filters: dict) -> int:
    """Count all matching rows without materialising them."""
    join, where_sql, params, _ = _query_parts(**filters)
    return get_conn().execute(
        f"SELECT COUNT(*) AS c FROM assets a{join} WHERE {where_sql}", params
    ).fetchone()["c"]


def iter_rows(filters: dict, batch_size: int = ROW_BATCH) -> Iterator[dict]:
    """Yield every matching row in batches (same filters/order as the list).

    This is the export path: it never holds the whole result set in memory.
    ``filters`` may include ``sort`` (used for ordering) but ``status`` is
    pinned by the caller. Tags are read live so recent edits are reflected.
    """
    conn = get_conn()
    join, where_sql, params, order = _query_parts(**filters)
    offset = 0
    while True:
        rows = conn.execute(
            f"SELECT a.* FROM assets a{join} WHERE {where_sql} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, batch_size, offset],
        ).fetchall()
        if not rows:
            return
        tags = _tag_names_by_asset([r["id"] for r in rows])
        for row in rows:
            asset = dict(row)
            asset.pop("embedding", None)
            asset["tags"] = tags.get(asset["id"], [])
            yield asset
        offset += len(rows)
        if len(rows) < batch_size:
            return


def search_all(*, q: str = "", media_type: Optional[str] = None, tag: Optional[str] = None,
               include_tags=None, exclude_tags=None, collection_id: Optional[int] = None,
               status: str = "active", sort: str = "newest") -> list[dict]:
    """Materialise the full matching set — convenience wrapper over iter_rows."""
    return list(iter_rows(dict(
        q=q, media_type=media_type, tag=tag, include_tags=include_tags,
        exclude_tags=exclude_tags, collection_id=collection_id,
        status=status, sort=sort,
    )))


def _semantic_search(q, media_type, status, limit, offset) -> dict:
    conn = get_conn()
    qvec = ai.embed(q)
    where = ["status = ?", "embedding IS NOT NULL"]
    params: list = [status]
    if media_type:
        where.append("media_type = ?")
        params.append(media_type)
    rows = conn.execute(
        f"SELECT * FROM assets WHERE {' AND '.join(where)}", params
    ).fetchall()
    scored = []
    for r in rows:
        score = ai.cosine(qvec, ai.unpack_embedding(r["embedding"]))
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    limit, offset = _clip_page(limit, offset)
    page = scored[offset:offset + limit]
    items = []
    for score, r in page:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        items.append(card)
    return {"total": len(scored), "items": items, "facets": _facets(status), "mode": "semantic"}


def more_like(asset_id: int, limit: int = 12) -> list[dict]:
    conn = get_conn()
    base = conn.execute("SELECT embedding FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not base or not base["embedding"]:
        return []
    bvec = ai.unpack_embedding(base["embedding"])
    rows = conn.execute(
        "SELECT * FROM assets WHERE id != ? AND status = 'active' AND embedding IS NOT NULL",
        (asset_id,),
    ).fetchall()
    scored = [(ai.cosine(bvec, ai.unpack_embedding(r["embedding"])), r) for r in rows]
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, r in scored[:limit]:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        out.append(card)
    return out


def _facets(status: str) -> dict:
    conn = get_conn()
    media = conn.execute(
        "SELECT media_type, COUNT(*) c FROM assets WHERE status = ? GROUP BY media_type ORDER BY c DESC",
        (status,),
    ).fetchall()
    tags = conn.execute(
        """SELECT t.name, COUNT(*) c FROM tags t
           JOIN asset_tags at ON at.tag_id = t.id
           JOIN assets a ON a.id = at.asset_id AND a.status = ?
           GROUP BY t.name ORDER BY c DESC LIMIT 25""",
        (status,),
    ).fetchall()
    return {
        "media_types": [{"value": r["media_type"], "count": r["c"]} for r in media],
        "tags": [{"value": r["name"], "count": r["c"]} for r in tags],
    }


def _card(a: dict) -> dict:
    """Compact asset representation for grid/list views."""
    return {
        "id": a["id"],
        "uuid": a["uuid"],
        "title": a["title"],
        "filename": a["filename"],
        "media_type": a["media_type"],
        "mime_type": a["mime_type"],
        "ext": a["ext"],
        "size": a["size"],
        "width": a["width"],
        "height": a["height"],
        "alt_text": a["alt_text"],
        "status": a["status"],
        "created_at": a["created_at"],
        "tags": a.get("tags", []),
    }

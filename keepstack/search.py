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

from typing import Optional

from . import ai
from .db import get_conn

MAX_LIMIT = 200
DEFAULT_LIMIT = 60

# Every order ends with a unique tie-breaker (a.id). Without it, assets sharing
# a created_at can swap places between two queries and be skipped or repeated
# when paging. ISO-8601 timestamps sort lexicographically in time order.
SORTS = {
    "newest": "a.created_at DESC, a.id DESC",
    "oldest": "a.created_at ASC, a.id ASC",
    "name": "a.title ASC, a.id ASC",
    "largest": "a.size DESC, a.id DESC",
    # Optional: relevance ranking for keyword queries (not used by the UI).
    "relevance": "bm25(asset_fts) ASC, a.id ASC",
}


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query."""
    terms = [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in q).split() if t]
    return " ".join(f'"{t}"*' for t in terms)


def _norm_tags(values) -> list[str]:
    """Lowercase (matching how tags are stored), de-duplicate, drop blanks.

    Accepts a string, a list of strings, or comma-separated strings so both
    repeated query parameters and a single delimited value work.
    """
    out: list[str] = []
    if not values:
        return out
    if isinstance(values, str):
        values = [values]
    for value in values:
        if value is None:
            continue
        for part in str(value).split(","):
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

    if q and q.strip():
        join += " JOIN asset_fts ON asset_fts.rowid = a.id"
        where.append("asset_fts MATCH ?")
        params.append(_fts_query(q))

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
        params.append(cid)

    order = SORTS.get(sort, SORTS["newest"])
    # Relevance only has meaning when an FTS match is part of the query.
    if order.startswith("bm25") and not (q and q.strip()):
        order = SORTS["newest"]
    return join, " AND ".join(where), params, order


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

    rows = conn.execute(
        f"""SELECT a.* FROM assets a{join} WHERE {where_sql}
            ORDER BY {order} LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    return {
        "total": total,
        "items": [_card(dict(r)) for r in rows],
        "facets": _facets(status),
        "mode": mode,
        "limit": limit,
        "offset": offset,
        "sort": sort,
    }


def search_all(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    include_tags=None,
    exclude_tags=None,
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: str = "newest",
) -> list[dict]:
    """The full matching set (no pagination) for CSV export.

    Uses exactly the same filter/order builder as :func:`search`; only the
    LIMIT/OFFSET window is dropped. Tags are attached from the live
    asset_tags table so an edit made through the UI is reflected immediately.
    """
    conn = get_conn()
    join, where_sql, params, order = _query_parts(
        q=q, media_type=media_type, tag=tag, include_tags=include_tags,
        exclude_tags=exclude_tags, collection_id=collection_id,
        status=status, sort=sort,
    )
    rows = conn.execute(
        f"SELECT a.* FROM assets a{join} WHERE {where_sql} ORDER BY {order}", params
    ).fetchall()

    out = []
    for row in rows:
        asset = dict(row)
        asset.pop("embedding", None)
        asset["tags"] = [t["name"] for t in conn.execute(
            "SELECT t.name FROM tags t JOIN asset_tags at ON at.tag_id = t.id "
            "WHERE at.asset_id = ? ORDER BY t.name", (asset["id"],))]
        out.append(asset)
    return out


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
    }

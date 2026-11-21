"""CSV serialization of search results.

The writer intentionally uses Python's ``csv`` module rather than hand-built
strings: it quotes fields containing commas, double quotes or embedded
newlines, which is required for free-text titles and descriptions. UTF-8 is
written *without* a BOM so that programmatic readers see a clean ``uuid``
header; the response sets ``charset=utf-8`` accordingly.
"""
from __future__ import annotations

import csv
import io

# Stable column order; new columns can be appended without breaking readers.
FIELDS = ["uuid", "title", "description", "filename", "media_type", "tags", "created_at"]

# Multiple tags are joined into one cell with this separator (documented in
# SUBMISSION.md). Tags are already lower-cased and de-duplicated at ingest.
TAG_SEPARATOR = "|"


def rows_to_csv(rows: list[dict]) -> str:
    """Return a CSV document (header always present, even for zero rows)."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(FIELDS)
    for asset in rows:
        writer.writerow([
            asset.get("uuid") or "",
            asset.get("title") or "",
            asset.get("description") or "",
            asset.get("filename") or "",
            asset.get("media_type") or "",
            TAG_SEPARATOR.join(asset.get("tags") or []),
            asset.get("created_at") or "",
        ])
    return buf.getvalue()

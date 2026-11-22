"""CSV serialization of search results.

The writer uses Python's ``csv`` module rather than hand-built strings: it
quotes fields containing commas, double quotes or embedded newlines, which is
required for free-text titles and descriptions. A UTF-8 BOM is prepended so
the file opens with correct Chinese characters in spreadsheet applications
(the content-operations audience). Cells beginning with a spreadsheet formula
trigger are neutralised with a leading apostrophe so untrusted metadata cannot
turn into an executed formula on open.
"""
from __future__ import annotations

import csv
import io
from typing import Iterable, Iterator

FIELDS = ("uuid", "title", "description", "filename", "media_type", "tags", "created_at")
TAG_SEPARATOR = "|"
FORMULA_PREFIXES = ("=", "+", "-", "@")
_BOM = "\ufeff"


def neutralise_formula(value) -> str:
    """Prefix a spreadsheet formula trigger with an apostrophe.

    The raw text stays readable after the marker; the trade-off is that such
    cells no longer auto-evaluate in a spreadsheet.
    """
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(FORMULA_PREFIXES) else text


def _row_values(row: dict) -> list[str]:
    tags = row.get("tags") or []
    cell = tags if isinstance(tags, str) else TAG_SEPARATOR.join(sorted(tags))
    values = [
        row.get("uuid") or "",
        row.get("title") or "",
        row.get("description") or "",
        row.get("filename") or "",
        row.get("media_type") or "",
        cell,
        row.get("created_at") or "",
    ]
    return [neutralise_formula(v) for v in values]


def _csv_line(values: Iterable[str]) -> str:
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\r\n").writerow(list(values))
    return buf.getvalue()


def iter_csv(rows: Iterable[dict]) -> Iterator[str]:
    """Yield BOM, header, then one correctly quoted line per row.

    Generator form keeps memory bounded when the caller streams the response.
    """
    yield _BOM
    yield _csv_line(FIELDS)
    for row in rows:
        yield _csv_line(_row_values(row))


def rows_to_csv(rows: Iterable[dict]) -> str:
    """Convenience wrapper for callers/tests that already hold all rows."""
    return "".join(iter_csv(rows))

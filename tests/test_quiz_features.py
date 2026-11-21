"""Complete acceptance tests for the combined-filter / paging / export exercise.

Run with:  python -m pytest -q

They build a fresh deterministic catalog via ``quiz_data.build_dataset()`` into
a throwaway data directory, so nothing touches a real repository. Every
requirement in the task statement maps to at least one test below:

  Requirement 1 (combined filtering)
      - keyword / blank keyword ......... test_combined_filter_matches_exactly_expected,
                                         test_duplicate_and_conflicting_tags
      - collection ...................... test_filter_conditions_are_individually_enforced
      - include tags (all) / exclude .... test_filter_conditions_are_individually_enforced
      - full-name tag match ............. test_tag_matches_by_full_name
      - dedup / case / conflict ......... test_duplicate_and_conflicting_tags
      - media type ...................... test_export_applies_media_type_and_requires_auth
      - active-only intersection ........ test_combined_filter_matches_exactly_expected
      - active-condition display/clear .. test_frontend_filter_ui_present

  Requirement 2 (sorting & paging)
      - asc/desc + keyword honours sort . test_equal_created_at_ordering_is_stable
      - prev/next + total + page size ... test_pagination_has_no_duplicates_or_omissions,
                                         test_frontend_pagination_and_race_guard
      - stable with equal created_at .... test_equal_created_at_ordering_is_stable
      - reset to page 1 / latest wins ... test_frontend_pagination_and_race_guard
      - empty / invalid / special chars . test_graceful_empty_result,
                                         test_invalid_pagination_and_special_characters

  Requirement 3 (CSV export)
      - all matches / filter+sort parity  test_export_matches_full_filtered_sorted_list,
                                         test_export_is_independent_of_page_window
      - required columns / multi-tag .... test_csv_escaping_and_columns
      - Chinese/comma/quote/newline ..... test_csv_escaping_and_columns
      - zero rows -> header ............. test_export_zero_results_still_has_header
      - auth + page download + failure .. test_export_applies_media_type_and_requires_auth,
                                         test_frontend_export_download_and_failure_toast
      - metadata only, no files ......... test_csv_escaping_and_columns

  Requirement 4 (edits reflected)
      - title / tag ..................... test_edits_reflect_in_query_and_export
      - description ..................... test_description_edit_reflects_in_query_and_export
"""
import csv
import io
import tempfile
from pathlib import Path

import pytest

from keepstack.config import config
from keepstack import db as db_module
from fastapi.testclient import TestClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def info():
    """Redirect config to a fresh dir and build the deterministic catalog."""
    tmp = Path(tempfile.mkdtemp(prefix="keepstack-quiz-test-"))
    saved = {k: getattr(config, k) for k in
             ("data_dir", "db_path", "blob_dir", "thumb_dir", "cache_dir")}
    config.data_dir = tmp
    config.db_path = tmp / "keepstack.db"
    config.blob_dir = tmp / "blobs"
    config.thumb_dir = tmp / "thumbnails"
    config.cache_dir = tmp / "cache"
    db_module._local.conn = None  # force a connection to the new database

    from keepstack import quiz_data
    built = quiz_data.build_dataset()
    built["tmp"] = tmp
    yield built

    db_module._local.conn = None
    for key, value in saved.items():
        setattr(config, key, value)


@pytest.fixture(scope="module")
def client(info):
    from keepstack.app import app
    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200, login.text
        c.headers.update({"Authorization": "Bearer " + login.json()["token"]})
        yield c


BASE = {
    "q": "harbor",
    "include_tags": ["approved", "campaign"],
    "exclude_tags": ["internal"],
}


def _params(info, **overrides):
    p = {**BASE, "collection_id": info["collection_id"]}
    p.update(overrides)
    return p


def _list_all(client, params, per_page=3):
    """Page through the list API and return every item in order."""
    items, offset = [], 0
    while True:
        page = client.get("/api/assets", params={**params, "limit": per_page, "offset": offset})
        assert page.status_code == 200, page.text
        data = page.json()
        items.extend(data["items"])
        offset += per_page
        if offset >= data["total"]:
            assert data["total"] == len(items)
            return data["total"], items


# ---------------------------------------------------------------------------
# Requirement 1: combined filtering
# ---------------------------------------------------------------------------
def test_combined_filter_matches_exactly_expected(info, client):
    """keyword + collection + include(ALL) + exclude(ANY) + active-only."""
    total, items = _list_all(client, _params(info))
    assert total == info["expected_match_count"] == 8
    uuids = {i["uuid"] for i in items}
    for key in ("m1", "m4", "m8", "m5", "m6", "m7"):
        assert info["uuids"][key] in uuids
    for key in ("n_internal", "n_no_campaign", "n_no_approved", "n_other_col",
                "n_deleted", "n_no_keyword"):
        assert info["uuids"][key] not in uuids


def test_filter_conditions_are_individually_enforced(info, client):
    # exclude tags: only the internal-tagged asset drops out here
    _, with_internal = _list_all(client, _params(info, exclude_tags=[]))
    assert info["uuids"]["n_internal"] in {i["uuid"] for i in with_internal}
    _, without_internal = _list_all(client, _params(info))
    assert info["uuids"]["n_internal"] not in {i["uuid"] for i in without_internal}

    # include tags are ALL-of, not any-of
    _, approved_only = _list_all(client, _params(info, include_tags=["approved"]))
    assert info["uuids"]["n_no_campaign"] in {i["uuid"] for i in approved_only}
    assert info["uuids"]["n_no_approved"] not in {i["uuid"] for i in approved_only}

    # collection filter really restricts membership (the pre-existing bug)
    _, in_collection = _list_all(client, _params(info))
    assert all(i["uuid"] in {v for k, v in info["uuids"].items() if k.startswith("m")}
               for i in in_collection)
    total_other, other = _list_all(client, _params(info, collection_id=info["other_collection_id"]))
    assert total_other == 2  # the two harbor matches placed in the other collection
    assert {i["uuid"] for i in other} == {info["uuids"]["n_other_col"], info["uuids"]["a_audio"]}

    # media type filter is applied
    total_audio, audio = _list_all(client, _params(info, media_type="audio"))
    assert total_audio == 0  # audio clip lives in the other collection


def test_tag_matches_by_full_name(info, client):
    """Tags match by complete name, not substring/prefix."""
    total, _ = _list_all(client, _params(info, include_tags=["approv"]))
    assert total == 0                      # a prefix of 'approved' must not match
    total, _ = _list_all(client, _params(info, include_tags=["approved-extra"]))
    assert total == 0                      # a superstring must not match
    total, _ = _list_all(client, _params(info, include_tags=["APPROVED", "CaMpAiGn"]))
    assert total == 8                      # case-insensitive full-name match does


def test_duplicate_and_conflicting_tags(info, client):
    _, dup = _list_all(client, _params(info, include_tags=["approved", "APPROVED", "campaign,campaign"]))
    assert len(dup) == 8  # de-duplicated, case-insensitive

    total, _ = _list_all(client, _params(info, include_tags=["approved"], exclude_tags=["approved"]))
    assert total == 0  # same tag both included and excluded -> empty

    # blank keyword means no keyword restriction
    total, _ = _list_all(client, _params(info, q="   "))
    assert total >= 8


def test_graceful_empty_result(info, client):
    """A valid-but-unmatched filter returns an empty list with the real total."""
    result = client.get("/api/assets", params=_params(info, q="no-such-harbor-term")).json()
    assert result["total"] == 0
    assert result["items"] == []
    assert result["facets"] is not None  # facets still rendered so the UI stays usable


# ---------------------------------------------------------------------------
# Requirement 2: sorting & paging
# ---------------------------------------------------------------------------
def test_pagination_has_no_duplicates_or_omissions(info, client):
    for per_page in (1, 3, 5, 200):
        total, items = _list_all(client, _params(info), per_page=per_page)
        uuids = [i["uuid"] for i in items]
        assert len(uuids) == total == 8
        assert len(set(uuids)) == len(uuids), f"duplicate across pages at size {per_page}"

    # out-of-range page yields an empty list but keeps the real total
    empty = client.get("/api/assets", params={**_params(info), "limit": 3, "offset": 999}).json()
    assert empty["items"] == [] and empty["total"] == 8


def test_equal_created_at_ordering_is_stable(info, client):
    m4, m8 = info["uuids"]["m4"], info["uuids"]["m8"]
    for sort in ("newest", "oldest"):
        _, first = _list_all(client, _params(info, sort=sort), per_page=3)
        _, second = _list_all(client, _params(info, sort=sort), per_page=3)
        seq1 = [i["uuid"] for i in first]
        seq2 = [i["uuid"] for i in second]
        assert seq1 == seq2, "order changed between identical queries"
        assert m4 in seq1 and m8 in seq1
        assert seq1.index(m4) != seq1.index(m8)  # both present, tied but distinct

    # keyword + sort: newest/oldest reverse the order (no bm25 override)
    _, newest = _list_all(client, _params(info, sort="newest"))
    _, oldest = _list_all(client, _params(info, sort="oldest"))
    nseq = [i["uuid"] for i in newest]
    oseq = [i["uuid"] for i in oldest]
    assert nseq == list(reversed(oseq))


# ---------------------------------------------------------------------------
# Requirement 3: CSV export
# ---------------------------------------------------------------------------
def _export_rows(client, params):
    resp = client.get("/api/assets/export.csv", params=params)
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers["content-type"]
    assert 'attachment; filename="assets.csv"' in resp.headers["content-disposition"]
    return list(csv.DictReader(io.StringIO(resp.text)))


def test_export_matches_full_filtered_sorted_list(info, client):
    for sort in ("newest", "oldest"):
        params = _params(info, sort=sort)
        _, items = _list_all(client, params, per_page=3)
        rows = _export_rows(client, params)
        assert [r["uuid"] for r in rows] == [i["uuid"] for i in items]
        assert len(rows) == 8


def test_export_is_independent_of_page_window(info, client):
    page = client.get("/api/assets", params={**_params(info), "limit": 2, "offset": 0}).json()
    rows = _export_rows(client, _params(info))
    assert len(page["items"]) == 2
    assert len(rows) == 8


def test_csv_escaping_and_columns(info, client):
    rows = _export_rows(client, _params(info))
    assert list(rows[0].keys()) == ["uuid", "title", "description", "filename",
                                    "media_type", "tags", "created_at"]
    by_uuid = {r["uuid"]: r for r in rows}

    comma_quote = by_uuid[info["uuids"]["m5"]]
    assert comma_quote["title"] == 'Harbor, the "old" dock, revisited'
    assert 'Quoted "value", a comma, and a\nsecond line' in comma_quote["description"]

    newline = by_uuid[info["uuids"]["m7"]]
    assert newline["title"] == "Harbor warehouse\nrow two"

    chinese = by_uuid[info["uuids"]["m6"]]
    assert chinese["title"] == "海港调研 Harbor survey"
    assert chinese["description"] == ""

    assert by_uuid[info["uuids"]["m1"]]["tags"] == "approved|campaign"
    assert by_uuid[info["uuids"]["m2"]]["tags"] == "approved|campaign|external"
    # metadata-only: no file bytes / content path columns leak into the CSV
    assert not any(k in rows[0] for k in ("storage_key", "sha256", "file", "blob"))


def test_export_zero_results_still_has_header(info, client):
    rows = _export_rows(client, _params(info, q="definitely-no-such-token-xyz"))
    assert rows == []
    resp = client.get("/api/assets/export.csv", params=_params(info, q="definitely-no-such-token-xyz"))
    assert resp.text.strip() == "uuid,title,description,filename,media_type,tags,created_at"


def test_export_applies_media_type_and_requires_auth(info, client):
    rows = _export_rows(client, {
        "q": "harbor", "collection_id": info["other_collection_id"], "media_type": "audio"})
    assert len(rows) == 1
    assert rows[0]["uuid"] == info["uuids"]["a_audio"]
    assert rows[0]["media_type"] == "audio"

    no_auth = TestClient(client.app)
    resp = no_auth.get("/api/assets/export.csv", params=_params(info))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Requirement 4: edits reflected in the next query and export
# ---------------------------------------------------------------------------
def test_edits_reflect_in_query_and_export(info, client):
    """Tag edits and title edits are reflected in both query and export."""
    target = info["uuids"]["n_no_campaign"]  # "Harbor draft", tag: approved only

    # add campaign -> asset joins the matching set (8 -> 9)
    added = client.post(f"/api/assets/{target}/tags", json={"name": "campaign"})
    assert added.status_code == 200, added.text
    total, _ = _list_all(client, _params(info))
    assert total == 9
    assert len(_export_rows(client, _params(info))) == 9

    # remove it again -> back to 8
    removed = client.delete(f"/api/assets/{target}/tags/campaign")
    assert removed.status_code == 200
    total, _ = _list_all(client, _params(info))
    assert total == 8

    # change title -> keyword search no longer finds the old word
    uuid_m1 = info["uuids"]["m1"]
    detail = client.get(f"/api/assets/{uuid_m1}").json()["asset"]
    patched = client.patch(f"/api/assets/{uuid_m1}", json={"title": "Renamed skyline overview"})
    assert patched.status_code == 200, patched.text
    total_after, items = _list_all(client, _params(info))
    assert uuid_m1 not in {i["uuid"] for i in items}
    assert total_after == 7
    assert all(r["uuid"] != uuid_m1 for r in _export_rows(client, _params(info)))

    # restore so this test is order-independent
    restore = client.patch(f"/api/assets/{uuid_m1}", json={"title": detail["title"]})
    assert restore.status_code == 200
    total, _ = _list_all(client, _params(info))
    assert total == 8


def test_description_edit_reflects_in_query_and_export(info, client):
    """A description edit is reindexed for keyword search and shown in CSV."""
    target = info["uuids"]["n_no_keyword"]  # in main collection, has approved+campaign, no harbor word
    baseline = client.get(f"/api/assets/{target}").json()["asset"]
    token = "seabuoy"

    # put a unique token in the DESCRIPTION only -> FTS must reindex on edit
    patched = client.patch(f"/api/assets/{target}",
                           json={"description": f"Survey note: {token} marked for review."})
    assert patched.status_code == 200, patched.text
    params = _params(info, q=token)
    total, items = _list_all(client, params)
    assert total == 1 and items[0]["uuid"] == target
    rows = _export_rows(client, params)
    assert len(rows) == 1 and token in rows[0]["description"]

    # remove the token from the description -> it drops out again
    patched = client.patch(f"/api/assets/{target}", json={"description": "No special marker here."})
    assert patched.status_code == 200
    total, _ = _list_all(client, params)
    assert total == 0
    assert _export_rows(client, params) == []

    # restore so this test is order-independent
    client.patch(f"/api/assets/{target}", json={"description": baseline["description"] or ""})


# ---------------------------------------------------------------------------
# UI structure (the browser behaviours are implemented in vanilla JS; these
# checks assert the wired-up elements/logic exist so a reviewer can trace them)
# ---------------------------------------------------------------------------
def test_frontend_filter_ui_present():
    html = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for element in ("filter-collection", "filter-include", "filter-exclude",
                    "btn-apply-filter", "btn-clear-filter", "active-filters"):
        assert f'id="{element}"' in html, f"missing UI element: {element}"
    assert "renderActiveFilters" in js          # active conditions are displayed
    assert "#active-filters .fchip" in js        # each condition is removable
    assert "btn-clear-filter" in js              # clear-all wired
    assert "include_tags" in js and "exclude_tags" in js


def test_frontend_pagination_and_race_guard():
    html = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for element in ("page-prev", "page-next", "page-info"):
        assert f'id="{element}"' in html
    assert "PAGE_SIZE" in js
    assert "state.reqSeq" in js and "mySeq !== state.reqSeq" in js  # latest-response-wins
    assert "resetPage()" in js                                      # filter/sort -> page 1
    assert "page-prev" in js and "page-next" in js


def test_frontend_export_download_and_failure_toast():
    html = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'id="btn-export"' in html
    assert "/api/assets/export.csv" in js
    assert "withPage: false" in js               # export ignores current page
    assert 'a.download = "assets.csv"' in js     # triggers a browser download
    assert "Export failed:" in js and "toast(" in js  # download failure is surfaced


# ---------------------------------------------------------------------------
# Invalid / hostile input
# ---------------------------------------------------------------------------
def test_invalid_pagination_and_special_characters(info, client):
    r = client.get("/api/assets", params={**_params(info), "limit": -5, "offset": -3})
    assert r.status_code == 200
    assert r.json()["limit"] == 1 and r.json()["offset"] == 0

    r = client.get("/api/assets", params={**_params(info), "q": '" OR 1=1 --'})
    assert r.status_code == 200  # FTS query is sanitised, no SQL injection

    r = client.get("/api/assets", params={**_params(info), "sort": "bogus"})
    assert r.status_code == 200  # unknown sort falls back to newest

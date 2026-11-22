/* Pure helpers for the combined-filter page: no DOM, no network, easy to test.
 * Exposed as CommonJS (for Node tests) or window.KeepstackFilters (browser). */
(function (root) {
  "use strict";

  function splitTags(raw) {
    return (String(raw || ""))
      .split(/[,，\s]+/)
      .map(function (s) { return s.trim().toLowerCase(); })
      .filter(Boolean)
      .filter(function (v, i, a) { return a.indexOf(v) === i; });
  }

  const SORTS = ["newest", "oldest", "name", "largest"];

  function queryParams(state, withPage) {
    withPage = withPage !== false;
    const p = new URLSearchParams();
    if (String(state.q || "").trim()) p.set("q", String(state.q).trim());
    if (state.media) p.set("media_type", state.media);
    if (state.collection) p.set("collection_id", String(state.collection));
    (state.include || []).forEach(function (t) { p.append("include_tags", t); });
    (state.exclude || []).forEach(function (t) { p.append("exclude_tags", t); });
    p.set("sort", SORTS.indexOf(state.sort) >= 0 ? state.sort : "newest");
    if (withPage) {
      p.set("limit", String(state.limit || 12));
      p.set("offset", String(state.offset || 0));
    }
    return p;
  }

  function fromLocationSearch(search) {
    const p = new URLSearchParams(search || "");
    const limit = Number(p.get("limit"));
    const offset = Number(p.get("offset"));
    const sort = p.get("sort");
    return {
      q: p.get("q") || "",
      media: p.get("media_type") || null,
      collection: p.get("collection_id") || "",
      include: splitTags(p.getAll("include_tags").join(",")),
      exclude: splitTags(p.getAll("exclude_tags").join(",")),
      sort: SORTS.indexOf(sort) >= 0 ? sort : "newest",
      limit: Number.isFinite(limit) && limit >= 1 && limit <= 200 ? limit : 12,
      offset: Number.isFinite(offset) && offset >= 0 ? offset : 0,
    };
  }

  const api = { splitTags: splitTags, queryParams: queryParams,
                fromLocationSearch: fromLocationSearch, SORTS: SORTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.KeepstackFilters = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

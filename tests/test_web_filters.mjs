// Node tests for the DOM-free filter helpers in web/filters.js.
// Run: node --test tests/test_web_filters.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const F = require(path.join(dir, "..", "web", "filters.js"));

test("splitTags: lowercase, de-dup, ASCII + Chinese comma + whitespace", () => {
  assert.deepEqual(F.splitTags("Approved, campaign, approved，internal"),
                   ["approved", "campaign", "internal"]);
  assert.deepEqual(F.splitTags(""), []);
});

test("queryParams: includes filters, sort; page optional; export drops page", () => {
  const state = { q: " harbor ", media: "image", collection: "3",
                  include: ["approved"], exclude: ["internal"],
                  sort: "oldest", limit: 12, offset: 24 };
  const paged = F.queryParams(state);
  assert.equal(paged.get("q"), "harbor");
  assert.equal(paged.get("collection_id"), "3");
  assert.equal(paged.get("offset"), "24");
  assert.deepEqual(paged.getAll("include_tags"), ["approved"]);
  const exportParams = F.queryParams(state, false);
  assert.equal(exportParams.has("limit"), false);
  assert.equal(exportParams.has("offset"), false);
  assert.equal(exportParams.get("sort"), "oldest");
});

test("fromLocationSearch: round-trips and ignores junk", () => {
  const state = F.fromLocationSearch("?q=harbor&include_tags=approved&include_tags=campaign"
    + "&exclude_tags=internal&sort=oldest&limit=3&offset=6&collection_id=2");
  assert.equal(state.q, "harbor");
  assert.deepEqual(state.include, ["approved", "campaign"]);
  assert.deepEqual(state.exclude, ["internal"]);
  assert.equal(state.sort, "oldest");
  assert.equal(state.limit, 3);
  assert.equal(state.offset, 6);
  const junk = F.fromLocationSearch("?sort=evil&limit=-5&offset=x");
  assert.equal(junk.sort, "newest");
  assert.equal(junk.limit, 12);
  assert.equal(junk.offset, 0);
});

test("queryParams(fromLocationSearch(x)) reproduces filters", () => {
  const src = "?q=harbor&include_tags=approved&exclude_tags=internal&sort=oldest";
  const state = F.fromLocationSearch(src);
  const out = F.queryParams(state, false);
  assert.equal(out.get("q"), "harbor");
  assert.deepEqual(out.getAll("include_tags"), ["approved"]);
  assert.deepEqual(out.getAll("exclude_tags"), ["internal"]);
});

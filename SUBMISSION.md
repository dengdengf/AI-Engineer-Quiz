# SUBMISSION — AI 应用开发工程师测试题（素材检索与结果导出）

## 1. 交付信息

| 项目 | 值 |
|------|----|
| Fork 仓库 | https://github.com/dengdengf/AI-Engineer-Quiz |
| 开发分支 | `feature/combined-filter-pagination-export` |
| 基线 commit（收到题目时的上游版本） | `3aa137c` |
| 最终 commit SHA | 为包含本文件的提交，完整值见交付回复（评审可在本分支 HEAD 直接查看） |
| 上游 | `upstream` → https://github.com/Qiyin-Tech/AI-Engineer-Quiz |

提交历史按实际开发顺序保留，未使用 squash / amend / rebase 改写：

1. `b8c0f43` feat(search): combined filters, stable paging and full CSV export
2. `6ea8954` fix(search): correct collection filter param binding; add reproducible quiz dataset
3. `aba932c` test: cover combined filters, stable paging, export parity and edits
4. `7c438cf` feat(web): combined filter panel, stable pagination and CSV download
5. `docs: add SUBMISSION.md`（本文件，即最终 commit）

无需向上游 Keepstack 提 PR。

## 2. 运行与验证步骤

要求 Python 3.10+（本地用 3.12）。在仓库根目录：

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'

export KEEPSTACK_AI_ENABLED=false
export KEEPSTACK_OCR_ENABLED=false

# 上游演示数据（24 素材 / 3 集合）——可选，用于界面浏览
uv run --no-sync python -m keepstack seed

# 本题专用可复现数据集（harbor + approved/campaign/internal）
uv run --no-sync python -m keepstack seed-quiz

# 启动
uv run --no-sync python -m keepstack
```

打开 http://localhost:8000，账号 `admin` / `admin`。

**手工验收业务场景**：在搜索框输入 `harbor`，集合选 `Harbor Campaign`，包含标签填 `approved, campaign`，排除标签填 `internal`，点 **Apply**，排序选 **Newest first**。应看到 8 条结果、分页控件显示 `1–8 of 8`（每页 12 条时）。点 **Export CSV** 下载全部 8 条（不受分页限制）。在素材抽屉里改标题/标签后再次查询与导出，结果随之变化。

**自动化测试**：

```bash
uv run --no-sync python -m pytest -q
```

测试写入临时数据目录，不污染真实数据。

## 3. 代码、测试与数据生成方式

### 代码改动

| 文件 | 作用 |
|------|------|
| `keepstack/search.py` | 组合筛选（`_query_parts`）、稳定排序/分页（`SORTS`、`_clip_page`）、全量结果查询（`search_all`）；修复集合筛选参数错位缺陷；关键词搜索不再被 bm25 覆盖排序 |
| `keepstack/csv_export.py` | CSV 序列化：固定列顺序、`csv.writer` 标准转义（逗号/引号/换行/中文）、多标签用 `\|` 连接 |
| `keepstack/app.py` | 列表接口新增 `include_tags` / `exclude_tags`；新增鉴权的 `GET /api/assets/export.csv`；`_filter_kwargs` 让列表与导出共用同一套筛选映射 |
| `keepstack/quiz_data.py` | 本题可复现数据集生成器 |
| `keepstack/__main__.py` | 新增 `seed-quiz` 命令 |
| `web/index.html`, `web/app.js`, `web/styles.css` | 筛选面板、生效条件 chips、分页、导出按钮、竞态保护 |

### 数据生成方式

默认 `seed` 不含 `harbor` 关键词与 `approved` / `campaign` / `internal` 标签，无法复现业务场景。新增 `python -m keepstack seed-quiz`，用 Pillow 在内存中生成各不相同的图片（避免内容寻址去重），按固定清单写入标题、标签、集合、`created_at`，产出 15 个素材：

- 8 个命中项：`m1..m8`（`m4` 与 `m8` 创建时间相同，用于稳定分页测试；`m5` 标题含逗号和引号、描述含中文/引号/逗号/换行；`m6` 中文标题；`m7` 标题含换行）；
- 7 个反例：带 `internal`、缺 `campaign`、缺 `approved`、在其他集合、已删除、无关键词、音频类型；
- 期望命中数固定为 8，测试通过 `build_dataset()` 返回的 `info` 断言，不硬编码数据库 id。

### 自动化测试覆盖（`tests/test_quiz_features.py`，12 项）

组合筛选精确集、各条件单独生效、重复/冲突标签、空白关键词；每页 1/3/5/200 条无重复无遗漏与越界页；相同创建时间下排序稳定、关键词+排序遵守所选排序；导出与完整筛选排序列表逐条一致、不受分页窗口影响、CSV 列/转义/中文/零结果表头、媒体类型筛选、鉴权 401；经现有 API 改标签/标题后查询与导出同步；非法分页参数与注入样式输入。

## 4. 实测结果

在干净目录 `/tmp/ks-final` 完整复现：

- `python -m keepstack seed` → `Seeded 24 assets, 3 collections`
- `python -m keepstack seed-quiz` → `Quiz dataset ready: 8 expected matches`
- `python -m pytest -q` → **34 passed, 2 skipped**（2 项跳过为可选本地视觉模型 / tesseract，环境未安装）
- 真实服务（uvicorn，`127.0.0.1:8123`）冒烟：`/healthz` → `ok`；`POST /api/auth/login` → 200；带 token 的 `GET /api/assets/export.csv`（harbor + 集合 + 包含/排除标签 + oldest）→ **HTTP 200**，`content-type: text/csv; charset=utf-8`，CSV 首行为 `uuid,title,description,filename,media_type,tags,created_at`，首条数据为 `Harbor sunrise overview ... approved|campaign ... 2026-01-01T09:00:00Z`（按 oldest 升序排列，符合预期）。

## 5. 主要取舍

- **排序 vs 相关性**：题目要求「有关键词时也遵守所选排序」，故关键词模式统一用 `created_at`/`name`/`size` 排序；bm25 相关性排序保留为可选 `sort=relevance`，但 UI 不暴露。好处是分页稳定、结果可预期；代价是关键词结果不再按相关度排列。
- **稳定分页**：所有排序追加唯一 tie-breaker `a.id`，确保 `created_at` 相同也不跨页重复/遗漏。没有引入跨请求快照（题目说明不要求）。
- **导出一致性**：抽出 `_query_parts` 与 `_filter_kwargs`，列表与导出共用；导出直接查库而非复用分页结果，天然不受当前页限制。多标签用 `|` 连接（示例 `approved|campaign`），比逗号不易与 CSV 分隔冲突。
- **CSV 编码**：UTF-8 无 BOM，并用标准 `csv.writer` 转义，便于程序解析中文、逗号、引号、换行；字段内换行会被正确加引号（物理行数会多于记录数，属正常 CSV）。
- **前端竞态**：用递增请求序号，只渲染最新响应；筛选/排序变更即回到第一页。语义搜索入口按题目允许予以隐藏。
- **新增生成器而非改原 seed**：不破坏上游演示数据，`seed` 与 `seed-quiz` 可共存。

## 6. 限制与未完成项

- 语义搜索模式（后端保留）不支持本题新增的组合筛选/稳定分页，UI 已隐藏；题目只验收关键词搜索，故未改造。
- 筛选选项（侧栏 media/tag 统计数）未做联动，仅展示全库统计，不会误标为当前匹配数（题目明确允许）。
- 导出为一次性生成全部匹配行，未做异步导出/流式分块（题目不要求）；本项目规模下内存开销可忽略。
- 未实现跨请求快照一致：并发写入时两次翻页之间若数据变更，仍可能出现边界变动（题目说明不要求跨请求快照）。
- 鉴权沿用现有 Bearer token 体系，未新增权限模型。
- 上游既有的 `on_event` 弃用告警等非本题范围问题仅记录，未改动。

## 7. 最终 commit SHA

最终 commit 为包含本文件的提交（本分支 HEAD）。完整 SHA 在交付回复中给出；评审也可在 Fork 仓库本分支的提交历史中直接查看。

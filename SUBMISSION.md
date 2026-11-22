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
5. `30c0da2` test: complete acceptance coverage for every requirement; verify all pass
6. `356a8b0` harden filters/export: punctuation, validation, BOM, formula, tz sort
7. `feat(web): URL-persisted filters, card tags and testable filter helpers`（本文件之前的提交）
8. `docs: refresh SUBMISSION.md`（本文件，即最终 commit）

无需向上游 Keepstack 提 PR。

## 2. 运行与验证步骤

要求 Python 3.10+（本地用 3.12）；前端纯函数测试需要 Node（缺失时自动跳过）。在仓库根目录：

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

**手工验收业务场景**：搜索框输入 `harbor`，集合选 `Harbor Campaign`，包含标签填 `approved, campaign`，排除标签填 `internal`，点 **Apply**，排序选 **Oldest first**。应看到 8 条结果；卡片显示标签便于核对；分页控件显示 `1–8 of 8`（每页 12 条时，或用 URL 参数 `limit=3` 看多页）。筛选条件同步到 URL，刷新/分享链接可还原视图。点 **Export CSV** 下载全部 8 条（不受分页限制）。在素材抽屉改标题/描述/标签后再次查询与导出，结果随之变化。

**自动化测试**：

```bash
uv run --no-sync python -m pytest -q
# 或单独跑 Node 纯函数测试：
node --test tests/test_web_filters.mjs
```

测试写入临时数据目录，不污染真实数据。

## 3. 代码、测试与数据生成方式

| 文件 | 作用 |
|------|------|
| `keepstack/search.py` | 组合筛选（`_query_parts`）、确定性排序（`julianday()` + id tie-breaker）、计数、分批取行（`iter_rows`）、标签批量查询；纯标点关键词视为不限；修复集合筛选参数错位与关键词强制 bm25 的上游缺陷 |
| `keepstack/csv_export.py` | CSV 序列化：UTF-8 BOM、`csv.writer` 标准转义、`\|` 连接多标签、`=+-@` 公式注入中和；生成器逐行输出 |
| `keepstack/app.py` | 列表/导出共用 `_filter_kwargs`；非法 `status/sort/mode` 与 semantic+组合筛选返回 400；`/api/assets/export.csv` 全量流式导出并写 `asset.export` 审计（含筛选摘要） |
| `keepstack/db.py` | `julianday(created_at)`、`lower(tags.name)`、`asset_tags(tag_id, asset_id)` 表达式/组合索引 |
| `keepstack/quiz_data.py` | 本题可复现数据集生成器 + `seed-quiz` 命令 |
| `web/index.html`, `web/app.js`, `web/styles.css` | 筛选面板、生效条件 chip、分页、卡片标签、URL 同步、导出与错误态 |
| `web/filters.js` | 无 DOM 依赖的筛选纯函数（`splitTags`/`queryParams`/`fromLocationSearch`），可由 Node 直接测 |
| `tests/test_quiz_features.py` | 21 项验收测试，逐条映射题目要求 |
| `tests/test_web_filters.mjs`, `tests/test_web_helpers.py` | 前端纯函数 Node 测试与 pytest 包装 |

### 数据生成方式

默认 `seed` 不含 `harbor` 关键词与 `approved`/`campaign`/`internal` 标签，无法复现业务场景。新增 `python -m keepstack seed-quiz`，用 Pillow 在内存生成互不相同的图片（避免内容寻址去重），按固定清单写入标题、标签、集合与 `created_at`，产出 15 个素材：8 个命中项（`m4`/`m8` 同创建时间用于稳定分页；`m5` 标题含逗号引号、描述含中文/引号/逗号/换行；`m6` 中文标题；`m7` 标题含换行），7 个反例（internal、缺 campaign、缺 approved、他集合、已删除、无关键词、音频类型）。期望命中数固定为 8，测试通过 `build_dataset()` 返回的 `info` 断言，不硬编码数据库 id。

### 自动化测试覆盖（`tests/test_quiz_features.py`，21 项 + 4 项 Node）

- **组合筛选**：精确命中集、各条件单独生效、标签完整名称匹配（非前缀/子串）、重复/大小写/冲突标签、中文逗号分隔、空白/纯标点关键词；集合筛选参数错位缺陷回归。
- **排序与分页**：每页 1/3/5/200 条无重复无遗漏与越界页；相同 `created_at` 稳定、`julianday()` 时区归一化；关键词遵守所选排序；空结果与加载失败；非法 `limit/offset`、注入样式输入；非法 `sort/mode/status` 返回 400。
- **CSV 导出**：与完整筛选排序列表逐条一致、不受分页窗口影响、必需列齐全、`\|` 多标签、中文/逗号/引号/换行、UTF-8 BOM、公式注入中和、零结果表头、仅元数据、媒体类型筛选、鉴权 401。
- **修改后结果正确**：经现有 API 改标题、改描述、改标签后查询与导出同步（三种字段各有独立测试）。
- **前端**：筛选元素与条件 chip、分页与竞态保护、导出下载与失败提示、URL 同步、卡片标签等契约检查；筛选纯函数由 Node 单测覆盖拆分/构造/URL 还原与往返。

## 4. 实测结果

在干净目录完整复现：

- `python -m keepstack seed` → `Seeded 24 assets, 3 collections`
- `python -m keepstack seed-quiz` → `Quiz dataset ready: 8 expected matches`
- `python -m pytest -q` → **44 passed, 2 skipped**（Node 前端纯函数测试计入；2 项跳过为可选本地视觉模型 / tesseract 环境未安装）
- `node --test tests/test_web_filters.mjs` → **4 pass / 0 fail**
- 真实服务（uvicorn）冒烟：`/healthz` → `ok`；登录 → 200；带 token 的导出（harbor + 集合 + 包含/排除标签 + oldest）→ HTTP 200、`text/csv; charset=utf-8`、UTF-8 BOM、表头 `uuid,title,description,filename,media_type,tags,created_at`，首条按 oldest 升序。
- 纯标点关键词（如 `!!!`）不再 500，按无关键词处理；非法 `sort/mode/status` 返回 400；公式标题导出时加 `'` 前缀；未登录导出返回 401。

## 5. 主要取舍

- **排序 vs 相关性**：题目要求「有关键词时也遵守所选排序」，故关键词模式用 `created_at`/`name`/`size` 排序；bm25 相关性保留为可选 `sort=relevance`，UI 不暴露。用 `julianday()` 归一化 ISO 时区并追加唯一 `a.id` tie-breaker，保证同 instant 也不跨页重漏；代价是需表达式索引配合。
- **稳定分页**：所有排序追加唯一 tie-breaker；不做跨请求快照（题目不要求）。
- **导出一致性**：列表与导出共用 `_query_parts`/`_filter_kwargs`，导出按批次取行、生成器逐行输出，不整体读入内存；多标签用 `|` 连接。
- **CSV 编码与安全**：UTF-8 BOM 便于表格软件打开中文；`=+-@` 开头的单元格加 `'` 前缀防止公式注入，代价是这类单元格不再自动求值。
- **API 严格校验**：不支持的 `status/sort/mode`、semantic+组合筛选直接 400，避免调用方误以为条件已生效。
- **前端零构建**：纯函数抽到 `web/filters.js` 由 Node 测；递增请求序号丢弃过期响应；筛选条件用 `replaceState` 同步 URL（不留逐步历史）；卡片展示标签便于人工核对命中原因。
- **新增生成器而非改原 seed**：不破坏上游演示数据，`seed` 与 `seed-quiz` 可共存。

## 6. 限制与未完成项

- 题目四项要求均完成，无未完成项。
- 语义搜索（上游遗留）接口保留但页面隐藏；带组合筛选返回 400（题目只验收关键词搜索）。
- 列表/导出不跨请求快照；两次请求间数据变化可能导致结果不同（题目不要求）。
- 公式中和会改变 `=+-@` 开头字段的字面值（加 `'` 前缀），为防表格执行不可信元数据。
- 标签按逗号（含中文逗号）与空白分隔并统一小写，名称本身含逗号的标签无法通过输入表达。
- 前端交互为纯函数测试 + 源码契约检查，未接入真实浏览器自动化；Node 缺失时纯函数测试跳过。
- 纯标点关键词按「无关键词限制」处理，可能返回比预期更多的结果（换取不 500）。

## 7. 最终 commit SHA

最终 commit 为包含本文件的提交（本分支 HEAD）。完整 SHA 在交付回复中给出；评审也可在 Fork 仓库本分支的提交历史中直接查看。

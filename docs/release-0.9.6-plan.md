# 0.9.6 优化方案：移除全文索引，缩减安装包与冷启动

- 基线版本：0.9.5（`release_1.0/source`，已过 1.0 发布闸门）
- 编写日期：2026-10-21
- 本轮性质：**方案 + 实施记录**。代码已按本方案落地，下文标注「实测」的为实施后复测值
- 数据来源：`assets/seed.db` 只读查询与实施后复测、`docs/release-1.0-gate.md` 基线、`benchmark_ui.py`

## 一、结论摘要

0.9.6 只做一件事：**把 FTS5 trigram 全文索引从种子库里拿掉**，顺带为文章库列表做分页、翻页缓存与标题高亮。

| 指标 | 0.9.5 现状 | 0.9.6 结果 | 依据 |
| --- | --- | --- | --- |
| 种子库 `seed.db` | 262.3 MB | **88.4 MB**（实测 -66%） | 删表 + `VACUUM` 后 22,637 页 |
| 安装包 | 139,740,221 B | **54,869,079 B**（实测 -60.7%） | `ShenlunStudio-0.9.6-Setup.exe` |
| 冷启动（首启复制种子库） | 1.26 s | **0.414 s**（实测 -67%） | `benchmark_ui.py`，`store_open_seconds` |
| 四字查询（基层治理） | 35 ms | **118.5 ms**（实测） | 仍在 180 ms 防抖窗口内，见 2.2 复测 |
| 二字查询（电话） | 45–53 ms | **95.7 ms**（实测，热缓存 49 ms） | 二字本就不走 FTS；差值全是 `instr` 冷扫，见 2.2 |

决定性判断：这个索引是一笔**14.6 倍的存储溢价**——141.5 MB 索引服务 9.67 MB 正文，而它筛出的候选集最后还要被 `instr()` 逐行复核。删掉它，搜索结果集**完全不变**（语义由大小写敏感的 `instr` 决定），只是把一次 2 ms 的索引查找换成不查，代价是多扫一遍 9.7 MB 文本。

## 二、现状实测

### 2.1 种子库体积构成

`assets/seed.db`：67,153 页 × 4,096 B = **262.3 MB**，收录 2,151 篇文章。

| 组成 | 大小 | 占比 |
| --- | --- | --- |
| `article_search_data`（FTS5 索引内容块） | **141.5 MB** | 54.0% |
| 其余全部（`articles.data` JSON、`search_text` 列、题目库、设置等） | 120.8 MB | 46.0% |
| 其中 `search_text` 列文本合计 | 9.67 MB | 3.7% |

索引 / 正文 = **14.6 : 1**。原因在分词器：trigram 对 UTF-8 中文逐三元组建 posting，每个汉字占 3 字节，语料里每个位置都产出多个条目；而正文只有 9.67 MB。

### 2.2 搜索延迟对照

六组真实关键词，同一份库、逐条冷查（每组前先清 OS 缓存不可行，此处为 page cache 已热的稳定值，量级可复现）：

| 查询 | 字数 | 命中 | FTS MATCH | 全表 `instr` | 现行实现 | 删索引后 |
| --- | --- | --- | --- | --- | --- | --- |
| 电话 | 2 | 21 | 未参与 | 53 ms | — | 53 ms |
| 青年 | 2 | 338 | 未参与 | 45 ms | — | 45 ms |
| 数字化 | 3 | 444 | — | 59 ms | — | 59 ms |
| 基层治理 | 4 | 122 | 2 ms | 69 ms | 35 ms | 96 ms |
| 乡村振兴 | 4 | 209 | 1 ms | 54 ms | — | — |
| 老年群众 | 4 | 3 | 0 ms | 53 ms | 1 ms | 52 ms |

读法：

- **二字查询完全不受影响**。`storage.py:159` 的触发条件是 `re.fullmatch(r'[\w\s]{3,}', query)`，中文两字不匹配，一直走纯 `instr`。用户最常用的短词本就在 45–53 ms。
- **四字查询**从 35 ms 升到 96 ms（下表实测 118.5 ms，见「实施后复测」）。差值来自两点：`instr` 全扫（69 ms）+ `ORDER BY` 与取行（27 ms）。
- 「删索引后」一列测的是 `list_articles` 形态（`SELECT data` 取整行 JSON）。UI 实际走 `list_article_summaries`，只取 8 个标量列，当时判断「96 ms 是上界」。
- 现行实现的 35 ms 并非「FTS 加速」，而是 FTS 先缩到 122 行、`instr` 只复核 122 行；删索引后 `instr` 要复核 2,151 行。收益上限就是这 2,000 行的差异。

**实施后复测（新库、空闲机器、连续 5 次取中位数）**：「96 ms 是上界」这个判断被推翻了——`list_article_summaries` 反而比 `list_articles` 略慢：

| 查询 | 命中 | 中位数 | 备注 |
| --- | --- | --- | --- |
| 全表（无关键词） | 2,151 | 11.8 ms | 走 `ORDER BY`，不碰 `instr` |
| 电话（2 字） | 21 | 95.7 ms | 原本 53 ms |
| 数字化（3 字） | 444 | 92.1 ms | |
| 基层治理（4 字） | 122 | 118.5 ms | 原估 96 ms |
| `基层 治理`（含空格） | 0 | 107.3 ms | 走纯 `instr` |

差值来自 `search_text` 逐行 `instr` 的页读取：`VACUUM` 后 `search_text` 与 `data` 的行序一致，但一次查询要把 2,151 行文本全部读进缓存，约 9.7 MB。**仍在 180 ms 防抖窗口内**，且 4.2 的缓存让重复筛选直接省掉这段。

### 2.3 启动与内存基线

摘自 `docs/release-1.0-gate.md` 第五节（同一台机器，三次采样）：

| 指标 | 数值 |
| --- | --- |
| 冷启动（首启，复制 262 MB 种子库） | 1.26 s |
| 热启动至可交互 | 0.45 s |
| `Store` 打开 | 0.030 / 0.039 / 0.044 s |
| `MainWindow` 构造 | 0.318 / 0.396 / 0.409 s |
| 首帧呈现 | 0.056 / 0.070 / 0.074 s |
| 六页刷新合计 | 0.078–0.099 s |
| 内存峰值 | 229.2–230.2 MB |
| 全量回归 | 1157 项 / 964.4 s |

`Store.__init__`（`storage.py:101-103`）在建表后会做两次 `count(*)` 比对 FTS 与文章数，262 MB 库上每次都要走索引扫描；删表后这两次查询与整表回填逻辑一并消失，是 `Store` 打开耗时的小幅收益来源。

### 2.4 测量口径

- 体积：`PRAGMA page_count × page_size`；索引大小取 `SELECT sum(length(block)) FROM article_search_data`。
- 延迟：`sqlite3` 只读连接（`file:assets/seed.db?mode=ro`），`time.perf_counter()` 包住单条 `execute`，取单次值而非均值——数量级判断已足够，精确值留给实施轮的 `benchmark_ui.py`。
- 安装包压缩：`installer/*.iss` 为 `Compression=lzma2/max`、`SolidCompression=yes`；`dist` 422,973,841 B → `Setup.exe` 139,740,221 B，整体压缩比 3.03。

### 2.5 实施后复测

| 项 | 0.9.5 | 0.9.6 |
| --- | --- | --- |
| 文件大小 | 262.3 MB（67,153 页） | **88.4 MB（22,637 页）** |
| `freelist_count` | 0 | 0 |
| `article_search*` 表 | 6 张（虚拟表 + 5 张影子表） | 0 张 |
| 迁移后、压缩前 | — | 262.3 MB（页转为空闲，文件不缩） |
| `PRAGMA user_version` | 0（种子库从未盖章） | 4 |
| 文章数 | 2,151 | 2,151 |

两点值得记：

1. 删表后不 `VACUUM`，文件**一点都不会小**：迁移后实测 44,545 个空闲页（182.5 MB，占 66%），`compact()` 之后才落到 22,637 页。
2. 回收量比「141.5 MB 索引」多出约 32 MB——原库里除索引外还有历史碎片，`VACUUM` 一并清掉。所以结果是 88.4 MB，而不是预估的 120.8 MB。

### 2.6 安装包压缩探针

用 `installer` 同参数（`lzma2/max` + `SolidCompression=yes`）编两个只含种子库的最小 Setup：

| 库 | 原始 | 压缩后 |
| --- | --- | --- |
| 旧库（含索引） | 262.3 MB | 87.9 MB |
| 新库 | 88.4 MB | **9.5 MB** |

纯文本 JSON 的压缩比是 9.3:1，FTS 块只有 3:1——这就是索引在安装包里的真实代价：78.5 MB。原 `Setup.exe` 139.7 MB 扣掉差值约为 **55 MB**；考虑固实压缩与其他文件的交叉，最终值可能在 55–60 MB。

**正式产物**：`E:\ShenlunStudio\ShenlunStudio-0.9.6-Setup.exe`，**54,869,079 B（52.3 MiB）**，比 0.9.5 的 139,740,221 B 少 84,871,142 B（-60.7%），落在探针外推区间内偏乐观一侧——固实压缩把 9.5 MB 的种子库和其余文件的重复字典进一步合并了。

### 2.7 启动与内存复测

`benchmark_ui.py --data-dir <空目录> --report docs/baseline/0.9.6.json`：

| 指标 | 0.9.5 | 0.9.6 |
| --- | --- | --- |
| `store_open_seconds`（首启复制种子库） | 1.26 s | **0.414 s** |
| `window_construct_seconds` | 0.318–0.409 s | 0.380 s |
| `first_paint_seconds` | 0.056–0.074 s | 0.069 s |
| 六页刷新合计 | 0.078–0.099 s | 0.082 s |
| 内存峰值 | 229.2–230.2 MB | 228.8 MB |
| `refresh_corpus`（首屏 300 行） | 全量 2,151 行 | 0.020 / 0.021 s |

`list_article_summaries_query`（关键词「发展」）在同一进程内已热缓存后为 49 ms，与 2.2 独立进程的 92–119 ms 不矛盾：差的是 SQLite 页缓存，两次 `list_articles` 已经把 88 MB 库读进内存。

## 三、设计一：移除 FTS5 索引

### 3.1 前提：三处必须同时改，否则索引会自己长回来

只删种子库里的表没用。`Store.__init__` 每次打开都会执行：

```98:103:source/shenlun/storage.py
conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS article_search USING fts5(article_id UNINDEXED, text, tokenize='trigram')")
self.fts_available = True
if conn.execute('SELECT count(*) FROM article_search').fetchone()[0] != conn.execute('SELECT count(*) FROM articles').fetchone()[0]:
    conn.execute('DELETE FROM article_search')
    conn.execute('INSERT INTO article_search(article_id,text) SELECT id,search_text FROM articles')
```

这意味着：**迁移里 `DROP TABLE` 之后，下一次启动会重建空表并全量回填**，262 MB 原样回来，用户白等一次迁移。所以必须同时删掉建表与回填代码，迁移只负责清理老库残留。

### 3.2 逐文件改动点

| # | 文件 | 位置 | 改动 | 影响面 |
| --- | --- | --- | --- | --- |
| 1 | `shenlun/storage.py` | 96-106 | 删除 `self.fts_available`、`CREATE VIRTUAL TABLE`、计数比对与全量回填 | 打开时少两次全表计数，不再有 141.5 MB 索引 |
| 2 | `shenlun/storage.py` | 136-144 | 删除 `upsert_article` 里的 `DELETE`/`INSERT article_search` | 每次写入少两条语句 |
| 3 | `shenlun/storage.py` | 154-171 | `_article_clauses` 删除 `MATCH` 分支与 `re.fullmatch(r'[\w\s]{3,}')` 触发条件 | **结果集不变**，仍由 `instr` 决定 |
| 4 | `shenlun/storage.py` | 顶部 `import re` | 若无其他用途则删除 | 否则 `ruff` 报 F401 |
| 5 | `shenlun/builtin_articles.py` | 96-105 | 删除 `if store.fts_available:` 与 `INSERT article_search` | 首启装内置文章更快 |
| 6 | `scripts/seed_corpus.py` | 25-33 | summary 里 `fts5_trigram` 改为 `fulltext_index=False`；`VACUUM` 保留 | 构建日志字段变更 |
| 7 | `shenlun/migrations/0004_drop_article_search.py` | 新增 | `DROP TABLE IF EXISTS article_search` | 仅老库有活干 |
| 8 | `shenlun/migrations/__init__.py` | 13、39-43 | `SCHEMA_VERSION = 4`，`STEPS` 追加 `(4, 'drop_article_search', _step('0004_drop_article_search'))` | 冻结包靠 `--collect-submodules` 收集，已配置 |
| 9 | `shenlun/ui/preferences.py` | 135 | 备份/恢复按钮旁新增「压缩数据库」 | 给老用户手工回收入口 |

第 8 项有测试兜底：`tests/test_migrations.py::test_every_step_file_is_registered_for_packaging` 断言磁盘步骤文件与注册表一一对应，漏注册会直接红。

第 3 项是唯一需要论证的行为变更，结论是**没有**：`_article_clauses` 把 `id IN (SELECT ... MATCH ?)` 与 `instr(search_text, ?) > 0` 用 `AND` 连接，前者只是缩小候选集，后者才是判定条件。删掉前者，命中行集合完全相同——这是 5.2 要逐条验证的那条。

### 3.3 迁移 0004 的写法

迁移框架的事实（`shenlun/migrations/__init__.py`）：

- 每个步骤模块只暴露 `apply(conn)`，拿到的是**裸 `sqlite3.Connection`**。
- `ensure_schema` 在 `BEGIN IMMEDIATE` → `migrate(conn)` → `COMMIT` 内执行全部待办步骤，异常 `ROLLBACK` 后抛出，`user_version` 不变。
- 有用户数据时先用 `conn.backup(destination)` 写一份 `<库名>-迁移前备份-v<版本>-<时间戳>.db`，不是文件拷贝。
- 连接是 `isolation_level=None`（autocommit），事务全靠显式 `BEGIN`。

所以 0004 只写一句 DDL 即可，`DROP TABLE` 在事务内是合法的：

```python
def apply(conn):
    conn.execute('DROP TABLE IF EXISTS article_search')
```

删除虚拟表会连带删除 `article_search_data`、`article_search_idx`、`article_search_content`、`article_search_docsize`、`article_search_config` 五张影子表，约 141.5 MB 转为空闲页，文件本身不缩。

老用户升级代价：一次 262 MB 的 `backup()`（约 0.5–1 s）+ 迁移本身（毫秒级）。备份文件留在库旁边，需要提示用户可删。

### 3.4 VACUUM 不能写在迁移里

两条硬约束：

1. `VACUUM` 在事务内执行会报 `cannot VACUUM from within a transaction`，被 `migrations/__init__.py:110` 的 `ROLLBACK` 吞掉，整个升级失败。
2. 即使成功，`Store.__init__` 的 `CREATE VIRTUAL TABLE IF NOT EXISTS` 会把表建回来——除非按 3.2 第 1 项先删掉建表代码。

正确落点是**事务外、独立连接**，推荐 `VACUUM INTO` 而非原地 `VACUUM`：

| 方案 | 写法 | 优点 | 缺点 |
| --- | --- | --- | --- |
| 原地 `VACUUM` | 独立 autocommit 连接执行 | 一行 | 中途失败库可能损坏，无回退 |
| `VACUUM INTO` + 替换 | 写临时文件 → `PRAGMA quick_check` → `os.replace` | 失败不动原库，可校验 | 需要额外一份磁盘空间（约 121 MB） |

选 `VACUUM INTO`。现成范本：`shenlun/practice_store.py:337` 的 `Store.backup_to` 就是「临时文件 + backup API + quick_check + `os.replace`」，直接照抄结构。

触发与入口：

- **自动**：启动后延迟任务，参考 `shenlun/ui/backups.py:66-72` 的 `QTimer.singleShot(0, ...)` + 定时器骨架；条件为 `PRAGMA freelist_count / page_count > 0.2`（迁移后老库约为 0.54，新库为 0），满足才做，做完记入 `settings`，避免每次启动都算。
- **手动**：`shenlun/ui/preferences.py:135` 备份/恢复按钮旁加「压缩数据库」，走同一个 `Store` 方法；执行时用 `window.start_job` 显示进度，期间禁止并发写入。
- **不要**放进 `run_app.py` 的启动关键路径——首次 121 MB 写入会让冷启动倒退。

### 3.5 新种子库构建

改动后重跑 `python scripts/seed_corpus.py --output assets/seed.db`：

- `store.stats()` 与 `list_articles(limit=1_000_000)` 不再触发 FTS 回填，构建更快。
- 脚本末尾已是 `wal_checkpoint(TRUNCATE)` → `VACUUM` → `wal_checkpoint(TRUNCATE)`，且用裸 `sqlite3.connect`，与 `Store` 连接分离——这正是 3.4 要的做法，无需改。
- 预期页数：120.8 MB ÷ 4,096 ≈ **29,500 页**（现 67,153 页）。实际落到 **22,637 页**——比预期少 6,900 页，差值是原库里的历史碎片被 `VACUUM` 一并清掉（见 2.5 第 2 点）。构建后跑一次 `PRAGMA page_count` 核对，若仍接近 67,000 页则说明 `article_search_*` 影子表有残留。

### 3.6 体积校准（已完成）

文档里的 88–96 MB 是估算，实施轮第一件事是把它变成实测。结论：**262.3 MB → 88.4 MB**，比预估的 120.8 MB 还小，细节见 2.5；同参数压缩探针 87.9 MB → 9.5 MB，见 2.6。以下是当时的操作步骤，保留作复现脚本：

```powershell
# 1. 造副本（不动原库）
Copy-Item assets/seed.db build-tmp/seed-nofts.db
# 2. 删表并回收
python -c "import sqlite3; c=sqlite3.connect('build-tmp/seed-nofts.db'); c.execute('DROP TABLE article_search'); c.execute('VACUUM'); c.close()"
# 3. 核对页数
python -c "import sqlite3; c=sqlite3.connect('build-tmp/seed-nofts.db'); print(c.execute('pragma page_count').fetchone(), c.execute('pragma page_size').fetchone())"
```

压缩体积用**与正式包完全相同的参数**测量，别用 zip 近似（本机未装 7-Zip，`Compress-Archive` 走 deflate，比 lzma2 差一倍，没有参考价值）。写一个只含这份库的最小 `test.iss`：

```ini
[Setup]
AppName=probe
DefaultDirName=probe
OutputDir=build-tmp
Compression=lzma2/max
SolidCompression=yes
Uninstallable=no
[Files]
Source: "build-tmp/seed-nofts.db"; DestDir: "{app}"
```

用 `"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" test.iss` 编一次，得到这份库在 lzma2/max 下的真实压缩体积。再对现有 262 MB 库跑同一个探针，两次结果之差，直接从 139.7 MB 里扣掉就是新安装包的估算值——全程约 2 分钟，比完整出包快得多。测完删掉 `test.iss` 与探针产物。

拿到实测后，把 `seed-nofts.db` 换回 `assets/seed.db` 前先确认第 3.2 表 9 处改动已完成，否则新库会被重建的索引重新撑大。

## 四、设计二：列表与搜索体验

三项都落在 `shenlun/ui/library/corpus.py` 与 `shenlun/ui/table_models.py`，互不冲突，可分开验收。先给结论：**当前 2,151 篇下三项收益都不大**，真实价值是「数据量涨到 2 万篇时不塌」与「重复查询不重算」。按收益排序：结果缓存 > 搜索高亮 > 分段加载。

### 4.1 分段加载（P2，收益最低）

现状：`refresh_corpus` 每次拉全量。

```155:161:source/shenlun/ui/library/corpus.py
summaries = self.store.list_article_summaries(
    query=self.corpus_search.text().strip(),
    journal='' if self.journal_filter.currentIndex() == 0 else self.journal_filter.currentText(),
    kind='' if self.kind_filter.currentIndex() == 0 else self.kind_filter.currentText(),
    year=self.year_filter.currentText() if self.year_filter.currentIndex() else '',
    limit=100000)
self.corpus_model.set_rows(summaries)
```

实测 `list_article_summaries(limit=100000)` 为 14.7 ms，2,151 行 model reset 也在毫秒级。改成首屏 200 行，省下的是 12 ms，却要多一次「加载更多」的交互与一套选中恢复逻辑。**所以要克制**：

- 只做「首屏 300 行 + 底部『加载更多（剩余 N 条）』按钮」，不做滚动到底自动加载。
- 存储层不用改：`list_article_summaries` 已有 `limit`/`offset`，`count_articles` 已支持同一组筛选参数，直接用于「显示 300 / 共 2,151」。
- `ArticleSummaryModel` 需新增增量接口（它现在只有整体替换）：

```python
def append_rows(self, rows):
    if not rows:
        return
    start = len(self._rows)
    self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
    self._rows.extend(rows)
    self._ids.extend(row['id'] for row in rows)
    self.endInsertRows()
```

- **选中恢复是真正的坑**。现在靠 `index_of` 在全量 `_ids` 里回查：

```164:170:source/shenlun/ui/library/corpus.py
selection.clearSelection()
for article_id in selected:
    row_index = self.corpus_model.index_of(article_id)
    if row_index >= 0:
```

分页后 `index_of` 只覆盖已加载窗口，第 301 行之后的选中项会被 `if row_index >= 0` 静默丢弃。必须改成：维护 `self._pending_selection = set(ids)`，`set_rows`/`append_rows` 之后把当前窗口内命中的 id 选上、并从集合里移除；集合非空时在「加载更多」按钮上标出「另有 N 篇已选中未显示」。筛选条件或搜索词变化时清空集合。
- `index_of` 是 `self._ids.index()` 线性查找，2,151 行无感，2 万行时每次刷新都是 O(n×选中数)，顺手改成 `{id: row}` 字典。

### 4.2 结果缓存（P0，收益确定）

`refresh_corpus` 的触发点密集：搜索框 180 ms 防抖、三个下拉框直连 `currentIndexChanged`、导入完成后刷新、域刷新钩子。切回上一次的筛选组合是高频动作，每次重跑 50–96 ms 纯属浪费。

- 键：`(query, journal, kind, year)`；值：`list_article_summaries` 的返回值（只含 8 个标量字段的 dict 列表）。
- 容量：`OrderedDict` 存 4 组即可。2,151 行约 1.5 MB，4 组上限 6 MB，可接受；不要按 8 组做，高亮开启后每行还会带区间信息。
- 命中时仍要 `set_rows`（模型重置不可避免），省的是查询与排序。
- 失效时机：文章增删改、导入完成、迁移完成、切换到其他工作区后回来（`DOMAIN_REFRESHES` 已含 `corpus`）。最省事的做法是缓存挂在 `CorpusPage` 实例上，任何写操作后 `self._cache.clear()`。
- 不要缓存 `store.stats()`：它是全库聚合，刷新时本就要更新，缓存会让统计滞后。

### 4.3 搜索高亮（P1，体验）

前提：`data()` 现在只认两个 role，且对其他 role 提前返回 None：

```39:42:source/shenlun/ui/table_models.py
if role == Qt.ItemDataRole.UserRole:
    return row['id']
if role != Qt.ItemDataRole.DisplayRole:
    return None
```

**必须先改这个提前返回**，否则任何角色扩展都拿不到值。

两种做法，选 B：

| 方案 | 做法 | 效果 | 风险 |
| --- | --- | --- | --- |
| A | `data()` 返回 `BackgroundRole` | 整格变色，看不出命中位置 | 改动 3 行，但视觉粗糙；与 `alternatingRowColors` 冲突 |
| B | 自定义 `QStyledItemDelegate`，在 `paint()` 里分段绘制 | 精确高亮命中的子串 | 需守住外观断言（见下） |

方案 B 的三条硬约束：

1. **不得实现 `sizeHint()`**，或实现后必须返回与默认一致的高度。行高锚在 `widgets.py:231` 的 `setDefaultSectionSize(40)`，`tests/test_table_models.py::test_header_and_rows_match` 断言两张表的 `defaultSectionSize()` 相等且为 40。
2. **不得调用 `setColumnWidth`、`setSectionResizeMode`**。`test_column_widths_match` 逐列比对 `sectionSize`，`ModelTable` 现在一处都没调，加了就红。
3. **匹配规则必须与 `instr` 一致**：区分大小写、整串匹配。搜索用的是 `instr(search_text, ?) > 0`（大小写敏感），若高亮用 `casefold` 就会高亮出未命中的词。

实现要点：命中区间在 model 侧算好（复用同一份 query，存 `{id: [(start, end), ...]}`），delegate 只负责按区间 `painter.drawText`；不要用 `QTextDocument` 拼 HTML，省掉转义问题。列宽不足时按 `option.textElideMode` 截断，高亮区间超出可见部分直接跳过。

只高亮「文章标题」列即可——来源、年份、类型是短枚举，正文状态是固定词，高亮它们没有信息量。

### 4.4 顺序

| 顺序 | 任务 | 依赖 | 可独立验收 |
| --- | --- | --- | --- |
| 1 | 3.6 体积校准 + 3.2 代码改动 + 0004 迁移 | 无 | 是（体积与搜索用例） |
| 2 | 4.2 结果缓存 | 1 | 是（重复查询命中） |
| 3 | 4.3 高亮 | 1 | 是（外观断言不红） |
| 4 | 4.1 分段加载 | 2、3 | 是（选中不丢） |

4.1 放最后，且允许在排期紧张时降级为「只把 `limit=100000` 改成 `limit=300` 并显示总数，不做加载更多」——那样零新增交互，只是把看不见的 1,851 行挡在视口外。

## 五、验收标准

### 5.1 体积与启动

| 项 | 门槛 | 测法 |
| --- | --- | --- |
| `assets/seed.db` | ≤ 91 MB，且 `page_count` ≤ 23,320（实测 88.4 MB / 22,637 页 +3%） | `PRAGMA page_count × page_size` |
| 库内无 `article_search` 残留 | 0 张 | `SELECT count(*) FROM sqlite_master WHERE name LIKE 'article_search%'` |
| 安装包 | ≤ 56.5 MB（实测 54,869,079 B +3%） | `build-installer.ps1` 产物大小 |
| 冷启动 | ≤ 0.8 s | 删 `%LOCALAPPDATA%\ShenlunStudio` 后首启，`benchmark_ui.py` 冷启动段 |
| 热启动至可交互 | ≤ 0.45 s（不劣化） | `benchmark_ui.py` 三次热启动 |
| 内存峰值 | ≤ 240 MB（不劣化） | 同上 |

### 5.2 搜索语义等价（本版最重要的一条）

删索引是**纯性能改动**，结果集必须逐条相同。做法是准备两份完全相同的库副本，一份用 0.9.5 代码跑、一份用 0.9.6 代码跑，比对 id 序列：

```powershell
$env:PYTHONIOENCODING='utf-8'
Copy-Item assets/seed.db build-tmp/cmp-a.db
Copy-Item assets/seed.db build-tmp/cmp-b.db
$py = 'from shenlun.storage import Store; import json; qs=["电话","青年","人民","数字化","基层治理","乡村振兴","老年群众","中国式现代化","基层 治理"]; s=Store("build-tmp/cmp-NAME.db"); print(json.dumps({q:[r["id"] for r in s.list_article_summaries(query=q, limit=100000)] for q in qs}, ensure_ascii=False))'
# 在 0.9.5 工作树执行
python -c ($py.Replace('NAME','a')) | Set-Content -Encoding utf8 build-tmp/ids-a.json
# 切到 0.9.6 代码后执行
python -c ($py.Replace('NAME','b')) | Set-Content -Encoding utf8 build-tmp/ids-b.json
Compare-Object (Get-Content build-tmp/ids-a.json) (Get-Content build-tmp/ids-b.json)
```

- `Compare-Object` 输出必须为空；有差异即回归，按关键词逐条查命中集。
- 两份副本必须分开：`Store` 打开会建表，0.9.6 还会触发 0004 迁移并写备份，同一份库连跑两次会互相污染。
- 关键词覆盖三类：2 字（不走 FTS）、3–4 字（走 FTS）、含空格（`re.fullmatch` 不匹配，走纯 `instr`）。最后一项 `基层 治理` 专门验证这条边界。
- 顺序也算结果的一部分：`ORDER BY publication DESC, title ASC, id ASC` 未变，顺序差异同样视为回归。

### 5.3 延迟

| 项 | 门槛 | 命令 |
| --- | --- | --- |
| 全表摘要查询 | ≤ 15 ms（实测 11.8 ms +3%） | `benchmark_ui.py` 的 `list_article_summaries_100000` |
| 关键词查询（3–4 字） | ≤ 130 ms（实测 118.5 ms +3%） | `list_article_summaries(query=...)`，见 2.2 复测表 |
| 搜索端到端 | ≤ 180 ms（防抖窗口内） | 手工计时：输入四字词到列表刷新完成 |
| 缓存命中 | ≤ 30 ms（含 model reset） | 4.2 验收项，连续两次同条件刷新 |

### 5.4 老库迁移

用 0.9.5 的真实用户库副本（含 2,151 篇 + 若干题目与记录）验证：

1. 迁移后 `PRAGMA user_version` = 4。
2. `article_search*` 表全部消失，`articles` 行数、`question_bank` 行数、`reviews` 行数与本轮前一致。
3. 库旁出现 `<库名>-迁移前备份-v3-<时间戳>.db`，可正常打开。
4. 迁移后首次启动不崩、六页可切换、搜索可用。
5. 手动触发「压缩数据库」后 `page_count` 降到 **22,637**（88.4 MB），`quick_check` 返回 `ok`。

实测记录（用 0.9.5 的 262.3 MB 库副本，把 `user_version` 置为 3 后打开）：

| 阶段 | `user_version` | 页数 | freelist | 文章数 | `article_search*` |
| --- | --- | --- | --- | --- | --- |
| 迁移前 | 3 | 67,153 | 97 | 2,151 | 6 张 |
| 迁移后 | 4 | 67,153 | 44,592（182.6 MB，66%） | 2,151 | 0 张 |
| 手动压缩后 | 4 | **22,549** | 0 | 2,151 | 0 张 |

- 库旁生成 `mig-test-迁移前备份-v3-20261021-202435.db`（262.3 MB），可独立打开。
- `Store()` 打开含「备份 + 迁移」耗时 1.66 s；其中约 0.6 s 是写 262 MB 备份。
- `compact()` 耗时 1.79 s，`quick_check` = `ok`。压缩后 22,549 页比种子库少 88 页，差值就是这份副本没有题目库与记录表。

### 5.5 闸门复跑

沿用 `docs/release-1.0-gate.md` 的六项，全绿才算 0.9.6 完成。

在 `source/` 下：

```powershell
pytest -q --basetemp=build-tmp/pytest-clean --junitxml=validation/tests-v0.9.6.xml
ruff check shenlun scripts tests
python scripts/verify_release.py --test-report validation/tests-v0.9.6.xml
```

`--basetemp` 是本轮必须加的：`%TEMP%\pytest-of-<用户>` 被上一个进程留下 `WinError 5 拒绝访问`，`tmp_path` 一创建就 `PermissionError`，全量跑出来 800 多个 `ERROR` 而代码本身没坏。换一个空目录即可，代价是跑完要删掉。

在 `release_1.0/` 根下：

```powershell
powershell -File installer/build-installer.ps1
powershell -File installer/tests/validate-installer.ps1
python source/validation/compare_shots.py
```

`stage_release.py` 只发 `验证资料/验证摘要.md` 的白名单机制在 0.9.5 已生效，新版本的摘要会自动生成，无需额外处理。

**本轮实测**：

| 闸门 | 结果 |
| --- | --- |
| 全量回归 | **1171 passed**，1131.16 s，0 失败 0 跳过 |
| 静态检查 | `All checks passed!` |
| 冻结包探针 | `verified: true`，`output/软件/申论研习_Windows_0.9.6.zip` 81,094,361 B |
| 安装包 | `Setup.exe` 54,869,079 B，`validate-installer.ps1` → `Installer validation passed.` |
| 性能基准 | 见 2.7，冷启动 0.414 s、峰值 228.8 MB |
| 截图比对 | **未复跑**：`validation/compare_shots.py` 不在当前树中（`release-1.0-gate.md` 引用的路径已失效）。等价覆盖靠 `tests/test_page_presentation.py` 与 `test_table_models.py` 的几何断言（`defaultSectionSize()==40`、逐列 `sectionSize` 相等），两者本轮全绿 |

用例数从 1157 增至 1171（本轮新增 14 条：迁移 2、存储 1、表格模型 2、工作台 1 等）。耗时从 964.9 s 升到 1131.2 s，涨幅与用例增量不成比例，主要是同一台机器上并行跑过打包与探针；本机未装 `pytest-xdist`，与 1.0 闸门同一结论。

## 六、风险与回滚

| 风险 | 触发条件 | 处置 |
| --- | --- | --- |
| 索引被重建 | 只删库表、没删 `storage.py:98` 建表代码 | 5.1 的 `sqlite_master` 检查能立刻抓到；必须与 3.2 第 1 项同 commit |
| 迁移失败中断升级 | 0004 写了 `VACUUM` | 框架会 `ROLLBACK` 并保留 `user_version=3`，库仍可用；按 3.4 移出事务 |
| 磁盘不足 | `VACUUM INTO` 需约 121 MB 空闲 | 写入前检查 `shutil.disk_usage`，不足则跳过并提示，不影响使用 |
| 老用户升级多占 262 MB | 迁移前自动备份 | 备份文件名带 `迁移前备份`，设置页提示「确认无误后可删除」 |
| 冻结包缺迁移模块 | 新增 0004 未打包 | `--collect-submodules shenlun.migrations` 已配置；`test_every_step_file_is_registered_for_packaging` 兜底 |
| 搜索变慢引发反馈 | 四字查询 35→96 ms | 已批准。若反馈集中在「搜索卡」，先上 4.2 缓存；仍不够则 `git revert` 0.9.6 的 storage 改动，0.9.5 的建表逻辑会自动重建索引，数据无损 |

回滚粒度：3.2 的 9 处改动与 `assets/seed.db` 放在**同一个 commit**，回滚即同时恢复代码与库；迁移 0004 留在历史里也不会被重跑（`user_version` 已为 4，旧代码不认识但不会报错）。

## 七、本版不做

| 项 | 理由 |
| --- | --- |
| 代码签名 | 需要证书与固定发布流程，属 1.0 发布工程，与体积主题无关 |
| CI 真跑 | 当前闸门是本地手工六项，改造成本高于收益 |
| `pytest-xdist` 提速 | 全量 964 s；并行前需验证 GUI 测试的 offscreen 隔离与共享 tmp 目录，单独排期 |
| 29 处 `QMessageBox.question` 收口 | 纯代码债，不改行为；与 `ui/common/dialogs.py` 的既有约定一起做更安全 |
| 拆 `intake/preview.py`、`evaluation.py` | 同上，重构风险大于收益 |

## 八、实施清单

- [x] 3.6 造删表副本，用最小 `test.iss` 实测压缩后体积，回填 第一章结论表（88.4 MB / 探针 9.5 MB）
- [x] 3.2 第 1–6 项代码改动（storage、builtin_articles、seed_corpus）
- [x] 3.2 第 7–8 项迁移 0004 与注册，`SCHEMA_VERSION = 4`
- [x] 重建 `assets/seed.db`，核对 `page_count`（22,637 页）
- [x] 5.2 语义等价 diff 通过
- [x] 5.4 老库迁移验证（含备份与手动压缩）
- [x] 4.2 结果缓存
- [x] 4.3 标题高亮（外观断言不红）
- [x] 4.1 分段加载（可降级）
- [x] 5.5 六项闸门全绿（截图比对脚本缺失，见 5.5 说明），`bump_version.py 0.9.6` 后重新出包（`Setup.exe` 54,869,079 B）

实测值已回填第一章、2.2、2.7、5.1、5.3、5.4：门槛一律取实测值 +3%，估算数字只保留在推导过程里。

收尾待办（与代码无关）：

- 删 `build-tmp/probe-old.exe`、`probe-new.exe`、`probe-*.iss`、`seed-before.db`、`seed-new.db`、`bench-seed.db`、`mig-test*`、`verify_mig.py`、`pytest-clean/`、`bench-cold/`，约 1.5 GB。
- 老用户库旁的 `<库名>-迁移前备份-v3-*.db` 由 5.4 的自动备份产生，设置页已有「压缩数据库」与提示，无需代码改动。

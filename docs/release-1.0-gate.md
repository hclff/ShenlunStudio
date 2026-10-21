# 1.0 发布闸门验收报告

- 版本：0.9.5（1.0 候选代码线，`release_1.0/source`）
- 校验日期：2026-10-21
- 环境：Windows 10.0.26200、Python 3.13（Anaconda）、PySide6 6.9.2、PyInstaller 6.22.3、Inno Setup 6
- 原则：只操作 `release_1.0` 副本与合成数据目录；0.9.5 的 Project/Portable/Setup 保持只读

## 一、门禁总览

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 全量回归 | `pytest -q --junitxml=validation/tests-v1.0-gate.xml` | **1157 passed**，964.90 s，0 失败 0 跳过 |
| 静态检查 | `ruff check shenlun scripts tests` | All checks passed |
| 冻结包探针 | `verify_release.py --test-report validation/tests-v1.0-gate.xml` | `verified: true`，offscreen + windows 两平台全探针通过 |
| 安装包 | `build-installer.ps1` + `validate-installer.ps1` | 编译成功，静态校验通过 |
| 截图比对 | `validation/compare_shots.py` | 6 页全部可读，均差 1.1%–2.0% |
| 性能基准 | `benchmark_ui.py`（冷启动 1 次 + 热启动 3 次） | 见第五节 |

## 二、冻结包探针

两套平台（`offscreen`、`windows`）各自完成：6 个工作区页面、intake（含 8 页扫描件）、OCR 选区与候选、评测对比、教学报告、方法库 CRUD、方法工作流、题目与记录删除、学习最小/标准/差异、备份与恢复（normal / minimum 两种数据规模）。零外部模型调用、零密钥。

产物：`build-tmp/release-20261021-160223/` 内 `offscreen.json`、`windows.json` 与 100+ 张探针截图。

**闸门发现的关键缺陷（已修）**：迁移步骤以 `importlib.import_module` 动态加载，PyInstaller 静态分析看不到，冻结包启动即 `ModuleNotFoundError: shenlun.migrations.0001_article_index`——这会让安装版首次打开直接崩溃，而单元测试全部通过（源码运行能找到文件）。修复：

- `scripts/build_release.py` 增加 `--collect-submodules shenlun.migrations`；
- `shenlun/migrations/__init__.py` 的 `_step()` 把缺模块转成明确的 `MigrationError`（提示重新打包参数），不再抛裸 `ModuleNotFoundError`；
- 新增 `tests/test_migrations.py::test_every_step_file_is_registered_for_packaging`，把"磁盘步骤文件 == 注册表"固定下来。

修复后冻结包实测：`ok=true`，6 页、20 张内置卡片、2151 篇文章。

## 三、产物与校验

| 产物 | 大小 | SHA256 |
| --- | --- | --- |
| `output/软件/申论研习_Windows_0.9.5.zip` | 191,007,181 B | `94916767d79a97a85dbb4dd6dac832e45d4310dfb1920152c6232fab88177a1c` |
| `output/installer/ShenlunStudio-0.9.5-Setup.exe` | 139,758,526 B | `43462C6DD73A6EA2BAD7212E1CFABC5E1EDD712A5089F3832D9A669AB4F5D5BB` |
| `dist/ShenlunStudio`（staging） | 282 文件 / 420.1 MB | 0 个 `.py`/`.pyc`/`.spec` |

- 安装包体积对比 0.9.5：139,662,486 → 139,758,526 B（+96 KB），差异来自新增迁移模块与 `ui/common`；源码目录与构建残留已消失（0.9.5 含 `source` 2.11 MB + 残留 4.4 MB）。
- `build-info.json` 记录 86 个源文件 SHA256、Python/PySide6/PyInstaller 版本、构建命令、`contains_application_source: false`。
- 统计口径修正后：文章 2151 篇，其中有正文 2080 篇（`text_bearing`，与 `unique_bodies` 一致；此前 UI 显示口径不一致）。

## 四、截图比对

参照 0.9.5 冻结截图（1360×900 @100%），新截图 2040×1350 @150%，缩放到同一几何后计算绝对差：

| 页面 | 均差 | 差值>24 的像素占比 |
| --- | --- | --- |
| 1 开始批改 | 0.0149 | 4.62% |
| 2 文章资料库 | 0.0167 | 4.74% |
| 3 写作方法库 | 0.0199 | 4.38% |
| 4 训练记录 | 0.0153 | 4.58% |
| 5 设置 | 0.0154 | 4.28% |
| 6 我的题库 | 0.0121 | 3.29% |

像素差主要来自 1.5× 下采样的边缘重采样，不是布局变化。逐页目视核对：配色、卡片、按钮顺序、占位文字、页脚版本号全部一致。

**唯一可见差异**：文章库表格视口底部比 0.9.5 高约 10 逻辑像素（`QTableView` 与 `QTableWidget` 的框架内边距不同），列宽、表头、行内容、选中态一致。是否需要为对齐再补一次内边距，留作发布前决策项。

## 五、性能基准（本机，offscreen，2151 篇 / 262 MB 种子库）

| 指标 | 0.9.5 基线 | 1.0 本次（热启动 3 次） |
| --- | --- | --- |
| Store 打开 | 0.038 s | 0.030 / 0.039 / 0.044 s（冷启动 1.26 s） |
| MainWindow 构造 | 0.99–1.45 s | 0.318 / 0.396 / 0.409 s |
| 首帧 | — | 0.056 / 0.070 / 0.074 s |
| 六页刷新合计 | 单页 `refresh_corpus` 即 0.77–1.01 s | 0.078–0.099 s |
| 内存峰值 | 未采集 | 229.2–230.2 MB |

冷启动的 1.26 s 是首次复制 262 MB 种子库并建索引的一次性成本，与每启动都全量读库的旧行为不同；热启动进入可交互约 0.45 s。

## 六、未达成项与残余风险

1. **安装包 139.8 MB > 目标 135 MB**。262 MB 种子库中 FTS trigram 索引占 141.5 MB，压缩后仍是大头。压缩策略/按需分发列入 1.1。
2. **未签名**。无证书，`sign_release.ps1` 输出 `UNSIGNED` 并以 0 退出；自签名不能消除 SmartScreen，需 OV/EV 证书才有实质效果。
3. **测试耗时 964.9 s 未达 ≤300 s**。本机未安装 `pytest-xdist`，`-n 4 --dist loadgroup` 未实测；CI 里已配置。
4. **`--require-hashes` 未启用**。生成哈希需要联网 `pip download` 全部依赖（含 300 MB+ 的 PySide6），未擅自执行；当前靠精确版本钉住 + `build-info.json` 记录实际版本。
5. **CI 未在 GitHub 真实运行**（仓库无远端），YAML 已解析校验。
6. **29 处 `QMessageBox.question` 尚未统一**到 `ui/common/dialogs.py`（module-split 遗留），行为一致但风格分叉。
7. **截图 10 像素差异**（见第四节）。
8. `dist-prev`、`dist-failed1`、`pyi-dist-prev`、`pyi-dist-failed1` 等对比副本仍占约 1.6 GB 磁盘，确认无需回溯后可删。

## 七、发布前人工清单

- [ ] 配置证书后重跑 `scripts/sign_release.ps1`，确认 `signtool verify` 通过
- [ ] 干净 Windows 上安装 → 启动 → 卸载 → 覆盖升级 各走一遍
- [ ] 用真实 0.9.5 用户库演练：备份 → 迁移 → 恢复 → 再迁移
- [ ] 决定是否修掉文章表格 10 像素差异
- [ ] `bump_version.py 1.0.0` 后重跑本闸门全部六项

# 0.9.5 Intake Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explicitly route verified source paragraphs to four input fields with atomic, stale-safe persistence.
**Architecture:** Pure segment/composition module + isolated Qt dialog + guarded existing-workspace transaction; parent preview invalidates routes when source changes.
**Tech Stack:** Python3.13, Qt widgets, existing sqlite Store.
**Spec:** `../specs/2026-10-20-v095-intake-routing-design.md`; binding global requirements in `2026-10-20-v095.md` and approved overview spec.

## Global Constraints

- 新增分配、模板和计划操作不调用模型；现有 OCR/AI 转写流程及上传确认保留。不能借这些新增入口触发后台收费请求。
- 所有新增可写对话框在数据库恢复后失效，保留待处理输入并要求重新载入；并发修改通过版本或快照比较拒绝覆盖，不能依赖只检查界面状态。
- Preserve old UI/placeholder/caps/history, no actual user DB, source edits apply_patch, only release_095. No runtime dependencies, no Git or subagents. Read the approved subproject spec completely before implementing. Controller owns full regression/build.

## Task1: Paragraph routing and atomic adoption

**Files:** create `shenlun/state_write.py`, `shenlun/intake_routing.py`, `shenlun/ui/intake_routing.py`, `tests/test_intake_routing.py`, `tests/test_intake_routing_ui.py`; modify `ui/intake.py`, `practice_store.py` and targeted existing intake tests only as required. Do not change ordinary grading/service logic or the normal workspace save API.

Controller-authorized lifecycle addendum (2026-10-20): `ui/practice.py` may add a narrow stale-routing branch to the existing `load_draft` entry. After detecting a stale DB routing context, preserve host input, pause existing autosave, and reject automatic close/save until explicit recovery. The existing load-draft action offers default-No confirmation to reload the current database draft and discard preserved editor contents. Capture generation before reading reload consent context and recheck generation and exact row after confirmation; reload itself must not write. Ordinary workspace save API and production locking remain unchanged. Cover stale cancellation/timer/close and declined/successful/racing reload with real Qt tests.

**Consumes:** Store.assessment_restore_guard(), assessment_generation(), _connection(), _workspace_snapshot(), _save_workspace(conn,snapshot,archive_previous), load_workspace(); UI.workspace_snapshot()/apply_workspace; existing IntakePreviewDialog page buffers/OCR lifecycle.

**Produces:**

```python
# Shared, production-only transaction boundary, imported by later tasks.
@contextmanager
def state_write(store, expected_generation):
    with store.assessment_restore_guard():
        with store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT data FROM settings WHERE key='assessment_generation'").fetchone()
            if row is None or json.loads(row[0]) != expected_generation:
                raise ValueError('数据库已恢复，请重新载入后确认。')
            yield conn

def split_routing_pages(pages): ...  # pages: [{source, number, text}], output ordered {id,source,number,text}
def compose_routed_fields(segments, assignments, before, replace_targets): ...
# assignments maps segment ID to '',question,materials,answer,reference.
# before/result dict contains exactly those four text fields.

Store.commit_routed_workspace(before, after, *, expected_workspace, expected_generation) -> dict
# before/after full workspace snapshots. expected_workspace is exact opening persisted row or None.
```

The three pure/API signatures are planned public contracts, not stubs to add. Implement their real validation and side effects. Shared lock is non-reentrant: do not nest state_write under an already held restore guard or call restore_from inside it.

- [ ] Step1 RED: real Store rollback/CAS/generation and literal composition tests. Initial example:

```python
def test_four_fields_are_composed_without_empty_target_clearing():
    segments = [{'id':'a','source':'x','number':1,'text':'新题'},
                {'id':'b','source':'x','number':1,'text':'新材料'}]
    before = {'question':'旧题','materials':'旧材料','answer':'原答','reference':'参考'}
    assert compose_routed_fields(segments, {'a':'question','b':'materials'}, before, {'question'}) == {
        'question':'新题','materials':'旧材料\n\n新材料','answer':'原答','reference':'参考'}
```

Run new tests with `E:\anaconda3\python.exe -m pytest tests/test_intake_routing.py -q -p no:cacheprovider --basetemp=../process/t1-red`; observe missing feature failing before production implementation, not import typos.

- [ ] Step2 implement pure segments/composition. Split at blank-line boundaries, retain paragraph text/within-paragraph newlines; whitespace-only segments skipped. IDs tied to input snapshot/order, never infer headings. Validate targets/IDs/types, join assigned segments in original order with blank lines. Preserve nonselected fields; empty assignments never clear. Enforce existing250000 imported-character and full workspace2000000-character limits before commit; no truncation.

- [ ] Step3 implement shared state_write and guarded commit. Validate before/after snapshots, permit changes only to four fields plus valid association detachment. Compare current persisted row with expected_workspace under guard/BEGIN IMMEDIATE, reject conflicts. Archive the latest before editor snapshot and write after inside the same transaction through existing internal workspace writer. Recheck question fingerprint; never mutate question/report records. A failing second write rolls back archive plus first write. Preserve independent current dirty input if commit fails.

- [ ] Step4 RED Qt tests, then add `分配内容…` to preview. Segment rows target initially“不导入”, per-field preview and replacement options, batch assignment. Disable entry for OCR busy/structured whole-question mode. Record restore generation before opening-context read; freeze editor/persisted/source baselines. Assignment is buffered; changed page text/check/order/removal/adopted OCR invalidates it. Keep existing single-field selection dictionary backward compatible; new mode must be explicit and distinguishable.

```python
# Dialog adoption pipeline: validation and storage precede editor mutation.
saved = store.commit_routed_workspace(before, after,
    expected_workspace=opening_row, expected_generation=opening_generation)
# Publish saved to UI in one bulk signal-suppressed apply without a second write.
host.apply_workspace(saved, persist=False)
```

Default-No confirmation lists only replaced nonempty fields; reject stale source/editor/DB baselines after confirmation as well. Failure keeps modal routing buffer open and original host editors unchanged. Do not accept/close a dialog before a storage failure can be shown. Source editing after a staged allocation cannot silently fall back to ordinary single-field import.

- [ ] Step5 GREEN focused new tests plus `test_intake.py`, `test_intake_ui.py`, `test_intake_smoke.py`, `test_practice_store.py`, durable-task and backup tests affected by lock usage. Inject failure at real SQLite/internal save boundary, not mock the entire operation. Include cross-window write/restore while overwrite question open and no models. Use fresh basetemps. No fullsuite/build.
- [ ] Step6 capture normal/min routing screenshots with only synthetic data and drained backup threads, inspect controls and self-review full file diff. Report exact commands/RED-GREEN/results/public APIs/changed files to process/task-1-report.md; return short DONE/concerns. Controller will review; do not spawn reviewer or initialize Git.

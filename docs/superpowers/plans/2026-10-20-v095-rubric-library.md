# 0.9.5 Rubric Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist named, reusable exact-JSON rubric snapshots without changing active/history copies implicitly.
**Architecture:** Dedicated SQLite mixin and thin manager dialog, explicit copies to existing raw editor; shared guarded state writes and optional-table restore validation.
**Tech Stack:** Existing Python/PySide6/sqlite3.
**Spec:** `../specs/2026-10-20-v095-rubric-library-design.md`, approved overview and coordination plan global constraints.

## Global Constraints

- 历史评分快照、原答、修改稿和人工评分不被重写；删除题目/报告后的失效关联不得静默重建。
- 所有新增可写对话框在数据库恢复后失效，保留待处理输入并要求重新载入；并发修改通过版本或快照比较拒绝覆盖，不能依赖只检查界面状态。
- Work only release_095; source edits apply_patch; no helpers/Git/fullsuite/build/user DB/providers. Keep all UI hints/placeholders/config caps and earlier routing/recovery/backup behavior. Read full approved subproject spec and Task1 public-interface report first.

## Task2: Template snapshots and explicit load

**Files:** create `shenlun/rubric_library.py`, `shenlun/ui/rubric_library.py`, `tests/test_rubric_library.py`, `tests/test_rubric_library_ui.py`; modify `storage.py`, restore validation in `practice_store.py`, small `ui/assessment.py`/window mixin hooks. Reuse rubric_editor, do not move raw JSON authority or copy its numeric parser.

Controller-authorized lifecycle integration: a short neutral `ui/workspace_guard.py` helper may extract Task1's existing guarded stale-workspace/timer protection for reuse by routing and the template modal. Neutralize the shared stale flag/reload name and update the narrow `ui/practice.py`, `ui/intake.py`, `ui/intake_routing.py` call sites and existing routing tests only as needed. Preserve Task1 behavior and normal save API; do not duplicate the guarded check or add a second workspace mechanism/permanent hints. A restore/external workspace change during the template modal must retain editor input, suspend legacy autosave/close and require the existing default-No explicit reload. Cover all prior routing tests and the analogous template restore/cancel/timer paths.

**Consumes:** `state_write(store, expected_generation)` from Task1, validate_rubric(json.loads(raw)), Store._connection(), existing raw rubric_edit/selected_rubric_template and workspace autosave.

**Produces:**

```python
Store.list_rubric_presets(query='', question_type='') -> list
Store.get_rubric_preset(preset_id) -> dict | None
Store.save_rubric_preset(value, *, preset_id=None, expected_version=0, expected_generation) -> dict
Store.delete_rubric_preset(preset_id, *, expected_version, expected_generation) -> None
# editable value={name, question_type, raw_json}; full return also id/version/timestamps.
```

- [ ] Step1 RED real Store: exact raw save/restart, normalized casefold name collisions, Unicode names, invalid rubric, stale version/delete/restore, backup compatibility. Example literal preservation:

```python
raw = '[{"id":"a","name":"甲","weight":33.333333333333333333,"extra":{"x":0.1234567890123456789}},{"id":"b","name":"乙","weight":66.666666666666666667}]'
row = store.save_rubric_preset({'name':'我的量表','question_type':'归纳概括','raw_json':raw},
    expected_generation=store.assessment_generation())
assert Store(store.path).get_rubric_preset(row['id'])['raw_json'] == raw
```

Run `pytest tests/test_rubric_library.py -q -p no:cacheprovider --basetemp=../process/t2-red` with requested Python/offscreen; capture correct missing-feature failure before production.

- [ ] Step2 add table `rubric_presets(id TEXT PRIMARY KEY,name_key TEXT NOT NULL UNIQUE,version INTEGER NOT NULL,data TEXT NOT NULL)` at Store initialization using existing connection (no nested lock during restore). JSON data has allowlisted name/question_type/raw_json/id/version/created_at/updated_at. Names1–60trimmed chars, casefold unique; known RUBRICS question types; raw string validated without serialization. Atomic UUID creation, versioned update/delete under state_write; missing updates never upsert deleted IDs. No user input SQL interpolation.
- [ ] Step3 extend optional restore columns and dedicated record validation: keys/index/data consistency, name-key uniqueness, positive int versions/timestamps/types and valid raw rubric. Absent table accepted and migrated empty; restore remains safety/rollback compatible. Invalid backups rejected before active data changes.
- [ ] Step4 RED real Qt, then manager `模板库…` at existing template area. Search/type filter; save current, load, copy, rename, explicit update-current-content, delete. Saving builtin stores snapshot but leaves editor empty/builtin. Loading copies exact raw after fresh row/version/epoch/editor checks; existing custom replacement default-No, cancel retains raw bytes; deletion does not clear loaded editor. Every write uses opening epoch, reject restore during confirmation or input collection. Error keeps typed name/raw/selection. Observe ordinary dirty/autosave state rather than bypassing it.

```python
# Explicit load only, after consent and current record checks:
host.rubric_edit.setPlainText(selected['raw_json'])
# Saving/renaming/filtering/deleting presets never calls this editor write.
```

The preset's question_type is library classification. Explicit load copies raw_json only; it must not silently change the host question type, exam, answer or other settings.

- [ ] Step5 GREEN library+Qt, visual_rubric/rubric_ui/rubric_templates, backup/restore and new routing tests (previous task must remain intact); tests use real stores/windows with unrelated backups disabled/drained. No extra source-text assertion tests. Capture normal/min screenshot, check all buttons fit and prior hints absent.
- [ ] Step6 self-review and process/task-2-report.md full API/commands/RED-GREEN/output/change list/concerns. Controller owns review/fullsuite/build; no helpers/Git.

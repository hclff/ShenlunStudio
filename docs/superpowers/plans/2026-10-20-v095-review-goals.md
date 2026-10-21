# 0.9.5 Review Goals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select existing evidence-bound learning-summary items into the linked question's existing review plan.
**Architecture:** Pure frozen-goal schema/basis + versioned plan extension and atomic append + small selection and plan-display UI. No new model reasoning or separate todo state.
**Tech Stack:** Existing Python/PySide6/SQLite.
**Spec:** `../specs/2026-10-20-v095-review-goals-design.md`, approved overview and coordination plan global constraints.

## Global Constraints

- 历史评分快照、原答、修改稿和人工评分不被重写；删除题目/报告后的失效关联不得静默重建。
- 新增分配、模板和计划操作不调用模型；现有 OCR/AI 转写流程及上传确认保留。不能借这些新增入口触发后台收费请求。
- No helper agents/Git/fullsuite/build/providers/user DB. Only release_095/apply_patch. Preserve existing status/date/tag semantics, caps/UI hints, earlier tasks' APIs and restore validators; read approved full subproject spec before implementing.

## Task3: Evidence snapshots in versioned review plans

**Files:** create `shenlun/review_goals.py`, `shenlun/ui/review_goals.py`, `tests/test_review_goals.py`, `tests/test_review_goals_ui.py`; modify `review_plans.py`, `ui/review_plans.py`, `ui/assessment.py` or review_learning mixin hook, `practice_store.py` validation only as needed. Do not alter learning_summary priority algorithm or original report snapshots.

**Consumes:** learning_summary(review), Store.question_for_review/get_review/get_question, normalize_review_plan/question_plan, state_write, existing restore generation and plan version.

Consume Task2's reviewed neutral modal-workspace guard and explicit reload contract from its report rather than duplicating timer/stale-context protection. Capture opening generation before report/question/editor/persisted-workspace context reads. Goal/plan dialogs must not let a restore or external workspace update resume a stale pending host autosave on cancel/error; retain buffers and use existing explicit reload. Preserve all reviewed routing/template behavior.

**Produces:**

```python
def review_goal_basis(review):
    return canonical_hash({key:review.get(key) for key in ('submission','assessment','prompt_version')})
def review_goal_candidates(review) -> list: ...
Store.add_review_goals(review_id, selected_goal_ids, plan_value, *, expected_version,
    expected_basis, expected_generation, expected_question_id) -> dict
# candidates have deterministic stable IDs derived from report ID+basis+summary item.
# existing save_review_plan keeps compatibility and adds optional expected_generation.
```

The required expected_question_id is the exact opening association captured after opening generation. In the same write transaction compare the real current report association to that ID and require the question to exist; matching question text/fingerprint alone cannot authorize a different ID. This closes the plan's initial omission of an opening-association argument while implementing the binding spec.

Ordinary plan edits may retain or explicitly remove existing canonical goal snapshots, not fabricate new source evidence or rewrite retained excerpts. Omission of goals by legacy callers preserves them; explicit goals=[] removes them. New goal creation goes through add_review_goals with selected candidate IDs, with server-derived evidence rather than caller-supplied goal bodies.

- [ ] Step1 RED real Store: add subset/duplicate/no candidates/deleted report or question/CAS/generation, manual statuses stay chosen, reports untouched. Construct review with a real valid material citation and a missing point or original annotation; save through Store.save_review and use literal expected title/action/quote, not the production summary to compute expected text. Capture RED before new feature.

```python
rid = store.save_review(fixture_review)
qid = store.question_for_review(rid)
original = store.get_review(rid)
candidates = review_goal_candidates(original)
saved = store.add_review_goals(rid, [candidates[0]['id']],
    {'status':'待复习','due_date':'','tags':['遗漏要点']}, expected_version=0,
    expected_basis=review_goal_basis(original), expected_generation=store.assessment_generation(),
    expected_question_id=qid)
assert saved['goals'][0]['title'] == '未覆盖：保留人工窗口'
assert store.get_review(rid) == original
```

- [ ] Step2 implement allowlisted goal snapshots: stable ID/report ID/basis/algorithm version/title/action/validated refs/annotation excerpt; bound fields using existing report/plan validation conventions, reject invalid shapes and duplicate IDs. Canonical basis excludes revisions/usage/timestamps. Reuse actual summary/citation checks, no invented reasons. Plan normalizer preserves optional goals and validates them; old missing goals behaves empty without unnecessarily changing legacy public return shape relied on by existing consumers.
- [ ] Step3 atomic add under state_write: load real current report, exact mapping to existing question, current plan and basis; rederive candidates server-side and accept only selected known IDs, dedupe against existing records. Check expected version/epoch and preserve existing unselected goals/metadata; increment plan version once. Empty/unknown selections reject, deleted NULL mapping never recreates question. Existing save-plan and mark-retry paths preserve goals. Plan UI writes capture epoch; report deletion doesn't block maintenance of already saved goal snapshots.
- [ ] Step4 RED Qt then `加入复习…` on actual saved linked report. User must check desired candidates, defaults not selected; show original title/action/refs, existing plan values, editable status/date/tags. Failure keeps buffer. Plan editor lists retained snapshots, explicit remove, opens local existing report only after current ID/basis checks; missing/changed source gets compact status, no upload or link execution. Existing solved status not reset automatically; no report-count/grade improvement inference.
- [ ] Step5 extend restore validation for goals, new/old backup roundtrip, tampered goal reject before restore, source-deleted/changed labels and reference checks. Ordinary plan edits/mutations cannot discard goals. Capture normal/min synthetic screenshots and test plaintext/HTML escaping against link-like/instruction-like payloads.
- [ ] Step6 GREEN review_goals/Qt + existing review_plans/review_learning/learning_ui/practice_deletion/practice_store and earlier new features as affected; no fullsuite/build. Write process/task-3-report.md with complete RED/GREEN commands/results/APIs/concerns after self-review. No helpers/Git.

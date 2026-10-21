# Shared implementation contracts

All modules import under package `shenlun`. Python 3.13. All public data is JSON-serializable dictionaries; timestamps ISO8601. Do not create incompatible dataclass wrappers.

## Article

`{id: str, title: str, journal: str, year: int, issue: int, author: str, url: str, publication: str, kind: str, status: 'available'|'unavailable', body: str, paragraphs: [{id: str, text: str}], fingerprint: str}`. IDs are stable from URL or body, paragraph IDs use `<article-id>:p1`. kind one of 评论/理论/导读/推荐/统计/人物/其他. Preserve missing entries with empty body. Additional fields allowed.

## Article list summaries (0.9.6 additive)

Migration step 1 adds the derived `articles.year`/`has_body` columns, backfills them once from the stored JSON and creates `article_year` plus the covering `article_summary_scan` index. `scripts/stage_release.py` runs the migration on the bundled seed (without a backup) so a new installation does not pay the upgrade while the first window opens.

`Store.list_article_summaries(query='',journal='',kind='',year='',limit=200,offset=0)` returns only `{id,title,journal,year,kind,status,publication,has_body}`; `data`, `body` and `paragraphs` are never read. `Store.count_articles(...)` applies the same filters. `list_articles` accepts the optional `year`/`offset` keywords and keeps its previous semantics. `Store.get_articles(ids)` and `Store.article_titles(ids)` resolve ids in chunks of 400 and preserve caller order, skipping unknown ids. `stats()` adds `text_bearing` (rows with a non-empty body).

List surfaces must read summaries and load a body only for the selected id. `ui/table_models.ArticleSummaryModel` with `ui/widgets.ModelTable` gives a virtualized, read-only table that reuses the `QTableWidget` styling. `MainWindow.start_job(..., domains=...)` declares what a job can change (`corpus`, `methods`, `history`, `usage`, `questions`, `calibration`); `finish_job` refreshes only those domains, while `domains=None` keeps refreshing everything.

## Schema migrations and paged lists (1.0 additive)

`PRAGMA user_version` carries the schema version (`shenlun.migrations.SCHEMA_VERSION`, currently 3). `ensure_schema(path, backup=True)` runs after the structural tables exist, writes `<db>-迁移前备份-v<from>-<timestamp>.db` when the database already holds user data, applies every pending step inside one `BEGIN IMMEDIATE` transaction, stamps the version and rolls back on any failure. A database stamped with a higher version raises `MigrationTooNewError` before any write; `open_store()` converts that into a startup message and exit code 2 instead of a traceback. Steps only add columns, indexes and backfilled values, so they stay idempotent; `migrate(conn)` may join an existing transaction.

Projected columns live in the same row as their authoritative JSON and are written by the same statement: `articles.year/has_body` and `question_bank.question_type/needs_retry/plan_status/plan_due`. `question_bank` rows are inserted through the explicit column list in `_save_question`; `_write_review_plan` updates all four projections together with `data`; backup restore accepts either the legacy shape (upgraded in place afterwards) or the legacy shape plus these trailing columns.

Paged list APIs keep the ordering of their unpaged counterparts: `list_reviews_page`, `count_reviews`, `usage_details`, `list_questions_page`, `count_questions`. `list_questions` delegates to `list_questions_page`. `list_review_plans` filters and sorts in SQL on the plan projections and batches attempt counts; `query` still matches title, source, question text and tags only.

## Store(path: Path)

`upsert_article(article:dict)->str`; `get_article(id:str)->dict|None`; `list_articles(query='',journal='',kind='',limit=200)->list[dict]` (metadata including body permitted, sorted deterministically); `stats()->dict` keys articles,available,unavailable,unique_bodies,analyses,cards,reviews.

`set_setting(key:str,value:Any)->None`; `get_setting(key:str,default=None)->Any` (JSON data, NEVER secrets).

`save_review(review:dict)->str` adds id/created_at; `get_review(id:str)->dict|None`; `list_reviews(limit=100)->list[dict]`.

`save_analysis(article_id:str,analysis:dict,version:str='1')->None`; `get_analysis(article_id:str,version:str='1')->dict|None`.

`save_card(card:dict)->str`; `list_cards(enabled_only=False)->list[dict]`; `set_card_enabled(id:str,enabled:bool)->None`.

`record_usage(usage:dict)->None`; `usage_summary()->dict` with requests,input_tokens,output_tokens,total_tokens.

Implement each operation with its own SQLite connection for Qt worker use. May add indexes/tables/helpers but must preserve this interface.

## Importers

`import_manifest(path:Path,store:Store,progress=None,cancel=None)->dict` counts processed,available,unavailable,duplicates (may add fields). progress(message:str), cancel is threading.Event or None. Handle existing source manifest `issues[*].articles`, source blocks type text/image. titles and metadata, source year/number fields available.

`read_text_file(path:Path)->str`: .txt/.md/.json UTF8 BOM and .pdf via pypdf; reject >25MB or scans with no extractable text.

`import_file(path,store,progress=None,cancel=None)->dict`: existing manifest JSON, generic article JSON/list or TXT/PDF.

`parse_official_article(html_text:str,url:str)->dict`: lxml huge_tree; qstheory.cn or people.com.cn official hosts only, body selectors per official templates.

`import_url(url:str,store,progress=None,cancel=None)->dict` fetches allowed official article, adds it.

`import_people_range(start_date:str,end_date:str,store,progress=None,cancel=None)->dict`: YYYY-MM-DD inclusive; navigate daily official electronic paper layout, select pages whose visible label contains 评论/理论; import each article once; checkpoint each successful date, retain failed date URLs to retry; bounded date window per invocation (UI can split ranges).

No shared global requests sessions/SQLite connections. Export useful parser helpers and error classes as needed. Network fetch timeout <=30s, total attempts <=2, official redirects must stay on allowed publisher.

## Submission / Rubric

`prepare_submission(raw:dict)->dict`: question/materials/answer strings, question_type (归纳概括/综合分析/提出对策/应用写作/文章写作), exam (国考/江苏省考/通用训练), year str, category str, max_score finite positive default100, word_limit int default0, reference str, rubric optional. canonical answer uses LF and trimmed outside whitespace only. materials_paragraphs use M1,M2 etc. answer_spans use Python Unicode codepoint offsets. mode full iff question and materials present, else diagnostic. counts nonwhitespace characters including punctuation.

`rubric_for(question_type)->list[{id:str,name:str,weight:float,description:str}]` sum100.

## AI assessment output

`{summary:str, task_analysis:str, dimensions:[{id:str,score:float (0..100),reason:str}], annotations:[{start:int,end:int,quote:str,issue:str,suggestion:str,material_refs:[{id:'M1',quote:str}],corpus_refs:[{id:'article-id:p1',quote:str}]}], material_points:[{point:str,material_refs:[...],coverage:'covered'|'partial'|'missing'|'uncertain',answer_quote:str}], strengths:[str], next_action:str, uncertainties:[str]}`.

Every annotation must bind nonempty exact answer[start:end]==quote. Every material/corpus ref resolves to the actual included paragraph and exact quote substring. Missing-point annotations need evidence. Full mode requires at least one material_points mapping with verifiable evidence (otherwise no successful score). Validate all score values finite; reject unknown/duplicate/missing dimension IDs in full mode. Locally calculate training total; ignore any model total. Diagnostic mode force total_score=None, no material_points or material_refs, no numeric dimensions. Validation output adds total_score,max_score,mode,rubric_version='training-1',word_count,warnings. Fail closed with ValueError on unverifiable citations.

## Settings and model

Config defaults: base_url '', model '', timeout 90, max_output_tokens 5000, session_token_budget 150000, max_input_chars 60000, temperature 0.2. URL configured by user; chat completions JSON protocol, no remote API calls before explicit action. Endpoint HTTPS except localhost/127.0.0.1/::1 for local services/tests; no userinfo/query/fragment; disallow redirects for authenticated calls. Key separately held in OS keyring/session memory. Empty key allowed for explicitly local service only.

`ModelClient(config:dict,key:str).complete(system:str,payload:dict,cancel=None)->dict`: returns {'data':dict,'usage':{'input_tokens':int,'output_tokens':int,'total_tokens':int},'model':str}; reject non-JSON, invalid schema content and limits; cancellation checked before/after network. Actual result validation belongs to domain/service. Do not expose HTTP error body/request headers in exception messages.

settings module: `load_config(store)->dict`, `save_config(store,config)->None`, `get_api_key()->str`, `set_api_key(key:str)->bool` true if OS vault, false session-only; never plaintext fallback.

## AppService(store,config,key)

`assess(raw_submission:dict,progress=None,cancel=None)->dict` validates input, retrieves <=8 corpus paragraphs plus enabled method guidance, real AI assessment, validates and saves initial review before revisions. Returned/saved review: `{id,created_at,title,submission,assessment,revisions:{minimal:str,model:str,status:str},sources:list[Article],model:str,config_snapshot:dict(no key),usage:dict}`. Generate revisions in a separate request; revision failure keeps initial assessment and revision status message. If canceled, do not persist post-cancel success; if original assessment was already durably saved, preserve it as original-only and mark interrupted revision.

`extract(article_ids:list[str],progress=None,cancel=None)->dict`: per-article paragraph-preserving batches <=16000 chars (oversize paragraph split tracked), real model structure analysis, validate evidence refs, store analysis and candidate method cards. Resume using model+prompt+content fingerprint version; never auto-enable candidate methods.

`estimate_extraction(article_ids)->dict` counts articles,characters,estimated_input_tokens; no network.

`test_connection(cancel=None)->dict`: user click issues one tiny JSON request; no automatic background call.

## Reports / UI

`review_html(review)->str`, `export_review(review,path)->None` for .html/.txt/.json; printable HTML for Qt PDF print; `calibration_metrics(pairs:list[dict])->dict` human,ai numeric plus optional question_id/version.

`MainWindow(store,service_factory=None)` testable; service_factory(config,key)->AppService. UI tests use local test service or local HTTP fixture, never fake scoring as product feature. ObjectNames for question_edit/materials_edit/answer_edit/assess_button/cancel_button/corpus_search/review_list/model_url/model_name/api_key. All long calls via workers, active QThreads retained until finished, close gracefully requests cancel.

Main entry CLI `python run_app.py [--data-dir PATH] [--smoke-test --screenshot PATH]`. Smoke uses temporary explicit data directories, local synthetic loopback API fixtures and no cloud calls, saves real widget screenshots, reports JSON state then exits. Packaged EXE uses the same flags. Default user data in %LOCALAPPDATA%/ShenlunStudio; bundle seed `assets/seed.db` copied only when no user DB exists; never overwrite history on upgrade.

Final-review additive fields: submission/assessment `rubric_fingerprint` is the first16 hex of SHA256 over canonical rubric JSON, preserving template rubric_version=training-1; reports/history show both. Review `method_snapshot` preserves applied validated method cards. Method `source_versions` maps article ID to body fingerprint; current references and at least two distinct bodies are revalidated before every assessment. Re-extraction replaces one article's outdated contribution while accumulating batches of its current version.

## Confirmed reference points and isolated evaluations (0.7 additive)

Store adds ReferenceStoreMixin and EvaluationStoreMixin with separate append-only reference_point_versions, eval_sets/cases/labels/runs/results. Reference confirmation requires actual material quotes and named reviewer; latest draft does not replace an active confirmation. Withdrawal is a new version. Optimistic expected_version prevents silent overwrite. Submission claims cannot supply confirmed points; AppService retrieves the actual matching Store version. Reviews freeze its snapshot and validate complete per-point coverage with exact answer quotes. Scoring conditions include its content hash/version when present; legacy reviews keep their prior condition shape.

AppService.prepare_grade builds the exact payload read-only; evaluate_submission performs one grade call and records usage, without revision or ordinary review writes. plan_evaluation pins actual payload, corpus sources, method snapshots, confirmed points, config and prompt/system hash. Batches are serial, atomically claimed once,1—5 repeats and<=100 cases. Cancellation rejects late success while retaining completed units. Unknown/missing human scores stay absent; valid zero is retained. Case origins remain separate, blind export omits AI/origin expectations, imports are atomic and fingerprint-bound. Only independent real/public candidate labels contribute to per-condition error metrics; different actual model conditions do not merge MAE. Restore validates immutable frozen provenance and public config; export recursively removes secret-key fields. Older backups remain supported.

`evaluation_release_probe.probe_evaluation` uses only explicit isolated synthetic databases and a loopback HTTP fixture, with no cloud calls or real credentials. It verifies draft/confirmation state, three calls using frozen points despite a newer active version, usage persistence, zero ordinary reviews/human pairs, and additive backup restoration. No synthetic data enters the seed.

## Practice workspace (0.4 additive)

Question identity is the canonical question/materials/question_type fingerprint; answers and metadata do not change identity. `save_question(submission,title='',tags=None,source='',needs_retry=None)->id`, `get_question(id)`, `list_questions(query='',status='',question_type='',limit=1000)`, `mark_question_retry(id,bool)` manage templates without original answers. Status is derived from linked reviews and explicit retry flag. `save_review` links reviews atomically; updating revisions on the same review ID never creates a new attempt or reverts explicit template metadata. Legacy reviews and the old draft setting migrate once, preserving their original JSON.

Workspace snapshot `{submission:{question,materials,answer,reference,exam,year,category,question_type,max_score,word_limit,rubric_text},question_id,updated_at}` preserves raw unfinished rubric JSON. `save_workspace(snapshot,archive_previous=False)`, `load_workspace()`, `load_question_draft(id)`, `list_workspace_history(limit=20)` use the same canonical identity; a changed question unlinks before per-question draft persistence. Switching or explicitly replacing archives the prior meaningful snapshot. UI debounces 900ms, flushes before replacement/navigation/close, and blocks these operations on save failure. Startup read failure pauses editing/autosave to prevent blank overwrite.

`list_question_attempts(id)` and `question_for_review(review_id)` expose durable linking. `practice.comparison(left,right)` requires the same question and returns escaped full answers/bounded diff plus actual scoring conditions. Delta requires valid full scores and known matching model/rubric/prompt/max score, word limit, model parameters and method snapshot; it is a training estimate, not proven learning gain.

`save_human_score(review_id,score,reason='',reviewer='',independent=False)` binds human score including zero to the exact answer, AI score and complete scoring conditions. `get_human_score` labels stale judgments; `human_calibration_pairs` excludes stale/invalid pairs. Independent is self-declared and defaults false. `practice_summary` counts valid observations and groups repeated scores only for the same answer and known scoring version. No synthetic pilot scores or labels enter the release seed.

`backup_to(path,overwrite=False)` uses SQLite online backup to a temporary file and atomic rename. `restore_from(path)->safety_backup_path` validates schema/column order/keys/JSON shapes before replacement, retains a pre-restore safety backup and rolls back on failure. Legacy 0.3 core databases may omit all practice tables. Existing physical aliases to the active DB are rejected. GUI restore requires idle workers, explicit confirmation, flushed/paused autosave and reloading all in-memory views.

## Exam/type training templates (0.5 additive)

`rubrics.PROFILE_CHOICES` has auto plus seven training frameworks. `resolve_template(question_type,exam='',category='',year='',selection='auto')` returns `{rubric,metadata}`. Metadata records id/name/version/kind/profile/selection/source_year/source_title/source_url/source_access/focus/notice/warnings/annual_verified. Public outlines inform ability emphasis; weights, descriptions and score bands are software training designs. National and Jiangsu2026 are the explicit reference baseline; unsupported/blank years carry a boundary warning, unknown exam/category auto-falls back to generic, and manual mismatch warns.

Validated input `rubric_selection` defaults auto and persists through workspace/question bank/structured intake. Nonempty actual custom `rubric` wins over the picker. `prepare_submission` returns the actual rubric and locally generated `rubric_template`. A twice-prepared builtin retains provenance only when its metadata exactly matches the resolver and actual rubric fingerprint matches. Edited/forged provenance becomes custom with no official source attribution. Assessment ignores model-supplied template claims and copies locally selected metadata. Reports use stored actual metadata/criteria rather than recalculating current defaults. New provenance participates in comparisons; legacy records lacking provenance retain their exact condition dictionary shape, preserving old human-score bindings.

## Image transcription (0.6 additive)

`InputPage.source_image` retains canonical full-resolution metadata-free bytes separately from thumbnail `preview_png`; `ocr_info` records actual operations, not character confidence. `read_inputs`/`read_image_bytes` add `ocr_options` and `retain_failed`; defaults preserve legacy standard one-pass/failure-drop behavior. UI uses enhanced and keeps failed image pages for rescue. Limits:25MB/24MP image,96MB aggregate retained source+preview bytes,12 segments/page. Local enhanced keeps glyph scale and partitions long images with overlapping geometry ownership; contrast/sharp/upscale retry only on empty text, optional one mild-angle retry, explicit0/90/180/270 clockwise rotation then normalized crop, optional two-column left-to-right reading. Neither readers nor image helpers invoke AI.

`ModelClient.complete_image(system,payload,image_bytes,cancel=None)` accepts one valid inlinePNG/JPEG<=4MB, request<=6MB; same hardened authenticated transport and no redirects, independent text character cap and conservative8192image-token reservation. `vision_ocr.prepare_image` strips metadata/white-flattens/appliesEXIF, longestedge2048,<=4MB, rejects aspect>10 withcropadvice. Pure `estimate_image` must not read credentials or invoke a server. `AppService.recognize_image` transcribes one explicitly approved current image; prompt forbids rewriting/solving/guessing and uses `[无法辨认]`. Return text,uncertainties,model,usage,warnings/info; record returned usage even if truncation/JSON/semantic validation fails; never create assessment/review or persist images/credentials. Cancel suppresses successful candidate; previously sent requests may still cost. UI requests default-No consent after pure estimate but before service/keyread, only sends visible selectedregion, then shows old/new candidate; only explicit adopt updates preview, bounded undo preserves prior manual edits. Outer cancel/failed retry never writes host editors.

## Public teaching guidance (0.8 additive)

Separate TeachingStoreMixin/teaching_method_versions stores append-only local draft/enabled/disabled versions. Eleven owners and twelve version-zero source drafts are pure catalog data; default seed has zero active teaching records. Save/import strip trust, edits require re-review, import conflicts clone new draft IDs, named local reviewer plus note and eligible exact source excerpt are required to enable. Expected version protects concurrent edits; canonical content hashes bind records. Full/index text can support enable; catalog/student-note alone cannot. Public source URL checks never fetch remote pages.

0.8.2 adds twelve independently rewritten GitHub community drafts (GH_*), keeping all twelve legacy drafts and eleven teacher/organization categories. Categories reflect repository-claimed perspective, not verified teacher authorship. Every community name, purpose, boundary and pinned source carries that distinction into editing, frozen payloads and reports. full_text denotes the repository document only. Remote skills/scripts are never installed or executed. Local saved versions take precedence over catalog defaults. The distinct-method cap becomes512, preserving the previous488 local slots. MIT author notice and source/license notes ship with the application; unlicensed external skill documents and unpublished course originals are not redistributed.

`resolve_teaching_guidance(store,submission,selection=None)` uses actual Store.get_setting and current eligible methods; raw submission guidance is ignored. Modes off/combined/source/compare; explicit owner coverage required, max six matching cards, compare covers 2–3 owners. `prepare_grade` embeds a full validated snapshot in the actual payload. Same existing grade call emits teaching_analysis with exact method coverage/status, exact M/answer quotations, consensus/differences. Same local rubric calculates one total; no teacher scores or averages. No guidance means model-supplied teaching claims are removed. Revisions remain normal second call and receive the same snapshot.

Review `teaching_snapshot` freezes source/method/version/local-review details. Reports escape text and link only safe source URLs; generated observations are not teacher-authored judgments. Scoring conditions include snapshot hash/mode/owners when present, leave legacy shape untouched. Evaluation plans freeze actual payload; success results must match that snapshot. Backup restore binds frozen teacher records to exact local (id,version) history and validates applications; older backups omitting the additive table remain supported when no frozen guidance exists.

UI manager shares existing methods workspace, preserves publication-card rules, local edits and import previews. Explicit local source intake never silently fetches a URL. Mode preflight precedes service/key access; busy jobs block method mutations. `teaching_release_probe` uses a separate synthetic database with custom fake source material, loopback HTTP only, and zero ordinary-review writes/cloud calls; it checks source/compare each use one grade call, frozen evaluation after disable, report provenance, usage and backup restoration. Synthetic fixtures never enter the distribution seed.

## Reversible method management (0.8.3 additive)

`method_library_trash(kind,method_id,deleted_at)` has a composite primary key and stores article/teaching deletion markers. Delete is atomic with article disable or a new disabled teacher version. Normal list/get/enable/edit exclude or reject deleted methods; built-in installation includes hidden IDs so startup cannot resurrect them or make repeat import backups. Re-extraction skips a deleted generated ID. Restoring removes the marker and keeps articles disabled/teachers in a new draft. Existing review snapshots, versions and frozen evaluations remain unchanged. Backup validates table keys/types, kinds, timestamps, record existence and disabled status; older backups may omit the table.

## Permanent method removal (0.8.4 additive)

`method_library_purged(kind,method_id,purged_at,version_hashes)` records a composite-key suppression marker, timezone timestamp and JSON version-to-SHA256 map (empty for articles). Only trash entries can be purged in a BEGIN IMMEDIATE transaction; expected_deleted_at rejects a stale dialog after restore/re-delete. Article rows and unreferenced teaching versions are physically removed. Reports, frozen plans and results retain exact enabled teaching versions. The marker prevents list/get/version/restore/edit/enable or built-in reinstallation from recovering a purged ID, including a stale writer's article row. Imports clone colliding teaching IDs. Markers are capped at 10000; permanent teaching IDs reserve historical identity capacity while freeing live slots.

The manifest captures every enabled version's complete canonical record hash before purge. A report or plan obtained before a concurrent purge may arrive afterwards: save_review, create_eval_run and save_eval_result validate and archive only matching original enabled versions in the same transaction as the historical write. Any changed snapshot is rejected and the transaction rolls back. These archive rows remain hidden from all method/recovery APIs. Backup validates the four-column schema, canonical version keys/digests, no trash overlap or article residue, all referenced versions present and every retained teaching row matching its manifest. Older backups without this table remain accepted; restoring a pre-removal backup restores its original state. The shared trash dialog confirms the named item with default No, checks busy before/after confirmation and refreshes the parent with the correct permanent-removal message.

## Batch method state changes (0.8.6 additive)

Both method libraries offer checkbox selection independent of the current single-item detail/editor selection. Select-all applies to the visible list; switching the teacher filter drops prior checks, refreshing the same list preserves remaining checked IDs. Batch confirmation defaults No and freezes selected record values before any modal review/confirmation. Busy/closing and unsaved teacher-editor guards are checked before and after dialogs. Batch results identify every target by ID/name and success/skipped/failed reason; successful items commit independently, so one ineligible item does not stop the others.

The local `set_methods_enabled` helper reuses AppService.enable_card for article eligibility and Store.set_teaching_method_enabled for append-only teaching audit versions. It compares the full displayed record with current state, then uses the existing transaction CAS (article expected_card, teaching expected_version). Changed/deleted/purged records fail, already-target states skip without new teaching versions, and teaching activation saves the explicitly entered shared reviewer/note in every newly enabled version. No models, credentials, uploads or new schema are involved. Existing snapshots and frozen plans are unchanged. The source/frozen real-widget probe covers cancellation, default No, filtered selection, per-item evidence failure, both batch state transitions, named audit and backup roundtrip.

`save_manual_article_method(value,identity=None,expected_card=None)` accepts bounded editable fields and exact local article quotes, computes actual source IDs/fingerprints/versions and always disables the result. Optional original-card equality detects stale UI edits. Empty evidence is a draft; existing two-distinct-body activation validation remains in AppService. UI provides local evidence selection, cancel-safe deletion and a recoverable deleted list. Busy checks run before and after modal actions. Active teacher capacity remains512; deleted IDs free live capacity, while the historical identity ceiling is4096. Restore rechecks capacity. Frozen EXE probe uses separate synthetic databases/widgets, real confirmation buttons and zero cloud calls.

`save_card` and `set_card_enabled` accept an optional expected_card sentinel; explicit None means expect no current record. Comparisons and writes share BEGIN IMMEDIATE, raising MethodConflictError without mutation. Activation passes the validated record. Extraction preserves local_manual cards and uses compare-and-swap for generated updates; automatic retirement of stale evidence also compares the original card. Concurrent edits/deletions take precedence over these automatic writes. Restore requires deleted article JSON id to match its SQL key.

## Method workflow (0.9.0 additive)

Both libraries use literal casefold token-AND query matching over method/source/teacher fields, with owner/type/enabled filters. Visible/total counts and selection IDs are computed from the visible list; successful filter changes clear checks, cancelled dirty-editor changes restore filter widgets and checks. Legacy single-item APIs remain intact.

Settings `method_presets` stores at most100 strict canonical records (ID/name/version/type/up to6 IDs per kind/timezone timestamps). Saves and deletes use BEGIN IMMEDIATE and version CAS; edits may retain unavailable IDs already present, but cannot introduce new missing/deleted IDs. `method_choice` is auto/off/preset and validated in backup restore. Deleting the active preset sets choice off, never auto. Preset resolution checks every chosen live, enabled, type-matching member and article evidence; no member is silently dropped or auto-enabled. Auto preserves legacy selection/retirement behavior. Off suppresses both method guidance types but does not alter retrieved corpus context. Presets store IDs; actual live reviewed versions are frozen at use.

Normal non-auto grading builds an offline context, confirms its exact named methods before credential construction, and sends that frozen context through assess. Choice metadata is stored outside the model payload; reports retain combination/version and actual method snapshots. Compact training controls keep 1050x720 rubric editors readable. Existing auto callers and old frozen contexts remain supported.

Optional table `method_comparisons(id,data)` links two ordinary frozen eval_runs created atomically. Planner enforces same case/order/content/provenance/reference/corpus/rubric/prompt/config, changing only method guidance and its output schema. Both arms' total reservation must fit the same service budget. Upload confirmation defaults No, followed by expected-plan-hash revalidation; no credentials read during estimate/cancel. A single cancellable worker reuses one service for both arms; completed/failed/cancelled states and partial results remain saved, and unstarted arms are cancelled when the job ends.

Paired reports require all planned repeats successful and matching realized model/conditions (excluding method guidance), normalize human error by each answer's maximum, and count each independent real/public-labelled answer once. GPT/control/institution labels do not count as human calibration. Single-call groups have no repeat spread; absent humans => no MAE/accuracy. JSON/HTML retain exact cases, conditions, individual results and human label versions. Restore validates every successful score finite and within frozen maximum, validates both linked plans and method membership, and requires a unique comparison group for every marked run. Missing comparison tables are accepted only for older unmarked runs. Invalid restores leave destination untouched.

The shipping workflow probe exercises real Qt filter/check/save/preview, default-No cancellation with zero requests, then eight actual loopback HTTP grading calls over two synthetic answers and two repeats per arm, export and backup restore. It declares zero cloud calls and explicitly does not validate human scoring accuracy.

## Teaching subject management (0.9.2 additive)

Teaching subjects are merged from the built-in catalog and a local `teaching_owners` registry. Custom IDs are stable and validated separately from live registration. Creating, enabling, and importing methods requires a live registered owner. Deleting a subject archives all live methods in one transaction, checks the confirmed method versions, and clears an explicit selection that references the subject. Recovering a method recovers its subject and keeps the method disabled.

New custom-subject guidance snapshots freeze the subject names under the content hash. Existing snapshots remain compatible. Backups retain registry tombstones, methods, reports and frozen evaluations; validation rejects dangling custom subjects and inconsistent live method relationships. Legacy backups without the registry remain supported.

## Training record and question deletion (0.9.1 additive)

Both practice lists offer checkboxes independent of the current detail selection, delete-current and delete-checked actions. Select-all includes only visible rows; changing question filters clears checks and an unchanged refresh retains surviving checks. Confirmation defaults No and identifies count, names and dependent data. Jobs/closing are checked before and after the modal. The UI flushes editor changes before preparing the confirmation; deletion never consumes unsaved answer text.

`prepare_practice_deletion(kind,ids)` captures selected records and relevant dependencies and produces an opaque token. `delete_practice_items(kind,ids,expected_token=...)` rechecks inside BEGIN IMMEDIATE and commits the complete selection atomically. Stale/missing targets or dependencies abort without partially deleting other items. Both single and batch actions use the same path.

Deleting reviews removes their review-question links and bound human observations, keeps questions and rejects later saves using those removed review IDs. Deleting questions removes question rows, per-question drafts and reference-point versions, keeps independent reviews/human observations/evaluation data, and detaches workspace/history question IDs while retaining text. Detached review-question rows remain with NULL question_id so startup migration does not recreate removed questions. Updating a retained detached report keeps it detached. An explicit future save of the question or a new training submission may establish a new question record; historical snapshots remain self-contained.

Minimal removed-review identity markers prevent late writes from resurrecting deleted reports. These markers and detached links survive backup/restore; older backups without markers remain supported. Restoring a backup from before deletion returns to that backup's original state. Human-score binding checks and writes share a write transaction, preventing an orphan observation after concurrent record deletion.

The shipping deletion probe uses separate synthetic SQLite databases and actual Qt controls at the minimum window size. It verifies default-No cancellation, single/bulk operations in both pages, filtered selection, stale confirmation rejection, history and editor preservation, restart suppression and backup roundtrip. It runs from source and from the frozen EXE with zero network/model calls and never deletes actual user records.

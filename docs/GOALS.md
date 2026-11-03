# Project goals

Kept by the lead. Goals and their "Done when" lines change only with the owner's yes.
Keep this file short: move finished goals to Archive.

Status: proposed | active | parked | done | dropped
Round line: `R-<n> <date>: <what this round had to achieve> -> PASS|FAIL, <n> blockers, <commit>, <tokens> agy tokens`

## Active

### G-16: S2-T3 hybrid retrieval and reranker (active)
Plan: docs/IMPLEMENTATION_PLAN.md, S2-T3. Depends on G-9, G-12 and G-15.
Owner decisions: the lead writes the code, with no agy. D1: `bge-reranker-v2-m3` fp16 on the GPU, in process through `sentence-transformers` on a CUDA 12.8 PyTorch build, loaded on first use. D2: BGE-M3 query encoding on the CPU through Ollama (`num_gpu: 0`), the same weights as S4. D3: only `factual_qa` gets hybrid chunks, in a new `EvidencePack.chunks` list carrying the rerank score; deep_dive and compare stay as they are; the gate threshold, synthesize and verify stay in S2-T4. D4: the hybrid query lives in `qdrant.py` (dense 40 + sparse 40, RRF, bound filter in every leg), `retriever.py` composes it, `retrieve.py` calls it. D5: an encoder or reranker failure is logged in `errors` and gives no chunks, so the gate refuses. The 15-question recall eval is out of scope. Two rounds: setup and parts, then wiring and live check.
Done when:
- Hybrid search returns the top 30, reranked to the top 8, all with `chapter_id <= chapter_max` and none from another novel
- A unit test shows the bound filter is in every prefetch leg
- The live check reports encode, fuse and rerank time, and VRAM with Qwen loaded
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-03: deliver the reranker, CPU query encoding and hybrid query -> PASS, 0 blockers, 77af56d, 0 agy tokens
- R-2 2026-11-03: wire the hybrid retriever into factual_qa and run the live check -> PASS, 0 blockers, 89eba0a, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-11-03: owner approved the goal and the decisions above
- 2026-11-03: live check on `xn_chunks` (pntt1, chapters 1-100, 704 points), with `qwen38-udiq4-hui-10k` resident at 13,297 MiB. Five questions, four at `chapter_max` 100 and one at 10: each fused 30 and returned 8, max chapter 75/100/71/98 and 6 for the N=10 question, and another novel returned 0. Warm steps: encode 26-66 ms on the CPU (Ollama `size_vram` 0), fuse 6-9 ms, rerank 110-116 ms, total 147-176 ms. The first query took 14.0 s, almost all reranker load. VRAM with Qwen and the reranker: 15,699 of 16,303 MiB (torch 1,092 MiB allocated, 1,346 MiB reserved), above the blueprint's ~13 GB estimate, with ~0.6 GB headroom

### G-11: clear the non-blocking Sprint 1 recheck items (active)
Plan: docs/S1_T9_EVAL.md, "Sprint 1 recheck". Follows G-10, whose recall line stays open there. Cut from `cld/s1-t9-feature-a3a0ec`.
Owner decisions: the lead writes the code, with no agy, because it is two small changes that must be run to be proven. `total_chapters` becomes 2468, the chapters on disk. `pntt1.yaml` gains `raw_dir: data/novel/pham_nhan_tu_tien_1`, and `xn run` falls back to `data/novels/<id>/raw` only without it (this changes the G-6 decision on the raw path; the folder stays where it is). The checkpoint serializer gets an explicit allowlist covering every `xn.schemas.state` model stored in a checkpoint, which includes `EvidenceItem` if it logs too. `novels` rows in existing databases keep their old value: there is no `data/xn.db`, and S0 only writes a missing row. Blueprint history (old benchmark plans) stays as written. The extraction prompt, the gold set, the extractor model and the other recheck items are out of scope. One round.
Done when:
- `configs/novels/pntt1.yaml`, the blueprint's chapter-count lines and `--to` examples in the blueprint and `docs/IMPLEMENTATION_PLAN.md` say 2468, and a bare `xn run --novel pntt1 --stage s0` finds the real folder without the missing-chapter abort
- A real follow-up logs no unregistered type, and `test_checkpoint_round_trip_gives_no_serializer_warning` checks logging as well as warnings, resets langgraph's warn-once record, and fails when the allowlist is removed
- `test_criterion_8_module_execution_and_pyproject_scripts` passes `encoding="utf-8"`, sets UTF-8 for the child, and passes under a cp1252 reader
- `s0_parse.py` raises no SyntaxWarning, `README.md` no longer names Redis, and blueprint §3.9 shows the Modelfile of `qwen3.8:udiq4_hui`
- G-9's Changes record that `cloud_spend_since` is a third exempt repository method and that `src/storage/qdrant_client.py` also keeps `src/config.py`
- `py -m compileall -q src` and `uv run pytest tests/unit` pass under both Bash and PowerShell
Rounds:
- R-1 2026-11-01: clear the five recheck items -> PASS, 0 blockers, 06bf95c, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-11-01: owner approved the goal and the decisions above

## Parked

## Archive

### G-1: S1-T1 project setup and config split (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T1.
Owner decisions: OQ-01 `src/xn/` package layout; OQ-02 `uv` with `pyproject.toml`, `uv.lock` and an exported `requirements.txt`; OQ-08 Python 3.12 (other OQ-08 values stay null). In `pntt1.yaml` the realm ladder, seed aliases and cleaning rules stay empty.
Done when:
- `configs/genres/xianxia.yaml` and `configs/novels/pntt1.yaml` exist, and config is split into global, genre and novel levels
- The Redis service is removed from `docker-compose.yml`
- Project layout and dependency files follow the owner's answers to OQ-01, OQ-02 and OQ-08
- A unit test shows the three config levels load and merge
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-10-31: deliver S1-T1 setup and the config split -> PASS, 0 blockers, 6aa3858, 258829 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-10-31: owner approved the goal and the decisions above; added the config unit test line and the pytest check

### G-2: S1-T2 SQLite schema and repository layer (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T2. Depends on G-1.
Owner decisions: OQ-05 deferred to S3-T1 (`jobs` built as in the blueprint, chapter-level stages only for now); OQ-06 deferred (this task needs none of those types); `feedback` gains `novel_id`; tests run with `uv run pytest tests/unit`.
Done when:
- All 15 tables in the plan exist with `novel_id` scoping, including an added `novel_id` on `feedback`, with WAL mode and foreign keys on
- Story-content reads require `novel_id` and `chapter_max` and reject `None`; writes and operational methods require `novel_id`, and only the novel list and novel-free `feedback` writes are exempt; `--full` passes `total_chapters`, so a bound is never null
- A unit test checks every public repository method against that rule and a short exceptions list
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-10-31: deliver S1-T2 schema and guarded repository -> PASS, 0 blockers, 3cbd361, 270525 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-10-31: owner approved the goal and the decisions above; reworded the table, bound and test lines

### G-3: S1-T3 pipeline stage S0, parse and clean (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T3. Depends on G-1 and G-2.
Owner decisions: OQ-15 S0 creates the `novels` row from `pntt1.yaml` if it is missing; S0 takes an upper chapter limit, default `total_chapters`; YAML front matter is stripped before hashing (blueprint deviation); BP-OQ-2 cleaning patterns stay empty, and a line counts as a watermark only above 5% of chapters and in at least 3; OQ-12 NFC only, through one shared function; OQ-13 non-matching files are skipped with a warning; `token_count` is characters / 1.6, to be calibrated in S1-T7. Also fixes the `upsert_job` nit on `attempts` and `updated_at`.
Done when:
- `src/xn/pipeline/s0_parse.py` sorts chapters by integer and aborts with a list of any missing or duplicate numbers
- It converts Markdown to plain text, normalizes it and applies the cleaning rules
- It stores `clean_text`, `sha256` and `token_count`, and records the stage in `jobs`
- Running it twice gives identical rows
- `tests/unit/test_s0_parse.py` covers all of this using made-up fixture chapters
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-10-31: deliver S0 parse and clean with its tests -> PASS, 0 blockers, 5b558c8, 264233 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-10-31: owner approved the goal and the decisions above

### G-4: S1-T4 pipeline stage S1, extract (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T4. Depends on G-3.
Owner decisions: BP-OQ-1 the Modelfile targets `qwen2.5:14b`, the current config value, and the live run is left to S1-T7; `src/schemas/extraction.py` and `src/ingestion/pydantic_extractor.py` stay until S1-T8 replaces their importers (plan deviation). Two rounds: schema, prompts, `llm.py` and Modelfile first, then the extractor.
Done when:
- The extraction schema, `prompts.py`, `llm.py`, the prompt and the Modelfile exist
- Chapters over 7,000 tokens split into 2 parts with a 300-token overlap, and the results merge
- Events whose evidence scores below 85 are dropped and logged, and `chapter_id` always comes from S0
- Each LLM call writes one `llm_runs` row, and extractions and `jobs` are saved
- Unit tests use a fake model
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-10-31: deliver schema, prompts, llm.py and Modelfile -> FAIL, 3 blockers, 130ef5a, 328568 agy tokens
- R-2 2026-10-31: clear the 3 llm.py blockers -> FAIL, 1 blocker, ae6bd6e, 102447 agy tokens
- R-3 2026-10-31: fix the test_llm.py import and prove the R-2 fixes -> PASS, 0 blockers, 87da24b, 22777 agy tokens
- R-4 2026-10-31: deliver the S1 extractor -> FAIL, 3 blockers, 468ff13, 357240 agy tokens
- R-5 2026-10-31: clear the 3 extractor blockers -> FAIL, 2 blockers, 3591690, 71464 agy tokens
- R-6 2026-10-31: fix the fake model routing in the extractor tests -> FAIL, 1 blocker, 96e7dd1, 54481 agy tokens
- R-7 2026-10-31: fix the failing split overlap test -> PASS, 0 blockers, b62ff80, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-10-31: owner approved the goal and the decisions above
- 2026-10-31: owner approved one extra round, R-7, past the revision limit
- 2026-10-31: full verifier pass at d39c569 -> PASS; owner approved marking the goal done

### G-5: S1-T5 pipeline stage S2, entity resolution (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T5. Depends on G-2 and G-4.
Owner decisions: a fuzzy score of 92 or above merges without the model only if both names have the same set of tokens, otherwise it goes to adjudication (plan deviation, because `token_set` scores names like Trưởng Lão / Mặc Trưởng Lão at 100). Every match step stays within one category. Links come only from `REVEALED_IDENTITY` events with `same_as`. A seed alias row is written only when the text first uses that name. `canonical_name` is the first spelling seen in the text. `holder` resolves as a character. Chapters run in strict order, S2 stops at a gap or model failure, and a changed finished chapter needs `rebuild=True`. No new table: S3 finds `entity_id` by `(alias_norm, category)`. Three rounds of code: foundations, resolver, driver.
Done when:
- `src/xn/pipeline/s2_resolve.py` and `prompts/s2_adjudicate/v1.md` exist, and resolution runs within one `novel_id`
- `normalize_alias` in `src/xn/text.py` builds on `normalize_text`, and S2 uses it for every alias
- The match order is exact, diacritics-stripped, fuzzy, adjudication, new entity, with boundary tests at scores 92 and 80 and the agreed auto-merge rule
- Ties never auto-merge, and a mention triggers at most 3 adjudication calls, each writing one `llm_runs` row
- `entities`, `entity_aliases` and `entity_links` are populated, with `first_chapter` on entities and aliases
- A reveal creates a link with `revealed_chapter` and never an alias, and tests show neither side gains the other's name
- A test shows the adjudicator prompt contains no text from a chapter after the mention's chapter
- A seed name not yet used in the text has no alias row
- Running twice changes no rows and makes no model calls, and a failed chapter leaves no partial rows
- Each event's name, `same_as` and `holder` maps to exactly one entity by `(alias_norm, category)`
- Unit tests use a fake model
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-10-31: deliver the S2 foundations -> PASS, 0 blockers, 9211129, 273754 agy tokens
- R-2 2026-10-31: deliver the in-memory resolver and adjudicator -> FAIL, 1 blocker, 2b2ce64, 311927 agy tokens
- R-3 2026-10-31: clear the adjudicator output blocker -> PASS, 0 blockers, e362125, 31515 agy tokens
- R-4 2026-10-31: deliver the run_s2 driver -> FAIL, 3 blockers, 92c7556, 323997 agy tokens
- R-5 2026-10-31: clear the 3 run_s2 test blockers -> FAIL, 1 blocker, 7b84a73, 152986 agy tokens
- R-6 2026-10-31: restore full job row comparisons in the snapshot helper -> PASS, 0 blockers, 795afb7, 106777 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-10-31: owner approved the goal and the decisions above, choosing the same-tokens guard
- 2026-10-31: verifier pass on the merged branch at 7a92bee -> PASS, every "Done when" line met
- 2026-10-31: PR #8 merged; owner approved marking the goal done

### G-6: S1-T6 pipeline stage S3, ledger write, and `xn run` (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T6. Depends on G-2 and G-5.
Owner decisions: event `id` is `chapter_id * 1000 + position`, so ids repeat exactly across re-runs. `ordinal` counts from 0 within `(chapter, entity, event_type)` in extraction order. `REVEALED_IDENTITY` writes one row, on the `name` entity. A name that normalizes to empty is skipped and logged, matching S2. `realm_rank` is the position in `realm_ladder`, otherwise NULL. The S3 input hash is the extraction payload plus the S2 job's hash, and S3 stops at the first chapter where S2 is not done. `--stale-only` works on S1 and S3 only and redoes chapters with a job row that failed or whose hash or prompt changed, skipping chapters never run. S0 and S2 reject `--stale-only` and `--from` above 1. `--rebuild` (S2 only, plan addition) clears the ledger, then redoes S2. The database is `data/xn.db` (`--db` overrides) and raw text is `data/novels/<id>/raw` (`--raw-dir` overrides). `src/schemas/lifecycle.py` stays until S1-T8 removes `src/cli.py` (plan deviation); `src/storage/sqlite_ledger.py` goes with it. `--all` is out of scope. Two rounds of code: ledger, then runner and CLI.
Done when:
- `entity_events` is written with the unique key `(novel_id, chapter_id, entity_id, event_type, ordinal)`, using an explicit, repeatable `id`
- `event_type` and `holder_entity_id` are correct
- Each of an event's names maps to exactly one entity; otherwise the chapter fails, leaves no partial rows and is recorded as `failed` in `jobs`
- Running twice changes no rows, and a changed S1 or S2 input rewrites that chapter only
- `xn run --novel --stage s0..s3 --from --to [--stale-only]` works, also as `python -m xn`
- An S0-to-S3 run through the CLI with a fake model, repeated twice, gives identical row counts
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-10-31: deliver the S3 ledger write -> PASS, 0 blockers, a7e6d1f, 459487 agy tokens
- R-2 2026-10-31: deliver the runner and xn run CLI -> FAIL, 1 blocker, 6c89f43, 412385 agy tokens
- R-3 2026-10-31: clear the raw-dir default blocker -> PASS, 0 blockers, 9821437, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-10-31: owner approved the goal and the decisions above
- 2026-10-31: verifier pass on the merged branch at fae527d -> PASS, every "Done when" line met
- 2026-11-01: PR #10 merged; owner approved marking the goal done

### G-7: S1-T7 extractor benchmark (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T7. Depends on G-4. Serves BP-OQ-1.
Owner decisions: the lead writes the code for this goal, with no agy. The models are `qwen3.5:Q8` and `deepseek-r1:14b`, as in the S1-T7 title; BP-OQ-1 and the blueprint name models that are not installed. Recall is scored against the pooled evidence-backed entities of both models, which compares them but does not satisfy Sprint 1's recall >= 0.80 (S1-T9 keeps that). 50 chapters evenly spaced across 1-1121, because chapter 1122 is missing and S0 stops at a gap. Winner: at least 95% schema success, then higher recall, and within 2 points the faster model wins; if neither reaches 95%, stop and report. `qwen3.5:Q8` runs with thinking off, `deepseek-r1:14b` as it behaves, both one chapter at a time. Setting the winner in config and its Modelfile goes beyond S1-T7's listed files (plan deviation). The chapter gap and `total_chapters` are out of scope. Three rounds: harness, calibration, full run.
Done when:
- Both models run the same 50 chapters through the real S1 code
- A report gives schema success, recall against the shared reference, s/chapter and peak VRAM for each model
- The winner follows the agreed rule and is set in config and its Modelfile
- `estimate_tokens` is calibrated from measured data
- Measured s/chapter is in the blueprint §3.9 cost table
- Unit tests use a fake model
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-10-31: deliver the benchmark harness, Modelfiles and output modes -> PASS, 0 blockers, 6691bc8, 0 agy tokens
- R-2 2026-10-31: calibrate estimate_tokens and clean the VRAM baseline -> PASS, 0 blockers, 3f9a36a, 0 agy tokens
- R-3 2026-10-31: run 50 chapters, record the results and set the winner -> PASS, 0 blockers, 7218978, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-10-31: owner approved the goal and the decisions above
- 2026-10-31: verifier pass on the integrated branch at b2aaead -> PASS, every "Done when" line met
- 2026-11-01: PR #11 merged; owner approved marking the goal done. G-8 later replaced `deepseek-r1:14b` in config and the blueprint

### G-8: compare `qwen3.8:UD_Q3` with `deepseek-r1:14b` (done)
Plan: follows G-7 and its `docs/S1_T7_BENCHMARK.md`. Depends on G-7.
Owner decisions: the lead writes the code, with no agy. Only `qwen3.8:UD_Q3` joins; `qwen3.8:IQ3` is dropped. It runs at `num_ctx 10240` with thinking off; `deepseek-r1:14b` keeps its 12288 results from G-7. Same 50 chapters, rescored against a pool of these two models. The agreed rule gives a recommendation and the owner makes the final pick. Config and blueprint change only for the owner's pick.
Done when:
- `qwen3.8:UD_Q3` runs the G-7 chapters through the real S1 code
- `xn.bench` can select which models run
- The report compares both models on one shared reference, and states the context difference
- A recommendation is given and the owner has picked a model
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-01: add `qwen3.8:UD_Q3`, its Modelfile and model selection -> PASS, 0 blockers, 7511ab0, 0 agy tokens
- R-2 2026-11-01: run 50 chapters, compare on one reference and recommend -> PASS, 0 blockers, 287dbbb, 0 agy tokens
- R-3 2026-11-01: compare `qwen3.8:udiq4_hui` with `qwen3.8:UD_Q3` and recommend -> PASS, 0 blockers, ff9cef5, 0 agy tokens
- R-4 2026-11-01: set the owner's pick in config and the blueprint -> PASS, 0 blockers, 4f082e8, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-11-01: owner approved the goal and the decisions above
- 2026-11-01: owner added `qwen3.8:udiq4_hui` against `qwen3.8:UD_Q3`, same chapters at `num_ctx 10240`, on a pool of those two; the pick stays open until this is compared
- 2026-11-01: owner picked `qwen3.8:udiq4_hui` as the extractor (plan change: set in config with `think: false`), so the last owner decision now reads "only for the owner's pick"
- 2026-11-01: PR #12 merged into the PR #11 branch instead of `main`, so a further PR carries the work to `main`; owner approved marking the goal done

### G-9: S1-T8 grounded query graph, ledger slice (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T8. Depends on G-2 and G-6.
Owner decisions: the lead writes the code for this goal, with no agy, on one feature branch, running `py -m compileall -q src` and `uv run pytest tests/unit` before each verifier pass. OQ-08: `daily_spend_cap_usd` is 1.00 and an unset cap refuses cloud calls; `fallback_model` stays unset, and the call then stops with an upstream error after the retries. OQ-07: `deep_dive` and `compare` need at least 1 resolved entity and 3 events; `timeline` and `factual_qa` need at least 1 resolved entity and 1 event. Cloud evidence is event type, chapter, description and realm only, at most 25 events per entity within the 20k-token budget, and quotes stay local. A different `--until` or a chapter number in a follow-up is refused with "start a new thread". Display name is `canonical_name`. Out of scope: the L5 scan, `xn progress`, token streaming, long-context mode and a live run. `src/schemas/extraction.py` and `src/config.py` stay until S3-T1 replaces `src/timeline/arc_detector.py` (plan deviation). `langgraph-checkpoint-sqlite` is added for `SqliteSaver`. Four rounds: foundations, first five nodes, last two nodes with graph and CLI, prototype removal.
Done when:
- The LangGraph flow runs route, bound, resolve_entities, retrieve, gate, synthesize, verify, and reads only from the ledger
- `route` falls back to `factual_qa` with no mentions when the model fails
- `bound` follows the §3.3 formula, and every answer prints "Phạm vi: chương 1–N"
- Entity resolution uses only aliases and links visible at N, and an unresolved name gets the top 3 fuzzy suggestions
- `gate` refuses with "Không đủ dữ liệu đến chương N" and makes no cloud call
- `verify` strips any citation not in the evidence or above N, and a test shows a deep-dive on 3 entities cites only chapters <= N
- The OpenRouter wrapper enforces `compact` egress in code, writes `egress_class` and `payload_chapters` to `llm_runs`, uses a 90 s timeout and 3 retries with backoff on 429/5xx then the fallback model, and checks the daily cap against `llm_runs.cost_usd`
- `SqliteSaver` checkpoints to `data/graph_checkpoints.db`, and a follow-up inherits `novel_id` and `chapter_max` from the thread
- `xn ask` works, also as `python -m xn`
- `src/agents/graph_workflow.py`, `src/agents/pydantic_agents.py`, `src/cli.py`, `src/schemas/state.py`, `src/schemas/lifecycle.py`, `src/storage/sqlite_ledger.py` and `src/ingestion/pydantic_extractor.py` are deleted
- Unit tests use fake models
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-01: deliver the state schemas, spend reads and OpenRouter wrapper -> PASS, 0 blockers, e2c1497, 0 agy tokens
- R-2 2026-11-01: deliver the route, bound, resolve, retrieve and gate nodes -> PASS, 0 blockers, be5bb30, 0 agy tokens
- R-3 2026-11-01: deliver synthesize, verify, the compiled graph and xn ask -> PASS, 0 blockers, 7e4e597, 0 agy tokens
- R-4 2026-11-01: remove the prototype files and pass the whole branch -> PASS, 0 blockers, 34ae093, 0 agy tokens
- R-5 2026-11-01: widen the chapter-bound parsing in bound -> PASS, 0 blockers, b8e528d, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-11-01: owner approved the goal and the decisions above
- 2026-11-01: verifier pass on the whole branch at 34ae093 -> PASS, every "Done when" line met
- 2026-11-01: owner approved R-5, widening the chapter-bound parsing in `bound` before merge
- 2026-11-01: PR #14 merged; owner approved marking the goal done
- 2026-11-01: recheck found `cloud_spend_since` is a third exempt repository method, beside the two in G-2, because cloud spend spans novels
- 2026-11-01: recheck found `src/storage/qdrant_client.py` also keeps `src/config.py` alive, so removing `src/timeline/arc_detector.py` alone won't free it

### G-10: S1-T9 gold set and eval harness, then Sprint 1 recheck (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T9. Depends on G-4, G-5, G-6 and G-9.
Owner decisions: the lead writes the code for this goal, with no agy, on one feature branch, running `py -m compileall -q src` and `uv run pytest tests/unit` before each verifier pass. The lead drafts the gold from chapter text without reading model output, labelled `annotator: claude`, and commits it before any scoring (plan deviation: §6 names a person as annotator). OQ-09: Sprint 1 sizes and thresholds here; 40/150/40/30 stays in S4-T2. Gold chapters are 20 evenly spaced across 1-100. OQ-14: `eval/<novel>/gold/` holds `chapters.jsonl`, `aliases.jsonl`, `qa.jsonl` and `spoilers.jsonl`; `baseline.json` stays in S4-T2. A name counts as found on a normalized match, whatever its category; category accuracy is reported apart. Q&A and spoiler probes are scored offline on the evidence pack with the local router. If recall is below 0.80 the lead stops and reports, without changing the gold or the prompt. One live deep-dive on 3 entities through OpenRouter is approved, at about 3 calls under the $1 cap, run from the main checkout so the app reads `.env` itself. Fixes found in the recheck are new work. Five rounds: harness, chapter gold, other gold, real run, recheck.
Done when:
- `eval/pntt1/gold/` holds 20 chapters, 80 alias pairs, 15 Q&A and 10 spoiler probes, and every gold name is checked against the chapter text
- `pytest tests/eval -m gold --novel pntt1` works, is skipped in a normal test run, and its scoring has unit tests
- A real S0-to-S3 run on chapters 1-100, repeated, gives identical row counts
- The report gives entity recall and precision, event-type accuracy, flag F1, alias F1, Q&A citation hit rate and spoiler leaks, and recall is at least 0.80
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-01: deliver the gold loader, scoring and eval harness -> PASS, 0 blockers, d73284a, 0 agy tokens
- R-2 2026-11-01: annotate 20 chapters from the text -> FAIL, 1 blocker, 4da44e9, 0 agy tokens
- R-3 2026-11-01: fix the chapter 53 conflict flag -> PASS, 0 blockers, 62e21e2, 0 agy tokens
- R-4 2026-11-01: deliver the alias pairs, Q&A and spoiler probes -> PASS, 0 blockers, 49e05fb, 0 agy tokens
- R-5 2026-11-01: run the real ingest, score it and report the numbers -> FAIL, 1 blocker (recall 0.68 against 0.80), b7eb8b5, 0 agy tokens
- R-6 2026-11-01: recheck all of Sprint 1 against today's code -> FAIL, 1 blocker (the same recall line), c53d1e9, 0 agy tokens
Sub-goals:
- G-10.1: raise extraction recall to 0.80 (done)
  Owner decisions: the lead writes the code, with no agy, running the same two checks before each verifier pass. `eval/` does not change, and the extractor stays `qwen3.8:udiq4_hui`. Every second gold chapter is held out, not tuned on, and scored only in the last round. At most 3 prompt attempts; if none reaches 0.80, stop and report what each reached. `EVIDENCE_MIN_SCORE` stays 85; if drops are the main cause, ask the owner. Tuning runs on copies, so `data/eval/s1t9.db` stays as it is. No cloud calls. Three rounds: measure drops, prompt v2, full run and score.
  Done when:
  - Events dropped by S1 are measured on the 20 gold chapters, by reason
  - `prompts/s1_extract/v2.md` exists and is pinned in config, with `v1` kept
  - Recall is at least 0.80 across all 20 gold chapters, with the 10 held-out chapters reported apart
  - Precision is at least 0.80
  - `eval/` is unchanged and the extractor is still `qwen3.8:udiq4_hui`
  - A real S0-to-S3 run on chapters 1-100, repeated, gives identical row counts
  - `py -m compileall -q src` and `uv run pytest tests/unit` pass
  Rounds:
  - R-1 2026-11-01: measure the events S1 drops, by reason -> PASS, 0 blockers, 8b50481, 0 agy tokens
  - R-2 2026-11-01: deliver prompt v2 tuned on the dev chapters -> PASS, 0 blockers, 4772e13, 0 agy tokens
  - R-3 2026-11-01: run prompt v2 on chapters 1-100 twice and score the held-out chapters -> PASS, 0 blockers, 71200c8, 0 agy tokens
Changes:
- 2026-11-01: owner approved the goal and the decisions above, including the live check
- 2026-11-01: owner added sub-goal G-10.1 and approved its criteria after recall came in at 0.68
- 2026-11-01: owner approved marking G-10 and G-10.1 done, after recall reached 0.98 on prompt v2 and G-10.1 R-3 passed

### G-12: S2-T1 Qdrant multi-tenant setup and manager (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S2-T1. Depends on G-2.
Owner decisions: dense vectors are BGE-M3 through Ollama (1024 cosine), kept over `embeddinggemma-2:570m` by the benchmark rule of 0.05 hybrid recall@8 (0.90 against 0.867 on chapters 1-100). Ollama returns dense only, so sparse vectors are BM25 built in our code and stored in Qdrant with the IDF modifier (blueprint change to ADR-03). The project's compose Qdrant is used, port 6233 bound to `127.0.0.1`, not `platform-qdrant`. The lead writes the code, with no agy, after agy was blocked twice, running the same two checks and the integration test before the verifier pass. Filling the vectors is S2-T2, and the reranker is S2-T3. One round.
Done when:
- `src/xn/storage/qdrant.py` creates `xn_chunks` with `dense` 1024 cosine and `sparse` with IDF, plus `xn_arcs`, and running it twice is safe
- The payload indexes match the plan, with `novel_id` as the tenant index
- Point IDs are deterministic UUID5, so a re-upsert adds no duplicates
- A test against the real Qdrant shows that a query on novel A never returns novel B, and nothing past `chapter_max` comes back
- `configs/config.yaml` uses the new collections, and `src/storage/qdrant_client.py` is deleted
- ADR-03 and §3.2 record the BM25 decision
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-01: deliver the Qdrant store, config and tests -> PASS, 0 blockers, 755bb5b, 0 agy tokens on the delivered code
Sub-goals:
- none yet
Changes:
- 2026-11-01: owner approved the goal and the decisions above
- 2026-11-01: owner moved the code from agy to the lead, after two agy runs (157018 tokens) were blocked by a denied MCP call and a denied `read_file`, with no code written
- 2026-11-01: verifier pass at 755bb5b -> PASS, every "Done when" line met; owner approved marking the goal done, recorded with PR #18

### G-15: S2-T2 pipeline stage S4, chunk and embed (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S2-T2. Depends on G-3, G-4, G-5 and G-12.
Owner decisions: the lead writes the code, with no agy, running the same two checks before each verifier pass. OQ-17: `entity_ids` comes from whole-word matches against the aliases visible at the chunk's chapter, tagging every entity that shares a matched alias; `arc_id` copies `chapters.arc_id`, null for now, and S5 sets it on the points later (S3-T1 gains that step). BGE-M3 stays in Ollama, so S4 refuses to start while any other model is loaded (blueprint and plan wording change: "Ollama unloaded" no longer applies). BM25 is new shared code that S2-T3 will reuse: word tokens, stable hashed term IDs, BM25 term weights, IDF from Qdrant. The input hash covers chunk text, entity tags and settings; an unchanged chapter makes no embed call, and a changed chapter deletes its points and writes new ones. S4 stops at the first chapter where S2 is not done, takes `--from` and `--to`, and refuses `--stale-only`. S4 writes `jobs` rows and no `llm_runs` rows. The live run uses a copy of `data/eval/g101-full.db` (prompt v2) on chapters 1-100, into `xn_chunks`, whose data stays for S2-T3. Three rounds: foundations, stage, live run.
Done when:
- `xn run --novel pntt1 --stage s4 --from --to` works, also as `python -m xn`
- Text chunks are ~450 tokens with 60 overlap, plus one `summary` point per chapter from `micro_summary`
- Each point has BGE-M3 dense and BM25 sparse vectors, and a payload with `entity_ids` and `arc_id`
- `entity_ids` uses only aliases visible at that chapter, and a test proves it
- A second run makes no embed calls and adds no points, and a changed chapter rewrites only that chapter and leaves no stale points
- A failed chapter is marked `failed` and stops the run
- The live run gives identical point counts on both runs, dense and sparse queries respect `chapter_max`, and measured items/s is in the blueprint §3.9 cost table
- OQ-17 is closed in the plan, and the blueprint's Ollama lines are corrected
- Unit tests use a fake embedder
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-02: deliver the chunker, BM25 vectors, embed client and alias tagger -> PASS, 0 blockers, 6fc9cef, 0 agy tokens
- R-2 2026-11-02: deliver stage S4 with its runner, CLI and config -> PASS, 0 blockers, 7ca6b97, 0 agy tokens
- R-3 2026-11-02: run S4 live on chapters 1-100 twice and correct the docs -> PASS, 0 blockers, d7351ac, 0 agy tokens
### G-13: benchmark the cloud model on S1 extraction (done)
Plan: chat plan of 2026-11-02. Prompt v2 unchanged, chapters 1-100.
Owner decisions: raw chapter text may go to OpenRouter for this benchmark only, as one exception to A-04; production S1 stays local. Concurrency starts at 10 with a hard limit of 20. The run skips `daily_spend_cap_usd`, and the owner monitors cost.
Done when:
- `deepseek/deepseek-v4.1-flash-1017` runs prompt v2 unchanged on 50 chapters: the 20 gold chapters and 30 others from chapters 1-100
- The report compares it with local `qwen3.8:udiq4_hui` on recall, precision, category accuracy, event-type accuracy and flag F1, with held-out chapters shown apart
- The report gives schema success, retries, 429 errors, s/chapter, tokens and actual cost per chapter, plus estimated time and cost for all 2,468 chapters
- Name overlap with the local run is shown for the 30 non-gold chapters
- `eval/`, the prompt, production config and production S1 are unchanged
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-02: deliver the benchmark runner and its unit tests -> FAIL, 2 blockers (resume counts and timing), 2f72e51, 0 agy tokens
- R-2 2026-11-02: fix the resume counts and timing -> PASS, 0 blockers, 8e2c73a, 0 agy tokens
- R-3 2026-11-02: run the 50 chapters and report the numbers -> FAIL, 1 blocker (a cause the data does not show), d531267, 0 agy tokens
- R-4 2026-11-02: fix the report wording -> PASS, 0 blockers, 913e631, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-11-02: owner approved the goal and the decisions above
- 2026-11-02: live run on `data/eval/g15-s4.db`, the copy of `g101-full.db`: run 1 wrote 704 points (604 text, 100 summary) in 32.6 s, run 2 skipped all 100 chapters at 704 points. Four real queries, dense and sparse, stayed within `chapter_max` 10 and returned nothing for another novel. Ollama's `/api/ps` showed BGE-M3 at 689 MB resident
- 2026-11-02: verifier pass on the whole branch at d7351ac -> PASS, every "Done when" line met. Its NIT on plan assumption A-02 still saying "Ollama unloaded" was fixed after that pass
- 2026-11-02: owner approved marking the goal done, recorded with PR #22
- 2026-11-02: the guard blocked the agy run, so the owner chose to have the lead write the runner, with no agy

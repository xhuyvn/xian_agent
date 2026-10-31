# Project goals

Kept by the lead. Goals and their "Done when" lines change only with the owner's yes.
Keep this file short: move finished goals to Archive.

Status: proposed | active | parked | done | dropped
Round line: `R-<n> <date>: <what this round had to achieve> -> PASS|FAIL, <n> blockers, <commit>, <tokens> agy tokens`

## Active

### G-5: S1-T5 pipeline stage S2, entity resolution (active)
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

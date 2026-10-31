# Project goals

Kept by the lead. Goals and their "Done when" lines change only with the owner's yes.
Keep this file short: move finished goals to Archive.

Status: proposed | active | parked | done | dropped
Round line: `R-<n> <date>: <what this round had to achieve> -> PASS|FAIL, <n> blockers, <commit>, <tokens> agy tokens`

## Active

### G-4: S1-T4 pipeline stage S1, extract (active)
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
Sub-goals:
- none yet
Changes:
- 2026-10-31: owner approved the goal and the decisions above

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

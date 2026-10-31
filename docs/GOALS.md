# Project goals

Kept by the lead. Goals and their "Done when" lines change only with the owner's yes.
Keep this file short: move finished goals to Archive.

Status: proposed | active | parked | done | dropped
Round line: `R-<n> <date>: <what this round had to achieve> -> PASS|FAIL, <n> blockers, <commit>, <tokens> agy tokens`

## Active

### G-1: S1-T1 project setup and config split (proposed)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T1. The owner must approve this goal before work starts.
Needs owner decisions: OQ-01 (package layout), OQ-02 (dependency tooling), OQ-08 (Python version).
Done when:
- `configs/genres/xianxia.yaml` and `configs/novels/pntt1.yaml` exist, and config is split into global, genre and novel levels
- The Redis service is removed from `docker-compose.yml`
- Project layout and dependency files follow the owner's answers to OQ-01, OQ-02 and OQ-08
- `py -m compileall -q src` passes
Rounds:
- none yet
Sub-goals:
- none yet
Changes:
- none yet

### G-2: S1-T2 SQLite schema and repository layer (proposed)
Plan: docs/IMPLEMENTATION_PLAN.md, S1-T2. Depends on G-1. The owner must approve this goal before work starts.
Needs owner decisions: OQ-05 (`jobs` rows for arc-level stages), OQ-06 (undefined types this task needs), and how unit tests join the Project commands.
Done when:
- All 15 tables in the plan exist with `novel_id` scoping, WAL mode and foreign keys on
- Repository methods require `novel_id` and `chapter_max`, and a unit test proves no method accepts a missing bound
- That unit test and `py -m compileall -q src` both pass
Rounds:
- none yet
Sub-goals:
- none yet
Changes:
- none yet

## Parked

## Archive

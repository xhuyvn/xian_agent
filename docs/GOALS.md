# Project goals

Kept by the lead. Goals and their "Done when" lines change only with the owner's yes.
Keep this file short: move finished goals to Archive.

Status: proposed | active | parked | done | dropped
Round line: `R-<n> <date>: <what this round had to achieve> -> PASS|FAIL, <n> blockers, <commit>, <tokens> agy tokens`

## Active

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

### G-25: benchmark `qwen2.5:14b-instruct` against `qwen3.8:udiq4_hui` on S1 extraction (active)
Plan: chat plan of 2026-11-05. Follows G-7, G-8, G-10.1 and G-13. Cut from `cld/sprint-3-tasks-60f7ab`.
Owner decisions: the lead writes the code, with no agy. Chapters are the G-13 set: the 20 gold chapters and 30 others from 1-100. Prompt v2, one chapter at a time, temperature 0.1. `qwen2.5:14b-instruct` runs as a new Ollama model `qwen25-14b-10k` from `ollama/Modelfile.qwen25-14b-10k` at `num_ctx 10240`; it has no thinking mode. The baseline is the G-10.1 run of `qwen38-udiq4-hui-10k` in `data/eval/g101-full.db` (prompt v2, chapters 1-100), read only; it is not run again. Databases go to `data/bench/g25-qwen25/`, never `data/eval/*.db`. No winner rule: the report recommends and the owner decides. Nothing is downloaded. Two rounds: harness, then run and report.
Done when:
- `xn.bench` has a `qwen25-14b-10k` candidate, can run the gold chapter set and scores it against the gold, with unit tests on a fake model
- `qwen25-14b-10k` runs the 50 chapters through the real S0 and S1 code, with the GPU checked free first
- `docs/S1_T9_EVAL.md` gains a section giving it and the G-10.1 baseline side by side, on the same 50 chapters: schema success, first-try rate, entity recall and precision (all 20, dev and held out apart), category and event-type accuracy, flag F1, s/chapter mean and p95, idle and peak VRAM, the exact commands and caveats
- `eval/`, the prompt, production config and production S1 are unchanged
- `qwen25-14b-10k` also runs the same chapters with 2 S1 processes at once (`--workers 2`), and the report compares its wall s/chapter and gold scores with the one-at-a-time run
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-05: deliver the gold chapter set, gold scoring, the qwen2.5 candidate and parallel workers -> PASS, 0 blockers, e1c1373, 0 agy tokens
- R-2 2026-11-05: run qwen25-14b-10k one at a time and with 2 workers, and report against the G-10.1 baseline -> PASS, 0 blockers, 6223bd6, 0 agy tokens
- R-3 2026-11-05: run qwen25-14b-16k on the same chapters and report it next to 10k -> PASS, 0 blockers, e6dc5d8, 0 agy tokens
- R-4 2026-11-05: run qwen35-9b-udq6-10k on the same chapters and report it next to qwen2.5 and production -> PASS, 0 blockers, afef249, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-11-05: owner approved the goal and the decisions above
- 2026-11-05: owner added a test of `qwen25-14b-10k` processing 2 chapters in parallel, for speed, as a "Done when" line. Ollama's `OLLAMA_NUM_PARALLEL` is unset and stays so unless the owner says otherwise
- 2026-11-05: owner: "why you run test with qwen38-udiq4-hui-10k, it already had result test before. run only qwen25-14b-10k and add to benchmark S1_T9_EVAL.md". The fresh baseline run was stopped after about 8 minutes and its database deleted; the G-10.1 run is the baseline and the results go into `docs/S1_T9_EVAL.md` instead of a new report
- 2026-11-05: owner: "try to run with model qwen2.5:14b-instruct with 16k token." Added `qwen25-14b-16k` (`ollama/Modelfile.qwen25-14b-16k`, `num_ctx 16384`), run one at a time on the same 50 chapters and added to the G-25 section; the 10k results stay
- 2026-11-05: owner: "let test with for speed and quality with qwen3.5:9b_UD_Q6 add result to S1_T9_EVAL.md". Added `qwen35-9b-udq6-10k` (`ollama/Modelfile.qwen35-9b-udq6-10k`, `num_ctx 10240`, thinking off), run one at a time on the same 50 chapters and reported in the G-25 section

### G-27: make `qwen3.5:9b_UD_Q6` good enough to replace `qwen3.8:udiq4_hui` as the S1 extractor (active)
Plan: chat plan of 2026-11-06. Follows G-26. Cut from `cld/heuristic-austin-b6a073` (PR xhuyvn/xian_agent#31, not yet merged).
Owner decisions:
- The owner did not say who writes code. The default from the plan is that `agy-coder` writes steps 1 and 2. If `agy` is unavailable or blocked, stop and ask the owner. Infrastructure and runs are the lead's.
- Prompt `v4` is a new file. `v2`, `v3`, `eval/` and production config stay unchanged until the final owner yes.
- Tuning uses the 10 dev gold chapters only (`xn.gold.gold_split`, first of each pair). Held out is scored once, in the full test. At most 2 prompt attempts.
- Model: `qwen35-9b-udq6-10k`, num_ctx 10240, thinking off, temperature 0.1.
- Databases go to new folders under `data/bench/` in the main checkout. Never write to `data/eval/*.db`.
- No downloads. agy as the extractor and the two-pass/glossary/GLiNER ideas are parked.
- Switching the production config needs a separate explicit owner yes after the report.
Steps:
1. Prompt v4 = v3 plus a one-line definition for each category (pills, potions, talismans and treasures are `weapon_treasure`; arts and methods are `technique`; sects, gangs, valleys, peaks and villages are `dungeon`; beasts and puppets are `puppet_beast`), plus a clearer `realm_breakthrough` rule. Examples come from dev chapters only. Run each attempt with `uv run py -m xn.bench ... --gold --gold-split dev --model qwen35-9b-udq6-10k --prompt v4`.
2. An S1 clean-up step behind config, off by default (e.g. `s1.postprocess` keys), with unit tests:
   - the G-26 name filter (`xn.name_filter`, rule `caps-proper+words`);
   - a category fix by the name's last word (offline on G-26's v3 run, dev only, this lifted category accuracy from 0.83 to 0.96; the lists were drawn from those dev errors, so expect less on held out; draft: "đan/hoàn/tán/thủy/phù" -> `weapon_treasure`, "công/quyết/bộ/thuật/pháp/chú" -> `technique`, "môn/bang/cốc/phong/thôn" -> `dungeon`, never overriding `character`);
   - removing repeated identical events (the model repeats "Hàn Lập MENTIONED" with alternating quotes).
   With it off, S1 must behave exactly as now.
3. Full test: S0-S3 on chapters 1-100 with qwen3.5 + the best prompt + clean-up on, using a config copy, then the full gold report (extraction, aliases, Q&A, spoilers; see `xn.gold.gold_report`, `xn.gold_run`, and the "Reproduce" section of `docs/S1_T9_EVAL.md`), next to the G-10.1 production run (`data/eval/g101-full.db`, read only). Note: `local_model(config)` also drives the S2 adjudicator and the query router, so switching the extractor changes those too.
4. Report in a "G-27" section of `docs/S1_T9_EVAL.md`. Recommend; the owner decides the switch.
Done when:
- S1 can run the clean-up step from config, off by default, with unit tests
- With qwen3.5 on the 20 gold chapters: entity recall >= 0.90, precision >= 0.88, category accuracy >= 0.90, held out reported apart
- Alias F1 and Q&A citation hit rate are no worse than G-10.1, and spoiler leaks are 0
- S1 <= 20 s/chapter mean
- The "G-27" section of `docs/S1_T9_EVAL.md` has exact commands and caveats, including that the 20 gold chapters have judged every run since G-10.1
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-06: prompt v4 (two dev attempts; attempt 1 kept: dev recall 0.96, category 1.00, realm_breakthrough found, 19.6 s/chapter; with clean-up precision 0.85) and the S1 clean-up step, off by default -> PASS, 0 blockers, fcf8787, 0 agy tokens; the lead wrote the code under CLAUDE.md's owner override; NIT: extra blank lines in s1_postprocess.py (fixed in c0c7367)
- R-2 2026-11-06: full S0-S3 test on chapters 1-100 (qwen3.5, v4, clean-up on) and the G-27 report: recall 0.96, precision 0.90, category 0.98 (held out 0.94 / 0.91 / 0.98), alias F1 0.67 vs 0.64, leaks 0, S1 17.5 s/chapter; Q&A citation hit rate 0.80 vs 0.93 with the whole local model on qwen3.5 (the router), 0.93 with qwen3.5 as extractor only -> FAIL, 1 blocker (a wrong held-out count in the report), 9873130; fixed in fb53ba3 -> PASS, 0 blockers, 0 agy tokens. The Q&A line is not met with the whole model switched, so the goal stays active for the owner's decision
Sub-goals:
- G-27.1 (active): run qwen3.5 for S1 only and keep `qwen3.8:udiq4_hui` as router, S2 adjudicator, S5 review and prefix model. An optional `models.s1_extractor` block (same shape as `local_extractor`) is used by S1 when set; every other stage keeps `local_extractor`; without the block nothing changes. `xn.bench` sets its candidate on `s1_extractor` too. Checked by re-running only the gold report on the G-27 database with a config copy (S1 model qwen3.5, `local_extractor` qwen3.8). The lead writes the code. Production config is out of scope; switching it needs a separate owner yes. One round.
  Done when:
  - Without `s1_extractor`, every stage uses `local_extractor` exactly as now, with unit tests
  - With it set, S1 calls and records the S1 model, and S2, the router, S5 and prefix use `local_extractor`, with unit tests on fake models
  - The config-copy gold report gives Q&A citation hit rate >= 0.93, alias F1 >= 0.64 and 0 leaks
  - `py -m compileall -q src` and `uv run pytest tests/unit` pass
  Rounds:
  - R-1 2026-11-06: `models.s1_extractor` for S1 only, unit tests, S2 rebuild + S3 + gold report on a copy of the G-27 database with a split config copy: Q&A citation hit rate 0.93, alias F1 0.644, 0 leaks, extraction unchanged -> FAIL, 1 blocker (an uncommitted CLAUDE.md edit from the working tree was swept into 037ef04), 46c0a00; taken out in ca1a4ad -> PASS, 0 blockers, 0 agy tokens; NIT: S2, S5 and prefix are covered by a source scan, only the router by a fake-model test
Changes:
- 2026-11-06: owner approved the goal and the decisions above ("go")
- 2026-11-06: owner approved sub-goal G-27.1, the S1-only model split ("Go")
- 2026-11-06: owner approved the production switch ("Yes, switch it"): `configs/config.yaml` gains `models.s1_extractor` = `qwen3.5:9b_UD_Q6` (`qwen35-9b-udq6-10k`), `prompts.s1_extract: v4` and the `s1.postprocess` block (all three fixes on); `local_extractor` stays `qwen3.8:udiq4_hui` for the router, S2, S5 and prefix. `xn run --stage s1 --stale-only` now compares jobs against the clean-up-aware hash, and the runner tests' fixture names the protagonist mid-sentence so the name filter keeps him. The existing ingests are not re-run here: v4 makes S1 redo every chapter (about 12 h for 2,468), then S2 needs `--rebuild`

### G-32: free-quota Alibaba model pools for S5, S6, prefix and S7 (active)
Plan: chat plan of 2026-11-11, from the G-29 run. Cut from this branch at ad95f05.
Owner decisions: the lead writes the code, no agy in Sprint 4. Each model below has a 1M-token free quota on the owner's Alibaba Model Studio workspace and fails when it is used up. S5, prefix and S7 run on a Qwen pool (qwen3.8-max, qwen3.8-2.4t-a95b, qwen3.8-27b, qwen3.8-max-0902, qwen3.7-max); S6 runs on a DeepSeek pool (deepseek-v4-pro, deepseek-v4-pro-0813, deepseek-v4.1-flash-1017, deepseek-v4.1-flash) instead of OpenRouter. When every model of a pool is used up, the run stops and the owner is alerted. The G-29 run stops after S4 (`xn ingest --until s4`) and resumes from S5 on the pools. Only the run config copy changes; `configs/config.yaml` does not. S5, prefix and S7 send chapter text and summaries to Alibaba from now on (owner's choice).
Done when:
- Optional `model_pools` and `stage_pools` config blocks: a stage named in `stage_pools` calls its pool, in order; a "quota used up" error marks that model exhausted in a new `model_quota` table, the same call moves to the next model, and later calls and resumed runs skip it; when every model is used up, the stage stops with a clear error and a resume continues from there; without the blocks every stage runs as before; unit tests on fake models
- `llm_runs` records the pool model that answered each call
- A smoke run of S4-S7 on chapters 1-100 on a database copy with separate Qdrant collections finishes on the pools, with time and tokens per model reported
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
Changes:
- 2026-11-11: owner approved the goal and the plan ("Go"), with S3 and S4 finished first
- 2026-11-11: probe: qwen3.8-2.4t-a95b only accepts enable_thinking true, so a pool entry may carry its own extra_body; the other eight models answer with thinking off

### G-31: benchmark `qwen3.5_12k:q8_mtp` against `qwen3.8:udiq4_hui` as the S2 adjudicator (active)
Plan: chat plan of 2026-11-08, from the S4-T1 run. S2 on pntt1 ran at about 13 s/chapter (664 chapters in 2.4 h) and gets slower as aliases grow, so the remaining 1,804 chapters would take 6.5 h or more. S2 runs chapters in order, so it cannot be split across workers; a faster model is the lever. Cut from this branch at 35d52e8.
Owner decisions: the lead writes the code, no agy in Sprint 4. The G-29 run is stopped first (S2 at chapter 664) and resumed later, maybe with qwen3.5 on S2. Benchmarks use copies of `data/xn.db` under `data/bench/`; the live database is not written. The production config changes only on a separate owner yes.
Done when:
- An optional `models.s2_adjudicator` block (same shape as `local_extractor`) is used by the S2 adjudicator when set, and S2 records that model; without it nothing changes; the router, S5 and prefix keep `local_extractor`; unit tests on fake models
- S2 is rebuilt on chapters 1-200 of a pntt1 database copy twice, once per model, with S3 and the gold report on each, and the router kept on `qwen3.8:udiq4_hui` for both
- `docs/S2_BENCHMARK.md` gives s/chapter (mean, and for chapters 1-100 and 101-200), adjudicator calls, alias F1, Q&A citation hit rate, spoiler leaks, VRAM, a projected time for the remaining S2 of pntt1, exact commands and caveats, and its numbers match the databases
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
Rounds:
- R-1 2026-11-08: optional `models.s2_adjudicator` (edafad6), S2 rebuild of chapters 1-200 on two copies with S3, S4 (1-100) and the gold report, `docs/S2_BENCHMARK.md`: qwen3.5 2.4 min against 19.0 min, 0.80 against 6.36 s/call, alias F1 0.667 against 0.644 (one pair, run-to-run), Q&A 1.00 both, 0 leaks -> FAIL, 1 blocker (speed counted the gold report's 10 adjudication calls per run as S2), fixed in e28ee82 -> PASS (delta), 0 agy tokens; NIT: 7.95 shown as 7.9
Sub-goals:
- G-31.1 (active): better S2 merges on qwen3.5. G-31 found qwen3.5 8 times faster with the same gold scores, but in chapters 1-200 it merged some generic words into named things ("phù lục" into "Kim Quang Tráo") and some names that share only the type word ("Kim tủy hoàn" into "Trừu tủy hoàn"), and both models miss every title form of a person ("Hàn sư đệ", "Vương môn chủ"), which the fuzzy matcher never sends to the adjudicator. The lead writes the code. Work: (a) record every pair S2 adjudicates on chapters 1-200 of a pntt1 copy, plus title-form pairs, labelled by the lead from the chapter text before any scoring and split into dev and held-out halves; (b) rule checks before the model (generic words never merge into a named entity; names that share only the type word are different); (c) title-form candidates (surname + title sent to the adjudicator with the matching person); (d) adjudicate prompt v2 with naming rules and short examples; optional second vote on "same". Changes are tuned on dev only. The production config changes only on a separate owner yes; a new prompt version makes S2 stale, so the G-29 resume then rebuilds S2.
  Done when:
  - The pair set is committed with labels before any scored run, with dev and held-out halves
  - On held-out pairs, qwen3.5 with the changes makes no more false merges than qwen3.8 with prompt v1 and has at least its accuracy, both replayed on the same pairs
  - On a fresh S2 rebuild of chapters 1-200 with S3, S4 (1-100) and the gold report: alias F1 >= 0.667 with precision 1.00, Q&A citation hit rate >= 0.93, 0 spoiler leaks, and mean adjudication latency <= 1.5 s/call
  - Without the new settings, S2 runs as before (unit tests); new rules and candidates have unit tests on fake models
  - `py -m compileall -q src` and `uv run pytest tests/unit` pass
  Rounds:
  - R-1 2026-11-08: labelled pair set (ac92302, e119f5d), opt-in `s2_rules` (generic words on the mention, title forms merged on exactly one "same"), prompt v2 tried and dropped, replays on dev, one held-out run, full rebuild 1-200 + S3 + S4 + gold (6e3f4b8): gold alias F1 0.806 (precision 1.00), Q&A 1.00, 0 leaks, 0.71 s/call; held-out accuracy 0.866 against 0.817 for qwen3.8, but 6 false merges against 4, so the held-out false-merge line is not met -> FAIL, 1 blocker (that line; verifier recomputed 6 against 4, every other line met), 0 agy tokens; NITs: held-out accuracy uses 82 pairs (77 when titles are scored per mention: 0.857 against 0.805), 201 calls is 202 in the DB, rule lists were written after seeing title pairs of both halves. Waiting for the owner: accept or keep working
  - R-2 2026-11-08: logprob threshold tried and dropped (false merges as confident as true matches); config-driven guards (type clash, distinct name word, part of a place, title rank clash; ead2de9) cut dev false merges 10 -> 3; 160 blind-labelled held-out pairs from chapters 201-400 (6c36227): qwen3.5 + rules 15 false merges, accuracy 0.878, against qwen3.8 7 and 0.919 -> FAIL on the held-out line (own measurement; verifier not run, as the line fails on the data), 0 agy tokens. Two rounds done: stopped and reported to the owner. Exploratory, after scoring: qwen3.5 then qwen3.8 confirming "same" gives 5 false merges, 0.926, qwen3.8 on 34 of 148 pairs
  - R-3 2026-11-10: optional `models.s2_confirmer` (15cd85d); 160 blind held-out pairs from chapters 401-600 (ee055c9): hybrid 2 false merges, 0.942 against qwen3.8 7 and 0.916 (held-out line met); full rebuild 1-200 + S3 + S4 + gold: alias F1 0.769 (precision 1.00), Q&A 1.00, 0 leaks (met); 4.86 s/call and S2 20.6 min against 19.0 for qwen3.8, because the two models swap on the 16 GB GPU, so the "<= 1.5 s/call" line is not met -> PASS on the round (verifier recomputed held-out 3, the gold report and the latency), the latency line confirmed not met, 0 agy tokens; NITs: R-2 qwen3.8 row needed its title-pair rule written down (done), the two held-out 3 replays ran on Ollama 0.40.15 and 0.40.16. G-31.1 stays open: the latency line needs an owner decision
Changes:
- 2026-11-08: owner approved the goal and asked to stop the G-29 run for it ("Approve the S2 qwen3.5 benchmark plan (new goal G-31) to run stop this run, we can resume it later maybe with qwen3.5")
- 2026-11-08: owner approved sub-goal G-31.1, better S2 merges on qwen3.5, including title forms ("Go")
- 2026-11-08: owner approved the cost of prompt v2: all of S2 becomes stale and the G-29 resume rebuilds S2 from chapter 1 on qwen3.5 (about 1-1.5 h)
- 2026-11-08: owner chose "Keep working, deep dive and researching improve wrong merges" after the R-1 FAIL; R-2 keeps the Done-when lines, takes fresh held-out pairs from chapters 201-400 labelled blind (without model decisions), and the R-1 dev and held-out pairs become dev
- 2026-11-08: after R-2 stopped at the two-round limit, owner chose "Test the hybrid": R-3 adds an optional confirmer model (qwen3.5 with the rules decides, qwen3.8 confirms its "same" answers) and checks it on fresh blind held-out pairs from chapters 401-600. The hybrid is expected to miss the "<= 1.5 s/call" line (about 2 s/call); changing that line needs a separate owner yes
- 2026-11-10: owner chose "Flash hybrid, full run" after the qwen3.8-flash confirmer test (0 held-out false merges, 92 s for S2 1-100, 1.53 s/call): the G-29 run config copy (`data/runs/g29-configs/`) gets `s2_adjudicator` qwen3.5, `s2_confirmer` qwen3.8-flash and the R-2 `s2_rules`; S2 is rebuilt from chapter 1; flash tokens beyond the free 1M are paid by the owner and are not counted by the spend cap. `configs/config.yaml` stays unchanged. Whether G-31.1 is done with the latency line at 1.53 s is still the owner's call

### G-29: S4-T1 full ingestion of `pntt1`, chapters 1-2,468, with stop and resume (active)
Plan: chat plan of 2026-11-07, approved with changes on 2026-11-08. `docs/IMPLEMENTATION_PLAN.md` S4-T1. Cut from `main` at f256455.
Owner decisions:
- The lead writes the code, no agy in Sprint 4 (2026-11-07).
- Database `data/xn.db` in the main checkout. The pntt1 points in Qdrant `xn_chunks` and `xn_arcs` (704 old chunk points) are deleted before the run.
- `daily_spend_cap_usd` is raised to 2.00 for this run only, then set back to 1.00. Done with a run-only config copy (`data/runs/g29-configs/`, `--configs-dir`), because a unit test pins `configs/config.yaml` at 1.00; the repo config never changes.
- Over 20 h: the run goes on and the time is reported. It must be stoppable and resumable: `xn stop` asks a running ingest to stop at the next safe point, and `xn ingest --resume` continues it, skipping finished work. Run state lives in a new `pipeline_runs` table (status, current stage, progress, stage times, stop request) so the S4-T5 `/v1/pipeline/runs` endpoints and the S4-T7 web UI can show and manage long runs later.
- S1 runs on `qwen3.5_12k:q8_mtp` (G-30 switch, PR xhuyvn/xian_agent#36); the run checks this before starting.
- Two rounds: R-1 code and unit tests, R-2 the live run and `docs/S4_T1_RUN.md`.
Done when:
- `xn ingest --novel pntt1` runs s0, s1, s2, s3, s4, s5, s6, prefix, s7 in order, records the run in `pipeline_runs`, stops at the first stage with failures, and refuses a second run while one is live
- `xn stop --novel pntt1` stops a running ingest within one chunk (50 chapters) on s1, s3 and s4, and at the next stage boundary on the others; `xn ingest --novel pntt1 --resume` continues from the stopped stage; unit tests with fake stages cover order, stop, resume, failure and single-flight
- `xn status --novel pntt1` prints the run's state and stage times, done and failed jobs per stage, failed chapters, and cloud spend today and in total
- All 2,468 chapters are `done` for s0-s4, every arc has s6, prefix and s7 output, and 0 jobs are `failed`
- Cloud spend for the run is <= $5; wall time is reported, a miss of 20 h is stated as a miss
- `docs/S4_T1_RUN.md` gives stage times, spend, failures and how they were cleared, row counts, commands and caveats, and its numbers match the database
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-08: `xn ingest`, `xn stop`, `xn status` and the `pipeline_runs` table, with unit tests on fake stages -> PASS, 0 blockers, f5d11df; NITs 1-2 (a chunk with stopped_at did not end s3/s4; a boundary stop resumed the finished stage) fixed in 1ce46d1 -> PASS (delta), 0 agy tokens; open NITs: the single-flight check and the run insert are not atomic (needs a unique live-run index for S4-T5), `pid_alive` has no ctypes argtypes and PID reuse can make a dead run look alive
Pause 2026-11-08: S1 finished all 2,468 chapters (79.4 min on one worker, then about 4.8 h on four after the resume at about 02:07 UTC, ending about 06:54 UTC), 0 failed. S2 ran 664 chapters from about 06:54 to 09:18 UTC (about 13 s/chapter). The owner asked to stop for G-31. `xn stop` only lands between stages for S2, so the process was ended (taskkill) after chapter 664 was saved; chapter 665 is left `running` and is redone on resume, since S2 resumes at the first chapter that is not done. The S1 time of the resumed part and the S2 time so far were only in memory, so they come from the log and status checks above, not from `pipeline_runs`.
Resume 2026-11-10 (G-31.1 decision): S2 rebuilt from chapter 1 with the flash hybrid (`xn run --stage s2 --rebuild`), then `xn ingest --resume` for S3 to S7. The S2 stage time comes from the log, not from `pipeline_runs`.
Pause 2026-11-10 16:35 UTC (owner asked to stop for the night): S2 done to chapter 923 (chapter 924 left `running`, redone on resume); S2 1-596 took 30 min, it stopped once at 597 on a bad flash answer (fixed in 337638f), then 597-923 ran 15:5x-16:35. So far 3,138 qwen3.5 calls and 649 flash calls (5 failed, counted as different), 744k flash input and 76k output tokens. Resume: `xn run --stage s2 --to 2468` (no `--rebuild`) with `--configs-dir data/runs/g29-configs`, then `xn ingest --resume`.
Sub-goals:
- G-29.1 (done): parallel S1 in `xn ingest`. G-30 found Ollama 0.40.15 gives `qwen3.5_12k:q8_mtp` 4 slots, and 4 workers ran at 5.7 s/chapter against 10.7 (about 3.9 h against 7.3 h for 2,468). `xn ingest --workers N` runs S1 chunks in N threads, each with its own SQLite connection on the same database (WAL); other stages stay one at a time. A stop lands after each worker's current chunk; resume works as before. The live run is stopped and resumed with 4 workers. The lead writes the code. One round.
  Done when:
  - With `--workers 1` (the default) ingest behaves exactly as in R-1, with the R-1 tests unchanged
  - With `--workers N`, S1 chunks run in N threads on their own connections, every chunk runs once, failures and exceptions are counted, a stop ends the stage after the running chunks, and unit tests cover it on a file database
  - The resumed live run shows S1 at <= 7 s/chapter wall over at least 200 chapters, with 0 `database is locked` errors
  - `py -m compileall -q src` and `uv run pytest tests/unit` pass
  Rounds:
  - R-1 2026-11-08: `--workers N` for S1 in `xn ingest`, 30 s SQLite busy timeout, spend cap moved to a run-only config copy -> FAIL, 1 blocker (`local_model` cached one model for all threads, so workers shared an HTTP client across event loops), 058b67b; fixed with a per-thread model cache and a deterministic stop test in e19e52a -> PASS (delta), 0 blockers, 0 agy tokens; the first verifier hung on its own background work and a fresh one gave the delta verdict; NIT: `_MODEL_CACHE.__dict__.setdefault` could be `getattr`/`setattr`. The live-speed line is checked on the resumed run
  - Live check 2026-11-08: run f57c2d70 stopped with `xn stop` at 350 S1 chapters (clean chunk boundary, 0 failed) and resumed with `--workers 4 --chunk 25`: 255 chapters in 1,779 s between first and last done, 7.00 s/chapter steady (last 200 also 7.00), 0 `database is locked`, 0 failed, 14.1 GB VRAM. Meets the <= 7 line exactly; slower than G-30's 5.7 on the 50 bench chapters (later chapters, run measured end to end)
Changes:
- 2026-11-08: owner approved the plan with decisions 1-3, replaced decision 4 with stop and resume for later use in the web UI, and asked to check the S1 model
- 2026-11-08: owner approved sub-goal G-29.1, parallel S1 workers ("yes, add it as sub-goal G-29.1")
- 2026-11-08: owner marked G-29.1 done ("Yes") after the delta PASS at e19e52a and the live check at 7.00 s/chapter, every Done-when line met
- 2026-11-08: owner asked to stop the run for G-31 and resume it later, maybe with qwen3.5 on S2

## Parked

## Archive

### G-30: benchmark `qwen3.5_12k:q8_mtp` against production S1 (`qwen35-9b-udq6-10k`) on S1 extraction (done)
Plan: chat plan of 2026-11-07, from the S4-T1 planning session, to decide which model runs the 12 h S1 of S4-T1. Follows G-25, G-26, G-27 and G-27.1. Cut from `main` at 1551d08.
Owner decisions: the lead writes the code, no agy in Sprint 4 (2026-11-07). The model already exists in Ollama as the owner built it (Q8_0, MTP draft 2, `num_ctx 12240`); nothing is created, pulled or downloaded. Same 50 chapters as G-25 (20 gold + 30 others from 1-100), production setup: prompt v4, `s1.postprocess` on, temperature 0.1, thinking off, one chapter at a time, GPU checked free first. Databases go to new folders under `data/bench/`, never `data/eval/*.db`. `configs/config.yaml`, `eval/`, the prompts and production S1 stay unchanged; any switch is the owner's decision.
Done when:
- `xn.bench` has a `qwen3.5_12k:q8_mtp` candidate that runs the existing model as it is, set on `models.s1_extractor` with thinking off, with unit tests on a fake model
- It has run on the 50 chapters with the production setup, into `data/bench/` only
- A "G-30" section of `docs/S1_T9_EVAL.md` puts it next to production S1 and the earlier rows, with schema success, first-try rate, recall and precision (all 20, dev and held out apart), category, event type, flag F1, s/chapter mean and p95, idle and peak VRAM, estimated hours for 2,468 chapters, whether MTP changes speed, whether Ollama runs it with more than one slot, exact commands and caveats
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-07: candidate `qwen3.5_12k:q8_mtp` in `xn.bench` (file-safe DB names, dotted-name report fix), 50 chapters with production setup next to a same-run production control, plus 4-worker runs of both: recall 0.95 / 0.95, precision 0.94 / 0.92, category 0.98 / 0.98, 10.7 vs 16.7 s/chapter (about 7.3 vs 11.5 h for 2,468), MTP acceptance 0.88, Ollama 0.40.15 gives both models 4 slots, 4 workers 5.7 vs 8.4 s/chapter; G-30 report -> PASS, 0 blockers, 061f765, 0 agy tokens; NITs: "within run-to-run variation" is not measured (one run each), older table cells copied from earlier sections, a warm-up MTP call at 23:30 excluded from the count but not mentioned
Changes:
- 2026-11-07: owner approved the goal and the plan ("Go")
- 2026-11-08: owner approved the production switch ("Yes, switch S1"): `configs/config.yaml` `models.s1_extractor` `name` and `serving_name` become `qwen3.5_12k:q8_mtp`; prompt v4, the `s1.postprocess` block and `local_extractor` (`qwen3.8:udiq4_hui`) stay. S1 skips done chapters by input hash and prompt version, not by model, so the switch must land before S4-T1's S1 run
- 2026-11-08: owner marked G-30 done ("Yes, mark done") after PASS on R-1 and the delta PASS on the switch, with every Done-when line met, PR xhuyvn/xian_agent#36

### G-28: S3-T5 timeline and deep-dive evidence under the 20k cap (done)
Plan: `docs/IMPLEMENTATION_PLAN.md` S3-T5, blueprint §3.3 `retrieve`, §3.4 L2/L3, chat plan of 2026-11-07. Cut from `cld/sprint-3-tasks-60f7ab` (after G-22, G-23, G-24). PR into that branch while PR #34 is open.
Owner decisions: the lead writes the code, with no agy. D1 timeline pack, highest rank first: the current arc's `arc_prefix_view` (snapped checkpoint + raw micro-summaries up to N), summaries of arcs ending <= N newest first (split at sentences into items <= 450 tokens, each opening with its span and citing the arc's end chapter), top 8 hybrid chunks, then the resolved entities' events. D2 timeline trim over 20k: events, then chunks lowest score first, then older arcs shrink to a short form (title + resolution) oldest first, then the oldest short forms go; the prefix view is never dropped. D3 deep-dive pack: one digest per arc (full if the arc ends <= N, else the prefix digest at the largest `upto_chapter` <= N), the top 25 ranked events, the top 8 entity-filtered chunks. D4 deep-dive trim: chunks lowest score first, events lowest rank first down to 3, oldest digests, the last 3 events. D5 compare: the D3 pack per entity at half the budget. D6 files: `retrieve.py`, `synthesize.py`, new `src/xn/graph/budget.py`, `schemas/state.py` (`SummaryEvidence`, `EvidencePack.summaries`), `llm.py` (additive `arc_summary` compact kind), `gate.py` (timeline passes with >= 1 summary), `verify.py` and `spoiler_scan.py` (summary chapters cite, summary text counts as evidence), `workflow.py` (checkpoint allowlist), `prompts/synthesize_compact/v4.md` pinned in `configs/config.yaml`, probe module `src/xn/straddle_probe.py`, unit tests. D7 live data: `data/eval/g28-t5.db`, a backup-API copy of `g23-s7.db` with arc payloads from `g22-s6.db` and prefix rows from `g24-prefix.db` (spans checked equal); the originals are never written. Two rounds: code + unit tests, then live probes.
Done when:
- timeline and deep-dive packs follow D1-D5 and synthesize sends them through the compact path with prompt v4
- unit tests with fakes cover the cap and both trim orders (incl. a 250-arc large-N case), the full-vs-prefix digest choice at, below and between checkpoints, no item after N in any pack, and a no-entity timeline passing the gate
- on `g28-t5.db`, evidence probes (each of the 17 arcs at start, first checkpoint, checkpoint+3, mid, end-1, plus end as a control; timeline, Hàn Lập deep-dive, one other entity) show 0 leaks, with the max Hàn Lập deep-dive tokens reported
- 12 `xn ask` answer probes (6 timeline, 6 Hàn Lập deep-dive, N inside 6 arcs) give 0 citations after N and 0 leaks, answers logged
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-07: timeline and deep-dive packs, budget packer, synthesize v4, gate/verify/scan wiring and unit tests -> PASS, 0 blockers, 7b3c9ee, 0 agy tokens; NITs: the prefix view is never trimmed (a very long micro-summary tail could exceed the cap alone), chunks over 450 tokens are not split (as before), unit suite takes ~137 s
- R-2 2026-11-07: live probes on `g28-t5.db` and the 8000-token synthesize cap -> PASS (delta), 0 blockers for the round, ad86707, 0 agy tokens; evidence probes: 306 packs, 0 hard problems (chapter bound, source provenance, egress, 20k), 13 review terms all read as diacritic/punctuation artifacts or generic phrases, max Hàn Lập deep-dive 6,348 tokens, max pack 16,470; answer probes at the 2500 cap: 1 of 12 answered (reasoning filled the cap); at 8000: 10 usable answers with 0 citations after N and 0 future terms, but the timeline at N=385 gave no answer (8000 spent before any) and at N=585 a truncated 139-character stub accepted as ok, so the answer-probe line is not met and G-28 stays open; spend $0.26 on the copy today; NITs: the probe does not flag a short or uncited answer, R-1 NITs still open
- R-3 2026-11-07: cap synthesize reasoning (`effort: low`), refuse a reply stopped at the cap, flag short or uncited probe answers -> PASS (delta), 0 blockers, 4f0fd02, 0 agy tokens; answer probes: all 6 timelines usable incl. N=385 and N=585; the deep-dives at N=263, 477, 585 failed on OpenRouter connection drops after the retries and were rerun once (marked `retry_of` in the JSON); 12 of 12 usable, 0 citations after N, 0 future terms; every Done-when line is met; spend on the copy today $0.39; NITs: the truncation guard is tested only with a fake (the R-2 stub's finish_reason was not recorded), connection drops needed a manual retry, R-1 NITs still open
Sub-goals:
- none yet
Changes:
- 2026-11-07: owner approved the goal and the decisions above ("Go")
- 2026-11-07: owner chose "Raise cap to 8000": synthesize calls `run_compact` with `max_tokens=8000`, as S6 does, instead of the blueprint's 2500. The first live answer probes failed 11 of 12, mostly because the model's reasoning filled 2500 tokens before any answer
- 2026-11-07: owner chose "Limit reasoning" for the two failed timeline probes (R-3): `run_compact` gains an optional `reasoning` argument (OpenRouter `openrouter_reasoning`, additive), `models.macro_synthesizer` and `models.deep_dive_evaluator` set `reasoning: {effort: low}` (S6 does not pass it, so S6 is unchanged), synthesize refuses a reply stopped at the cap (`OUTPUT_TRUNCATED`), and the probe counts an answer under 300 characters or without citations as unusable
- 2026-11-07: owner marked G-28 done ("Yes, mark done") after PASS on R-3, with every Done-when line met, PR xhuyvn/xian_agent#35

### G-23: S3-T3 pipeline stage S7, entity×arc digests and prefix digests (done)
Plan: `docs/IMPLEMENTATION_PLAN.md` S3-T3, blueprint §3.2 S7 and §3.4 L3, chat plan of 2026-11-04. Cut from `cld/sprint-3-tasks-60f7ab`. Runs beside S3-T2 and S3-T4; S3-T5 waits for it.
Owner decisions: the lead writes the code, with no agy. D1: for each (entity, arc) with >= 3 `entity_events` in the arc, the local model writes an 80-150 word digest with `upto_chapter` = the arc's end chapter and `event_count` = events used; the word count is checked in code and sent back to the model, up to 3 tries. D2: prefix digests at every multiple of 10 inside an arc before its end (S3-T4's grid), written only when the entity has >= 3 events up to that chapter and more than at its last checkpoint. D3: each digest's events are read with `chapter_max = upto_chapter`; the entity is named by `canonical_name`, its first surface form. D4: one `jobs` row per arc, `chapter_id` = the arc's end chapter, `stage = 's7'` (OQ-05), whose input hash covers every digest's events, the checkpoint rule, prompt and model; any change redoes the arc. D5: an arc's digests replace its old rows in one transaction; a failed call marks that arc failed and keeps its old rows, other arcs go on; only arcs ending <= `--to` run, and an arc needs S3 done on all its chapters. D6: over ~5k input tokens, MENTIONED events go first. D7: `runner.py`, `cli/main.py`, `repository.py` and `test_repository_guards.py` change only additively. The live run uses `data/eval/g23-s7.db`, a copy of `g21-s5.db` with S2 rebuilt and S3 run on 1-600.
Done when:
- `xn run --novel pntt1 --stage s7 --to 600` writes full and prefix digests for the 17 arcs, and a rerun on unchanged input skips every arc with no model call
- unit tests with a fake model cover the >= 3 threshold, the checkpoint rule and the word-count retry, and a spoiler test shows a prefix digest's prompt and row ignore events after its `upto_chapter`
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
- the live run reports digest counts (full and prefix), failures and time
Rounds:
- R-1 2026-11-04: deliver the S7 stage code, prompt, runner and CLI registration and fake-model tests -> PASS, 0 blockers, 34ec789, 0 agy tokens; NITs: `STAGES += ("s7",)` on its own line (for D7); past MENTIONED, the budget cuts the latest events; a failure building the model leaves the arc's job `running`
- R-2 2026-11-07: live run over the 17 arcs of chapters 1-600 on g23-s7.db, with a length-retry fix (directional retry note, ~100-word target, then a warmer second round of 3 retries at temperature 0.7) -> PASS, 0 blockers, 7653c46, 0 agy tokens; 1,521 digests (659 full, 862 prefix, 468 entities), every one 80-150 words; restarted run processed=14 skipped=3 failed=0 in 5h14m (1,362 calls, avg 13.8 s, 13 digests needed the second round); rerun skipped all 17 in 4 s with no model call; NITs: R-1 NITs still open; `raise AssertionError("unreachable")` is dead code
- R-3 2026-11-07: merge `cld/sprint-3-tasks-60f7ab` (S6, prefix summaries, G-25, G-27) into the S7 branch, both sides kept -> PASS (delta), 0 blockers, 2adddd0, 0 agy tokens; 661 unit tests pass, S7 rerun on g23-s7.db still skips all 17 arcs; NIT: S7 imports sit before the S6 and prefix imports
Sub-goals:
- none yet
Changes:
- 2026-11-04: owner approved the goal and the decisions above
- 2026-11-07: owner marked G-23 done ("Yes, mark done") after PASS on R-2 (live run) and R-3 (merged branch), PR xhuyvn/xian_agent#33

### G-26: cut the false names of `qwen3.5:9b_UD_Q6` on S1 extraction (done)
Plan: chat plan of 2026-11-05. Follows G-25. Cut from `cld/bench-qwen25-14b-872abd`.
Owner decisions: the lead writes the code, with no agy. Prompt `v3` is a new file. `v2`, production config, production S1 and `eval/` stay unchanged. Tuning uses only the 10 dev gold chapters (`xn.gold.gold_split`, the first of each pair). The 10 held-out chapters are scored once, in the last round. Any name filter is measured in this goal only, and putting it into production S1 is a later owner decision. No downloads. The model is `qwen35-9b-udq6-10k`, num_ctx 10240, thinking off, temperature 0.1, on the G-13 50-chapter set. At most 3 prompt attempts; if the targets are not reached, stop and report what each attempt reached. Databases go to new folders under `data/bench/` in the main checkout. The two-pass/scratchpad, dynamic glossary and GLiNER ideas are parked. Three rounds: offline filter study, prompt v3, full run.
Done when:
- The 26 false names of the G-25 qwen3.5 run are sorted by type, and filter rules (capitalization outside sentence starts, category, a list of kinship and address words) are scored offline on the G-25 qwen3.5 database and on `g101-full.db`, tuned on dev chapters only
- `prompts/s1_extract/v3.md` exists, and `xn.bench` can run a chosen prompt version (for example `--prompt v3`, applied to a config copy), with a unit test
- On the 20 gold chapters, qwen3.5 9B with the best setup (prompt v3, with or without a filter) reaches recall >= 0.90 and precision >= 0.88, with held out reported apart, at <= 20 s/chapter mean
- The results are in a "G-26" section of `docs/S1_T9_EVAL.md`, next to qwen3.5 on v2 and production, with exact commands and caveats, including that these 20 gold chapters have been used to judge every run since G-10.1
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-05: sort the 26 false names by type and score filter rules offline on dev -> PASS, 0 blockers, 9f65355, 0 agy tokens
- R-2 2026-11-05: prompt v3, bench --prompt and --gold-split, two v3 attempts on dev (precision unchanged by the prompt; v3 kept at attempt 1) -> PASS, 0 blockers, 7b4a205, 0 agy tokens
- R-3 2026-11-05: run prompt v3 on all 50 chapters, score the filters on all 20 gold chapters with held out apart -> PASS, 0 blockers, 5d03c1b, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-11-05: owner approved the goal and the decisions above ("Go")
- 2026-11-05: owner set `OLLAMA_NUM_PARALLEL=4` on the host during R-3; S1 sends one request at a time, so it is noted as a caveat only
- 2026-11-05: done after R-3 PASS: v3 + caps-proper+words filter, recall 0.95, precision 0.88, 16.3 s/chapter; the filter stays offline
- 2026-11-05: owner: "Yes, run it" (a 2-worker test with `OLLAMA_NUM_PARALLEL` raised). Ran after the goal closed and added to the G-26 section: no speed-up, because Ollama runs the qwen35 architecture with one slot (`n_seq_max = 1`)

### G-24: S3-T4 arc prefix summaries for L2 spoiler safety (done)
Plan: `docs/IMPLEMENTATION_PLAN.md` S3-T4; blueprint §3.4 L2, §3.9. Chat plan of 2026-11-04. Cut from `cld/sprint-3-tasks-60f7ab`. Runs beside S3-T2 and S3-T3; S3-T5 waits for it.
Owner decisions: the lead writes the code, with no agy. D1: a checkpoint is every chapter `c` with `c % 10 == 0` and `start < c ≤ end` of its arc; a reader at N snaps down to the greatest stored checkpoint ≤ N in N's arc. D2: each checkpoint's summary comes from the S1 micro-summaries of `start..c` only, not from the previous checkpoint. D3: stage id `prefix`; one `jobs` row per checkpoint with `chapter_id = upto_chapter`, the end chapter of the span it covers (OQ-05); the input hash covers the arc id and span, the micro-summaries up to `c`, the prompt version and the model; rows and jobs of checkpoints that no longer exist are pruned. D4: prompt `prompts/arc_prefix_summary/v1.md`, output schema kept in `prefix_summaries.py`. D5: `arc_prefix_view(repo, novel_id, chapter_max)` in `prefix_summaries.py` returns the checkpoint summary plus the raw micro-summaries after it up to N, and never the arc's end chapter; the query graph is untouched. D6: `repository.py`, its guard tests, `runner.py` and `cli/main.py` change only additively. The live run uses `data/eval/g24-prefix.db`, a backup-API copy of `g21-s5.db`. One round.
Done when:
- `xn run --novel pntt1 --stage prefix --to 600` writes one `arc_prefix_summaries` row per D1 checkpoint for the 17 arcs, and a rerun on unchanged input skips every checkpoint with no model call
- A unit test with a fake model shows that no stored summary's input, and no `arc_prefix_view` result at any N in 1..end, carries a chapter after its `upto_chapter` or N
- Unit tests cover snap-down at, below and between checkpoints and before the first one, pruning after re-segmentation, and per-checkpoint failure isolation
- The live run on `data/eval/g24-prefix.db` reports row counts, time per checkpoint and total time; the original database is never written
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-04: deliver the prefix stage, prompt, snap-down view and unit tests -> PASS, 0 blockers, 7c02ce4, 0 agy tokens; the live run is judged apart; NITs: `_arc_spans` docstring overstates the min(), CLI import order
- 2026-11-04: verifier delta pass after merging `cld/sprint-3-tasks-60f7ab` at cb95034 (S6, PR #27) into the branch at bba237a -> PASS, 0 blockers, 592 tests; conflicts in the runner, CLI, repository and guard test kept both sides; NIT: `prefix_summaries` imports sit after `s6_synthesize`
Sub-goals:
- none yet
Changes:
- 2026-11-04: owner approved the goal and the decisions above
- 2026-11-04: live run at 7c02ce4 on `data/eval/g24-prefix.db` (backup-API copy of `g21-s5.db`, which still has 0 prefix rows and jobs), `qwen38-udiq4-hui-10k` sharing the GPU with S3-T3: `xn run --stage prefix --to 600` wrote 58 rows for the 17 arcs, exactly the D1 checkpoint set, 0 failed, 0 retries, 876 s in total; per checkpoint 15.0 s mean and median, 19.4 s max; input up to 5,089 tokens; summaries 643-1,581 characters (mean 1,056). The rerun skipped all 58 in 6 s with no new `llm_runs` row. `arc_prefix_view` at chapter 157 gives arc 5 (start 128), checkpoint 150 plus the raw tail 151-157
- 2026-11-04: owner approved marking the goal done, recorded with PR #28

### G-22: S3-T2 pipeline stage S6, macro StoryArc synthesis (done)
Plan: `docs/IMPLEMENTATION_PLAN.md` S3-T2, chat plan of 2026-11-04. Cut from `cld/sprint-3-tasks-60f7ab`; S3-T3 and S3-T4 run in parallel sessions, S3-T5 waits for all three.
Owner decisions: the lead writes the code, with no agy. Each arc's S1 micro-summaries go to `models.macro_synthesizer` through `run_compact` (compact egress, spoiler bound = the arc's end chapter, spend cap checked). The model writes the story fields only; `arc_id`, start and end chapter come from the `arcs` row. The `xn_arcs` point embeds title plus summary with BGE-M3, id uuid5 of `novel:arc:<arc_id>`; points of arc ids that no longer exist are deleted. Jobs are keyed by the arc's end chapter (OQ-05), with an input hash over span, micro-summaries, prompt, model and embedding model. One failed arc does not stop the others. The legacy `src/schemas/arc.py` stays. The live run uses `data/eval/g22-s6.db`, a copy of `g21-s5.db`, with `daily_spend_cap_usd` raised to 1.50 for that run only (the copy already holds $1.24 of today's S1 spend); `configs/config.yaml` keeps 1.00.
Done when:
- `StoryArc` is in `xn.schemas.arc` next to `BoundaryReview`, and `prompts/s6_arc_synthesize/v1.md` exists
- `xn run --novel pntt1 --stage s6 --to 600` fills `payload`, `prompt_version` and `model` on all 17 arcs and upserts 17 points into `xn_arcs`, with the spend reported from `llm_runs`
- a rerun on unchanged input makes 0 cloud calls and changes no rows; an arc whose input changed is synthesized again
- unit tests with a fake model and in-memory Qdrant cover skip and rerun, the egress and spoiler bound, a refused spend cap, one failed arc and the repository guard
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-04: deliver the S6 stage code and unit tests -> PASS, 0 blockers, 745eb9d, 0 agy tokens; NIT fixed after: `written` was printed twice by `xn run`; the live run stays open for R-2
- R-2 2026-11-04: live run of S6 on 1-600 in `data/eval/g22-s6.db` -> 1st run 11 of 17 (2 connection drops, 2 hits on the 2500-token output cap since the model reasons first, 2 output-validation failures at the 20-name `key_entities` limit), $0.105; fixed in 998ca84 (S6 caps output at 8000 tokens via a new optional `run_compact` `max_tokens`, 2 output retries, lists up to 15/30 with 8/15 asked in the prompt); 2nd run wrote 17 of 17 and 17 `xn_arcs` points, $0.133; a 3rd run skipped 17 with 0 cloud calls and identical rows. S6 spend $0.238 over 64 attempts (28 ok, the rest instant connection drops); the copy's cloud spend today is $1.479 against the one-off $1.50 cap. Summaries run 436-626 words against the 250-400 asked; 4 of 383 names are descriptions not found verbatim in the arc's summaries -> PASS, 0 blockers, cd93473, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-11-04: owner approved the goal and the decisions above, with the one-off $1.50 cap for the live run
- 2026-11-04: owner approved marking the goal done, recorded with PR #27

### G-21: S3-T1 pipeline stage S5, arc segmentation (done)
Plan: `docs/IMPLEMENTATION_PLAN.md` S3-T1, chat plan of 2026-11-04. Cut from `cld/sprint-3-tasks-60f7ab` as `cld/s3-t1-s5-segment`. First of Sprint 3; S3-T2, S3-T3 and S3-T4 wait for it, S3-T5 waits for those.
Owner decisions: the lead writes the code, with no agy. OQ-04: DP maximizes the sum of smoothed scores at the chosen boundaries minus a fixed penalty per boundary, with every arc 30-90 chapters long; the penalty is tuned on chapters 1-600 against boundary F1 (±3); the prototype's `score_threshold 0.60` and 5-year time-skip rule are dropped. OQ-05: S5 writes `chapters.arc_id` and placeholder `arcs` rows (start and end chapter, null payload) that S6 fills; novel-wide stages record `jobs` with `chapter_id = 0`, per-arc stages key by the arc's end chapter. The gold arc boundaries for 1-600 are annotated by Claude from the chapter titles and S1 micro-summaries, committed before any scoring. The live run uses `data/eval/g21-s5.db`, a copy of `g15-s4.db` (prompt v2) with S0 to chapter 600 and S1 on chapters 1-600 from the cloud model (see Changes).
Done when:
- `eval/pntt1/gold/arcs.jsonl` holds the gold arc starts for 1-600 and is committed before the first scored S5 run
- `xn run --novel pntt1 --stage s5 --to 600` writes `chapters.arc_id` and placeholder `arcs` rows, every arc 30-90 chapters long, and a rerun on unchanged input gives identical rows
- the local LLM boundary review sees micro-summaries ±10 chapters and moves no boundary more than 10 chapters
- S5 sets `arc_id` on the chapter's `xn_chunks` points in place, with no re-embedding of chunks
- boundary F1 ≥ 0.50 on chapters 1-600 (±3 chapters)
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-04: deliver the S5 stage code and unit tests -> PASS, 0 blockers, 8975374, 0 agy tokens; the live run, F1 and the gold commit stay open for R-2
- R-2 2026-11-04: switch S5 to the summary-embedding change score, widen the review to ±10, tune and run live on 1-600 -> FAIL, 1 blocker (boundary F1 0.52 against 0.80; best DP-only F1 in tuning 0.58), 1cb3796, 0 agy tokens; every other line met, reported to the owner
- 2026-11-04: verifier pass on the merged feature branch at 7c6fed9 against the 0.50 target -> PASS, 0 blockers, F1 0.516 (8 hits, 16 predicted, 15 gold), every "Done when" line met
Sub-goals:
- none yet
Changes:
- 2026-11-04: owner approved the goal and the decisions above
- 2026-11-04: owner approved running S1 on chapters 1-600 with `deepseek/deepseek-v4.1-flash-1017` (prompt v2) instead of the local model, to save about 3.4 h, as a one-off exception to A-04 that sends raw chapter text to OpenRouter, with a $3 spend cap for this run. The live run, the penalty tuning and the F1 therefore rest on cloud flags, which G-13 found set more often than local; production S1 stays local
- 2026-11-04: owner approved changing the S5 score after flags + DP reached only F1 0.20 (0.47 with a perfect ±5 review) on the cloud flags: the main signal becomes the change between BGE-M3 embeddings of the micro-summaries before and after each chapter, with the smoothed flags as a minor, tuned term; the LLM review window widens to ±10 chapters with shifts up to 10. The F1 target stays 0.80, at most 2 rounds; if it is still short, report back. The `xn_chunks` line now says "no re-embedding of chunks", since S5 itself now embeds the summaries
- 2026-11-04: owner lowered the F1 target from 0.80 to 0.50 after R-2 reached 0.52 (best tuned DP-only 0.58). S5 finds a shift about every 30 chapters while the gold arcs, annotated by Claude from the same summaries S5 embeds, run longer; the gold is a weak independent reference. Blueprint Sprint 3 acceptance criterion 1 still says 0.80 and is not met by this goal
- 2026-11-04: owner approved marking the goal done, recorded with PR #26

### G-18: S2-T5 reading progress, `xn progress` (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S2-T5. Bound resolution, `--full` and the "Phạm vi" line already exist from G-9; only the command is missing.
Owner decisions: the lead writes the code, with no agy. `xn progress <novel_id> <N>` writes `novels.reader_progress` through the existing `set_reader_progress`, and refuses N < 1, N > `total_chapters` and an unknown novel. One round.
Done when:
- After `xn progress pntt1 50`, a bare `xn ask` prints "Phạm vi: chương 1–50", and `--until` or a chapter in the question still wins
- `xn progress` refuses N < 1, N > `total_chapters` and an unknown novel with a coded error
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-03: deliver `xn progress` with its checks and tests -> PASS, 0 blockers, 061a772, 0 agy tokens; NIT: `xn progress ghost 0` reports VALIDATION_FAILED before NOVEL_NOT_FOUND
Sub-goals:
- none yet
Changes:
- 2026-11-03: owner approved the goal and the decisions above
- 2026-11-03: owner approved marking the goal done, recorded with PR #25

### G-19: S2-T6 L5 spoiler scan, regenerate and redact (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S2-T6; blueprint §3.4 L5. Depends on G-18.
Owner decisions: the lead writes the code, with no agy. D1: `spoiler_scan.py` matches the answer, without diacritics and on word bounds, against aliases with `first_chapter > N` (minus any alias_norm also known at <= N) and realm names above the max realm seen <= N. D2: the max realm is novel-wide, not per mentioned entity (a deviation from the blueprint, to avoid redacting early mentions of higher-realm side characters). D3: `pntt1.yaml` gets the PNTT realm ladder; the max seen is read at query time from `tier_or_realm` of events <= N, so S3 needs no rerun. D4: on a hit, synthesize runs once more with the terms listed as forbidden; a second hit redacts the sentence to "[đã ẩn để tránh tiết lộ]"; the retry is a graph edge with a counter in state. Two rounds: scanner, then graph wiring.
Done when:
- Unit tests show a future alias and a higher realm are caught, an alias also known at <= N is not, and an answer with no hit passes unchanged
- A leaking answer triggers exactly one regenerate with the forbidden terms in the prompt; a second leak redacts only the leaking sentence
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-03: deliver the scanner, its two guarded repository reads and the PNTT realm ladder -> PASS, 0 blockers, 3232a8f, 0 agy tokens; NITs: `test_prompts.py` updated for the filled ladder; a realm text naming two rungs counts as the higher one
- R-2 2026-11-03: wire the scan into verify, regenerate once with the leaked terms forbidden (prompt v3), redact a second leak -> PASS, 0 blockers, 51bfeb7, 0 agy tokens; NIT: three of the five new graph tests guard over-triggering and the per-turn reset rather than the retry edge
Sub-goals:
- none yet
Changes:
- 2026-11-03: owner approved the goal and the decisions above
- 2026-11-03: lead choices inside D1, recorded for review: one-syllable aliases are not scanned (they match ordinary words), and a term found in the evidence pack is allowed, since the evidence is all <= N (rule 2 of the prompt already permits it). Filling `realm_ladder` adds the ladder to future S1 prompts; `prompt_version` hashes the raw template, so no extraction goes stale
- 2026-11-03: owner approved marking the goal done, recorded with PR #25

### G-20: Sprint 2 acceptance check (done)
Plan: docs/IMPLEMENTATION_PLAN.md, "Sprint 2 Acceptance Criteria". Depends on G-18 and G-19. Live run, no new product code.
Owner decisions: run the 10 probes in `eval/pntt1/gold/spoilers.jsonl` and the 15 questions in `eval/pntt1/gold/qa.jsonl` through the real graph with cloud answers; about $0.03.
Done when:
- 0 leaks on the 10 spoiler probes, checked on the final answers
- Context recall >= 0.75 on the 15 Q&A
- factual_qa p95 latency <= 15 s
Rounds:
- R-1 2026-11-03: run the 10 spoiler probes and 15 Q&A through the full graph at cdcf999 -> FAIL on latency, 0 leaks, recall met, p95 25.5 s; $0.035, 0 agy tokens
Sub-goals:
- G-20.1: factual_qa p95 <= 15 s (done). Owner decisions: D1: p95 is measured over the 25 gold questions in one warm process (reranker loaded before timing), like the S4 API; the CLI's per-call reranker load is reported apart. D2: a connection error that fails in under 1 s is retried after 0.25 s instead of 1/2/4 s; 429, 5xx and timeouts keep the backoff. D3: OpenRouter is asked to route the DeepSeek model by lowest latency; the answer length is capped if the tail calls are long outputs. D4: the graph logs time per node for each question. Out of scope: changing models, replacing the router, the CLI cold start; the router's ~4 s goes back to the owner if D2-D3 are not enough. Two rounds: timing, retry and routing, then a live re-run after a curl check to openrouter.ai succeeds.
  Done when:
  - In a warm process, factual_qa p95 <= 15 s on the 25 gold questions, counting every attempt
  - The same run keeps 0 leaks and context recall >= 0.75
  - Each answer's run record shows time per node
  - `py -m compileall -q src` and `uv run pytest tests/unit` pass
  Rounds:
  - R-1 2026-11-03: time each node in state, retry a dropped connection after 0.25 s, route cloud calls by latency -> PASS, 0 blockers, 79b0c96, 0 agy tokens; NIT: an empty timings update is the implicit per-turn reset
  - R-2 2026-11-03: warm live re-run of the 25 gold questions at a1dcb1b -> PASS, p95 12.95 s, 0 leaks, recall 0.91; $0.059, 0 agy tokens
  Changes:
  - 2026-11-03: R-2 on a fresh copy of `data/eval/g15-s4.db`, reranker and router warmed before timing; curl to openrouter.ai 8/10 just before. All 25 questions routed to factual_qa (q07 was timeline in G-20 R-1). Per question 3.98-13.11 s, p95 12.95 s; per node: route 1.3-3.7 s, retrieve <= 0.4 s, synthesize 1.8-11.0 s (q13 11.0 s, q09 9.6 s). Successful cloud calls 4.2 s mean, 10.4 s max (6.7 s and 13.9 s before latency routing). 16 of 43 cloud attempts still dropped at once, each now costing ~0.25 s; no question failed. Leaks 0 of 10; recall 0.91 on events, 0.98 with chunks, citation hit 0.93, 0 refused. L5: q01 regenerated, then one sentence redacted for "cự hán", a descriptive alias (giant man) first seen after chapter 10, a likely false positive
  - 2026-11-03: D3's answer-length cap dropped: in G-20 R-1, cloud latency did not track output tokens (467 tokens took 12.4 s, 1,066 took 3.3 s)
  - 2026-11-03: owner approved the sub-goal and the decisions above; profile before the change (warm, no cloud): route 2-6 s (median ~4 s), retrieve ~0.4 s (17.5 s cold), cloud answer 6.7 s mean, 13.9 s max
  - 2026-11-03: owner approved marking the sub-goal done, recorded with PR #25
Changes:
- 2026-11-03: owner approved the goal and the decisions above
- 2026-11-03: R-1 on a copy of `data/eval/g15-s4.db` (ledger and `xn_chunks` for chapters 1-100), `qwen38-udiq4-hui-10k` resident, `deepseek/deepseek-v4.1-flash-1017`, prompt v3. Leaks: 0 of 10 probes on the final answers; the L5 scan regenerated s09 ("khu vật thuật") and q03 ("chiếc bình thần bí", a descriptive alias), and both retries came back clean; nothing was redacted. Context recall 0.84 on ledger events (the S1-T9 metric), 0.91 with chunks; q01 failed with a connection error and q13 missed its chapters. Latency: 23 factual_qa runs, p95 25.5 s. 26 of 53 cloud attempts failed in under 0.2 s with "Connection error", and curl to openrouter.ai failed TLS setup (exit 35) on 6 of 8 tries right after, so the network inflated the run. Even without backoff, s04 and s07 take about 16 s, and s01 (26.4 s) includes the ~14 s reranker cold load; runs with no failed attempt took 4.6-14.3 s. Successful cloud calls averaged 6.7 s, max 13.9 s
- 2026-11-03: G-20.1 R-2 met all three lines in one warm run: 0 leaks, recall 0.91, p95 12.95 s
- 2026-11-03: owner approved marking the goal done, recorded with PR #25

### G-17: S2-T4 query graph, factual_qa and compare (done)
Plan: docs/IMPLEMENTATION_PLAN.md, S2-T4. Depends on G-9 and G-16.
Owner decisions: the lead writes the code, with no agy. D1: `factual_qa` passes the gate when the top chunk's rerank score is >= `retrieval.min_rerank_score` (0.3), with no entity required; events are still added when an entity resolves. D2: `compare` gives each entity its events at half the budget plus up to 4 chunks filtered by that entity (8 at most); its gate needs >= 2 resolved entities with >= 3 events each (OQ-07), and chunks help but are not required (a deviation from the blueprint's 0.3 rule for compare). D3: a `compare` decision with fewer than 2 mentions becomes `deep_dive` (1 mention) or `factual_qa` (none) in code. D4: chunks reach the cloud model as `chunk` items (<= 450 tokens, <= 8), with a new `synthesize_compact/v2` prompt; v1 stays. D5: `verify` allows chapters from events and chunks, all <= N. D6: `qdrant.py` and `retriever.py` gain an optional entity filter, plus the prompt file and config lines; `workflow.py` changes only if needed. Two rounds: gate, route and retrieve, then synthesize, verify, prompt and live check.
Done when:
- `factual_qa` refuses with "Không đủ dữ liệu đến chương N" when the top rerank score is below 0.3, and answers from chunks otherwise
- `compare` refuses with fewer than 2 resolved entities, and every chunk it uses carries its entity, is within N and comes from the novel
- The cloud payload carries at most 8 chunks and `llm_runs.payload_chapters` lists their chapters
- `verify` keeps chunk citations and strips any above N
- A live check runs 3 `factual_qa` and 2 `compare` questions on pntt1, chapters 1-100
- `py -m compileall -q src` and `uv run pytest tests/unit` pass
Rounds:
- R-1 2026-11-03: deliver the factual_qa rerank gate, compare's per-entity chunks and gate, and the compare fallback in route -> PASS, 0 blockers, f833ea3, 0 agy tokens
- R-2 2026-11-03: send chunks to the cloud model with the v2 prompt, accept their citations in verify, run the live check -> PASS on code, 0 blockers, 71b9f09, 0 agy tokens; the live check stopped before synthesize (OPENROUTER_API_KEY not set in the session), so that line stays open
Sub-goals:
- none yet
Changes:
- 2026-11-03: owner approved the goal and the decisions above
- 2026-11-03: live check route to gate on a copy of `data/bench/e2e/xn.db` (ledger chapters 2-15) with `xn_chunks` and `qwen38-udiq4-hui-10k` resident. Three factual_qa at N=100: 8 chunks each, top rerank 0.996/0.994/0.989, gate passes. Compare Hàn Lập vs Trương Thiết at N=15: 6 chunks tagged 7/13 in turns, 20 events, gate passes. Compare Hàn Lập vs Mặc đại phu at N=15: refused, Mặc đại phu has 2 events. Warm route-to-gate 2-3 s, first query ~13 s (reranker load); VRAM 14,184 of 16,303 MiB. The router added junk mentions on two questions (out of scope). Synthesize not reached: OPENROUTER_API_KEY not set in the session; owner is setting it
- 2026-11-03: live check completed with the key set, the same 5 questions through `xn ask` on `deepseek/deepseek-v4.1-flash-1017`, compact egress. The three factual_qa answers cite chunk-only chapters (67/74/75, 23/26, 65/68/69), and `payload_chapters` lists them beside the ledger chapters; all citations are <= N. Compare Hàn Lập vs Trương Thiết answered per aspect, grounding 42%; Hàn Lập vs Mặc đại phu refused with no cloud call. Grounding 70/60/60/42%. 4 successful calls, $0.0062 in total, 4.0-5.7k input tokens; 3 attempts failed with "Connection error" and the retries recovered them
- 2026-11-03: verifier pass on the whole branch at 5624d69 -> PASS, every "Done when" line met; this closes the live-check line left open in R-2. Compare ran at N=15 only, where the bench ledger has events; factual_qa used the full 1-100 chunk index
- 2026-11-03: owner approved marking the goal done, recorded with PR #24

### G-16: S2-T3 hybrid retrieval and reranker (done)
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
- 2026-11-03: verifier pass at 89eba0a -> PASS, every "Done when" line met; owner approved marking the goal done, recorded with PR #23

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

### G-14: test DeepSeek's own API, one chapter against two chapters per call (done)
Plan: chat plan of 2026-11-02. Prompt v2, every fixed pair (1-2, 3-4, ...) in chapters 1-100 that holds a gold chapter. Cut from `cld/g13-cloud-bench`.
Owner decisions: raw chapter text may go directly to DeepSeek for this test only, as one exception to A-04; production S1 stays local. Model `deepseek-flash` with thinking off, concurrency 10, and the owner monitors cost. Two-chapter mode counts as better only if name recall and precision do not drop and at least one flag score rises. The lead writes the code, with no agy. S2 and S3 are out of scope.
Done when:
- One-chapter and two-chapter calls both finish through DeepSeek directly, on the same chapters, using prompt v2 plus only the pairing instructions
- Two-chapter mode gives one full result per chapter, scored by the same gold, and drops and counts any event whose quote is not in its assigned chapter
- The report compares both modes with the OpenRouter and local results on names, categories, events, flags, misattributed events, tokens, cache hits, time and cost, and gives the verdict by the rule above
- `eval/`, `prompts/`, production config and production S1 are unchanged
- `py -m compileall -q src` and `uv run pytest tests/unit` pass, and the verifier passes
Rounds:
- R-1 2026-11-02: deliver the DeepSeek provider and two-chapter mode -> PASS, 0 blockers, 02e28d1, 0 agy tokens
- R-2 2026-11-02: run both modes and report the numbers -> PASS, 0 blockers, 9b446bb, 0 agy tokens
Sub-goals:
- none yet
Changes:
- 2026-11-02: owner approved the goal and the decisions above
- 2026-11-02: verifier pass at 9b446bb, verdict: two-chapter calls did not beat one-chapter calls, goal marked done

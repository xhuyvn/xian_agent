# S2 adjudicator benchmark: `qwen3.5_12k:q8_mtp` against `qwen3.8:udiq4_hui`

Date: 2026-11-08. Goal: G-31.

**qwen3.5 runs the S2 adjudicator 8 times faster per call (0.80 s against 6.39 s) with the same gold scores: alias F1 0.667, Q&A citation hit rate 1.00, 0 spoiler leaks. S2 on chapters 1-200 took 2.4 min against 19.0 min. The two models resolve 33 of 655 aliases differently, and neither is clearly better on those.** The production config is unchanged; a switch is the owner's decision.

## Why S2 was measured

The G-29 full run of pntt1 reached S2 at about 06:54 UTC on 2026-11-08 and was stopped after chapter 664 at about 09:18 UTC. S2 time is almost all model time: the 1,601 adjudication calls of those 664 chapters add up to 8,615 s of model latency against about 8,640 s of wall time. Calls per chapter grow as the alias table grows, from 0.4 (chapters 1-100) to 3.6 (chapters 401-664). S2 runs chapters in order, since each chapter's matches depend on the ones before, so it cannot be split across workers like S1. The model is the lever.

## Setup

- Two copies of `data/xn.db`, taken after the run was stopped (S0 and S1 done for all 2,468 chapters), in `data/bench/g31-q38/g31.db` and `data/bench/g31-q35/g31.db`. The live database was only read.
- Two config copies of the G-29 run config (`data/runs/g29-configs/`), in `data/bench/g31-*/configs/`. The qwen3.5 copy adds `models.s2_adjudicator` for `qwen3.5_12k:q8_mtp` (temperature 0.1, native output, thinking off). In both copies, `local_extractor` stays on `qwen3.8:udiq4_hui`, so the router in the gold report is the same model in both runs. Both copies point Qdrant at their own collections (`g31q38_chunks`/`g31q38_arcs` and `g31q35_chunks`/`g31q35_arcs`), so the live `xn_chunks` and `xn_arcs` are not touched.
- S2 was rebuilt (`--rebuild`) on chapters 1-200, followed by S3 on chapters 1-200 and S4 on chapters 1-100 (needed by gold Q&A), then the gold report. One run per model, one after the other, on the same GPU (16 GB). The adjudicate prompt is v1 in both runs.

## Speed

From `llm_runs` (stage `s2`) and the S2 job times in each benchmark database:

| S2, chapters 1-200 | qwen3.8 (`qwen38-udiq4-hui-10k`) | qwen3.5 (`qwen3.5_12k:q8_mtp`) |
|---|---|---|
| Wall time | 19.0 min (09:23:25 to 09:42:23 UTC) | **2.4 min** (09:45:29 to 09:47:54 UTC) |
| Adjudication calls | 188 | 186 |
| Model time | 1,201.5 s | 148.5 s |
| Mean latency per call | 6.39 s | **0.80 s** |
| Calls / model time, chapters 1-100 | 50 / 273.0 s | 49 / 50.8 s |
| Calls / model time, chapters 101-200 | 138 / 928.5 s | 137 / 97.7 s |
| Wall time, chapter 1 to 100 and 100 to 200 (job times) | 204 s and 931 s | 43 s and 98 s |
| Mean output tokens per call | 66.7 | 60.7 |
| Failed calls, retries | 0, 0 | 0, 0 |

qwen3.8 is a 27.3B model of 16.8 GB, and Ollama held 12.9 GB of it in VRAM after the run, so part of it runs on the CPU. That explains most of the gap. qwen3.5 is the model S1 ran on in G-29 at about 14.1 GB.

**Projection for the rest of pntt1 S2 (chapters 665-2,468, 1,804 chapters).** On qwen3.8 the live run spent 5,262 s of model time on chapters 401-664, about 19.9 s/chapter, and the rate was still rising. At that rate, the rest takes at least 10 h on qwen3.8. At the measured 8.0 times per-call speed-up, the same calls take at least 1.25 h on qwen3.5. Both are lower bounds, since calls per chapter keep growing.

## Quality

Gold report (`tests/eval -m gold`, 20 gold chapters in 1-100, 15 Q&A questions, 10 spoiler probes), after S4:

| Gold, chapters 1-100 | qwen3.8 | qwen3.5 |
|---|---|---|
| Alias F1 (precision / recall) | 0.644 (1.00 / 0.475) | 0.667 (1.00 / 0.500) |
| Q&A citation hit rate / context recall | 1.00 / 0.978 | 1.00 / 0.978 |
| Q&A refused | 0 | 0 |
| Spoiler leaks | 0 of 10 | 0 of 10 |

The one alias difference is "Ngự phong quyết / Ngự phong thuật", missed by qwen3.8 in this run. In the first gold run of the same qwen3.8 database (before S4), qwen3.8 scored 0.667 too, so alias F1 moves by one pair between runs, as G-27.1 also found. Every remaining alias miss is a recall miss of a title form: for example "Hàn sư đệ", "Lệ sư huynh", "Vương môn chủ" and "Cổ bang chủ" are not linked to the person in either run. Neither model makes a false merge on the gold pairs.

**Resolution differences beyond the gold set.** Comparing alias to canonical entity on all 655 aliases of chapters 1-200: 33 aliases resolve to a different entity (qwen3.8 has 575 entities, qwen3.5 568). The lead's reading of the 33, not checked against a gold set:

- qwen3.8 better, about 10: qwen3.5 merges "Kim tủy hoàn" into "Trừu tủy hoàn", "Băng trùy thuật" into "Băng đống thuật", "Thanh Văn đạo sĩ" into "Tùng Văn", the place "Thiên vụ đài" into the event "Thăng Tiên đại hội", generic "phù lục" into "Kim Quang Tráo", "kim quang" and "phù bảo kim quang chuyên" into generic "phù bảo", "Trường Xuân công khẩu quyết" into generic "khẩu quyết", and "Đại hỏa cầu phù lục" into "hỏa cầu".
- qwen3.5 better, about 6: it links "bình tử" to "Thần bí bình tử", "kim nhận" to "Kim phù tử mẫu nhận", "phệ hồn đại pháp" to "Thất quỷ phệ hồn", "chưởng môn" (chapter 150) to "Chung Chưởng môn", and it keeps "Ngũ phu nhân" apart from "Nghiêm phu nhân" and "Thái Nam tiểu hội" apart from "Thái Nam cốc".
- The rest are spelling or wording variants of the same canonical name ("Mã phó môn chủ" / "Mã phó bang chủ", "Phượng Vũ" / "Mặc Phượng Vũ") or unclear.

The qwen3.5 errors are mostly a generic word merged into a named thing, or two names that share only the type word (hoàn, thuật). The fuzzy matcher uses `token_set_ratio`, which scores 100 whenever one name's words are a subset of the other's, so these pairs always reach the adjudicator.

## Caveats

- One run per model. Run-to-run variation of the speed is not measured; the gold alias F1 moved by one pair between two gold runs of the same database.
- Chapters 1-200 only. Calls per chapter are much higher later (3.6 at 401-664), so the projection extrapolates per-call latency, not per-chapter time.
- The 33-alias reading is the lead's judgment from names and chapter numbers, not a labelled set.
- S2 does not record the model in its input hash, so a resumed run can switch the adjudicator and keep the chapters already done (unit test `test_switching_only_the_s2_model_does_not_make_s2_stale`). A new adjudicate prompt version does make S2 stale and needs `--rebuild`.
- The bench Qdrant collections `g31q38_*` and `g31q35_*` hold chapters 1-100 of pntt1 and can be deleted after review.

## Code

`models.s2_adjudicator` (optional, same shape as `local_extractor`): when set, `run_s2` and the gold report's adjudicator use it and S2 records its serving name; without it nothing changes. The router, S5 and prefix summaries stay on `local_extractor` (source-scan test). Tests: `tests/unit/test_s2_model_split.py`.

## Commands

With `<data>` = `E:/Workspace/projects/xian_agent/data`, and `<m>` = `q38`, then `q35`. Database copies were made with the SQLite backup API from `<data>/xn.db` opened read only.

```bash
uv run xn run --novel pntt1 --stage s2 --to 200 --rebuild --db <data>/bench/g31-<m>/g31.db --configs-dir <data>/bench/g31-<m>/configs
```

```bash
uv run xn run --novel pntt1 --stage s3 --to 200 --db <data>/bench/g31-<m>/g31.db --configs-dir <data>/bench/g31-<m>/configs
```

```bash
uv run xn run --novel pntt1 --stage s4 --to 100 --db <data>/bench/g31-<m>/g31.db --configs-dir <data>/bench/g31-<m>/configs
```

```bash
uv run pytest tests/eval -m gold --novel pntt1 --gold-db=<data>/bench/g31-<m>/g31.db --gold-report=<data>/bench/g31-<m>/g31-report-qa.json --gold-configs-dir=<data>/bench/g31-<m>/configs
```

Before S4, all other Ollama models were unloaded (`keep_alive: 0`). Logs: `g31.log`, `gold.log` and `gold2.log` in each `<data>/bench/g31-<m>/`. The first gold reports (before S4) are `g31-report.json`; their Q&A refused 14 of 15 questions because the bench had no chunks yet, and only their alias and spoiler numbers are used above.

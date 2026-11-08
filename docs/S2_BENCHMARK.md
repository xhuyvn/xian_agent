# S2 adjudicator benchmark: `qwen3.5_12k:q8_mtp` against `qwen3.8:udiq4_hui`

Date: 2026-11-08. Goal: G-31.

**qwen3.5 runs the S2 adjudicator 8 times faster per call (0.80 s against 6.36 s) with the same gold scores: alias F1 0.667, Q&A citation hit rate 1.00, 0 spoiler leaks. S2 on chapters 1-200 took 2.4 min against 19.0 min. The two models resolve 33 of 616 distinct aliases differently, and neither is clearly better on those.** The production config is unchanged; a switch is the owner's decision.

## Why S2 was measured

The G-29 full run of pntt1 reached S2 at about 06:54 UTC on 2026-11-08 and was stopped after chapter 664 at about 09:18 UTC. S2 time is almost all model time: the 1,601 adjudication calls of those 664 chapters add up to 8,615 s of model latency against about 8,640 s of wall time. Calls per chapter grow as the alias table grows, from 0.4 (chapters 1-100) to 3.6 (chapters 401-664). S2 runs chapters in order, since each chapter's matches depend on the ones before, so it cannot be split across workers like S1. The model is the lever.

## Setup

- Two copies of `data/xn.db`, taken after the run was stopped (S0 and S1 done for all 2,468 chapters), in `data/bench/g31-q38/g31.db` and `data/bench/g31-q35/g31.db`. The live database was only read.
- Two config copies of the G-29 run config (`data/runs/g29-configs/`), in `data/bench/g31-*/configs/`. The qwen3.5 copy adds `models.s2_adjudicator` for `qwen3.5_12k:q8_mtp` (temperature 0.1, native output, thinking off). In both copies, `local_extractor` stays on `qwen3.8:udiq4_hui`, so the router in the gold report is the same model in both runs. Both copies point Qdrant at their own collections (`g31q38_chunks`/`g31q38_arcs` and `g31q35_chunks`/`g31q35_arcs`), so the live `xn_chunks` and `xn_arcs` are not touched.
- S2 was rebuilt (`--rebuild`) on chapters 1-200, followed by S3 on chapters 1-200 and S4 on chapters 1-100 (needed by gold Q&A), then the gold report. One run per model, one after the other, on the same GPU (16 GB). The adjudicate prompt is v1 in both runs.

## Speed

From `llm_runs` (stage `s2`, limited to each S2 run's time window, since the gold report that ran afterwards also logs adjudication calls under stage `s2`) and the S2 job times in each benchmark database:

| S2, chapters 1-200 | qwen3.8 (`qwen38-udiq4-hui-10k`) | qwen3.5 (`qwen3.5_12k:q8_mtp`) |
|---|---|---|
| Wall time | 19.0 min (09:23:25 to 09:42:23 UTC) | **2.4 min** (09:45:29 to 09:47:54 UTC) |
| Adjudication calls | 178 | 176 |
| Model time | 1,132.2 s | 140.9 s |
| Mean latency per call | 6.36 s | **0.80 s** |
| Calls / model time, chapters 1-100 | 40 / 203.7 s | 39 / 43.2 s |
| Calls / model time, chapters 101-200 | 138 / 928.5 s | 137 / 97.7 s |
| Wall time, chapter 1 to 100 and 100 to 200 (job times) | 204 s and 931 s | 43 s and 98 s |
| Mean output tokens per call | 67.0 | 60.8 |
| Failed calls, retries | 0, 0 | 0, 0 |

qwen3.8 is a 27.3B model of 16.8 GB, and Ollama held 12.9 GB of it in VRAM after the run, so part of it runs on the CPU. That explains most of the gap. qwen3.5 is the model S1 ran on in G-29 at about 14.1 GB.

**Projection for the rest of pntt1 S2 (chapters 665-2,468, 1,804 chapters).** On qwen3.8 the live run spent 5,262 s of model time on chapters 401-664, about 19.9 s/chapter, and the rate was still rising. At that rate, the rest takes at least 10 h on qwen3.8. At the measured 7.9 times per-call speed-up (6.36 s / 0.80 s), the same calls take at least 1.25 h on qwen3.5. Both are lower bounds, since calls per chapter keep growing.

## Quality

Gold report (`tests/eval -m gold`, 20 gold chapters in 1-100, 15 Q&A questions, 10 spoiler probes), after S4:

| Gold, chapters 1-100 | qwen3.8 | qwen3.5 |
|---|---|---|
| Alias F1 (precision / recall) | 0.644 (1.00 / 0.475) | 0.667 (1.00 / 0.500) |
| Q&A citation hit rate / context recall | 1.00 / 0.978 | 1.00 / 0.978 |
| Q&A refused | 0 | 0 |
| Spoiler leaks | 0 of 10 | 0 of 10 |

The one alias difference is "Ngự phong quyết / Ngự phong thuật", missed by qwen3.8 in this run. In the first gold run of the same qwen3.8 database (before S4), qwen3.8 scored 0.667 too, so alias F1 moves by one pair between runs, as G-27.1 also found. Every remaining alias miss is a recall miss of a title form: for example "Hàn sư đệ", "Lệ sư huynh", "Vương môn chủ" and "Cổ bang chủ" are not linked to the person in either run. Neither model makes a false merge on the gold pairs.

**Resolution differences beyond the gold set.** Comparing alias to canonical entity on all 616 distinct aliases of chapters 1-200 (655 alias rows): 33 aliases resolve to a different entity (qwen3.8 has 575 entities, qwen3.5 568). The lead's reading of the 33, not checked against a gold set:

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

Speed figures: `llm_runs` rows with `stage = 's2'` and `created_at` from 2026-11-08T09:23 to 09:42:24 UTC (qwen3.8) and from 09:45 to 09:47:55 UTC (qwen3.5); the database copies also hold the live run's older rows, and the gold report's 10 adjudication calls per run come after the window. Before S4, all other Ollama models were unloaded (`keep_alive: 0`). Logs: `g31.log`, `gold.log` and `gold2.log` in each `<data>/bench/g31-<m>/`. The first gold reports (before S4) are `g31-report.json`; their Q&A refused 14 of 15 questions because the bench had no chunks yet, and only their alias and spoiler numbers are used above.

## G-31.1: better S2 merges on qwen3.5

Date: 2026-11-08. Goal: G-31.1.

**qwen3.5 with two new opt-in rules and the unchanged prompt v1 raises gold alias F1 from 0.667 to 0.806 (precision 1.00, recall 0.675), keeps Q&A at 1.00 and 0 spoiler leaks, and runs at 0.71 s/call. On held-out adjudication pairs it is more accurate than production qwen3.8 (0.866 against 0.817) but makes 6 false merges against 4, which misses the Done-when line "no more false merges".** Production config is unchanged.

### Pair set

`eval/pntt1/s2_pairs/` (README there): 179 pairs that S2 sent to the adjudicator on chapters 1-200 (qwen3.5, prompt v1), and 30 title-form pairs harvested with the title rule and no model calls. Labels by the lead, policy in the README; 11 + 3 pairs are `unclear` and not scored. Split by chapter parity: dev (odd) 98 + 15 scored pairs, held-out (even) 70 + 12. The labeller saw the recorded qwen3.5 decisions of the first set, so labels are not blind.

### What was tried on dev

Replays send the same pairs to each setup (`tools/replay.py`). One run per row unless noted; repeat runs moved by 0 to 2 pairs.

| Dev setup (98 pairs) | Accuracy | False merges | Missed |
|---|---|---|---|
| qwen3.8, prompt v1 (production) | 0.857 | 5 | 9 |
| qwen3.5, v1 | 0.898 | 5 | 5 |
| qwen3.5, v2 (naming rules, examples) | 0.878 | 7 | 5 |
| qwen3.5, v2, generic rule on mention and candidate | 0.888 / 0.888 (two runs) | 5 | 6 |
| same, second vote on "same" | 0.898 | 4 | 6 |
| same, reason written before the decision | 0.878 | 6 | 6 |
| qwen3.5, v3 (v2 + compare-then-decide reason) | 0.796 | 3 | 17 |
| qwen3.5, v2, generic rule on mention only | 0.908 | 6 | 3 |
| qwen3.5, v2b (v2 without the "name with description" clause), mention rule | 0.878 / 0.878 | 5 | 7 |
| **qwen3.5, v1, generic rule on mention only** | **0.918 / 0.918** | 2 / 3 | 6 / 5 |
| qwen3.5, v1, mention rule, second vote | 0.888 | 3 | 8 |

Prompt v2 made qwen3.5 merge descriptive names that are different things (two wooden boxes, Quy tức công and Liễm tức công, two token plates), so it was dropped; the tried prompts are kept in `tools/prompts/`. The candidate-side generic rule (drop candidates known only by generic words) blocked four true matches where a specific object was first named generically ("đạo phù lục", "công pháp cơ sở", "hồ lô", "lão đầu"), so only the mention-side rule stays.

Title forms on dev (15 pairs): asking about each same-surname candidate and taking the first "same" gave 2 false merges ("Ngô sư huynh" to Ngô Cửu Chỉ and Ngô Minh Thụy; qwen3.8 made 3). Merging only when exactly one candidate is "same" gave 15 of 15.

### Frozen setup

- Adjudicate prompt v1, `models.s2_adjudicator` = `qwen3.5_12k:q8_mtp`.
- `s2_rules.generic_terms`: a mention that is only a generic word (51 terms such as "phù lục", "khẩu quyết", "chưởng môn") never merges into another entity.
- `s2_rules.titles`: a character mention "<surname> <title>" (47 title words such as "sư huynh", "môn chủ", "đại phu", plus "thị", "gia", "lão giả", "phủ") is asked against up to 3 named characters of that surname, most recently seen first, and merges only on exactly one "same".
- Both lists are in `data/bench/g311/configs/config.yaml`; without an `s2_rules` block S2 runs as before. A set block is part of the S2 input hash, so turning it on makes S2 stale.

### Held-out (one run, 82 pairs)

| Held-out setup | Accuracy | False merges | Missed |
|---|---|---|---|
| qwen3.8, v1, no rules (production) | 0.817 | 4 (p084, p146, p167, p171) | 11 |
| qwen3.5, v1, no rules | 0.854 | 6 | 6 |
| **qwen3.5, v1, rules** | **0.866** | **6** (p070, p110, p146, p148, p168, t023) | **5** |

Without rules the title pairs never reach the adjudicator, so they count as "different" for the first two rows. The qwen3.5 false merges are a place merged into an event (Thiên vụ đài / Thăng Tiên đại hội), two spells that differ in one syllable (Băng trùy / Băng đống thuật, Ẩn hình / Ngụy nặc thuật), a talisman merged into a spell, a description merged into a generic candidate, and one title form (Vương sư đệ / Vương Tuyệt Sở).

### Full rebuild and gold report

S2 rebuilt on chapters 1-200 of a fresh copy (`data/bench/g311-final/`) with the frozen setup, then S3 (1-200), S4 (1-100, collections `g311_chunks`/`g311_arcs`) and the gold report:

| Gold, chapters 1-100 | qwen3.5 + rules (G-31.1) | qwen3.5 (G-31) | qwen3.8 (G-31) |
|---|---|---|---|
| Alias F1 (precision / recall) | **0.806** (1.00 / 0.675) | 0.667 (1.00 / 0.50) | 0.644 (1.00 / 0.475) |
| Q&A citation hit rate / context recall | 1.00 / 0.978 | 1.00 / 0.978 | 1.00 / 0.978 |
| Spoiler leaks | 0 of 10 | 0 of 10 | 0 of 10 |
| S2 adjudication calls, s/call | 201, 0.71 | 176, 0.80 | 178, 6.36 |

S2 took 2.5 min (10:55:15 to 10:57:42 UTC). The title rule adds the gold matches "Lệ Phi Vũ / Lệ sư huynh", "Lệ Phi Vũ / Lệ đại đường chủ", "Cổ Thiên Long / Cổ bang chủ", "Hàn Lập / Hàn sư huynh", "Hàn Lập / Hàn đại phu", "Hàn Lập / Hàn tiểu đại phu" and "Hắc Hùng / Hắc gia", with no gold false merge. Still missed: title forms the adjudicator judged different ("Hàn sư đệ", "Hàn thần y", "Vương môn chủ", "Vương đại môn chủ", "Cổ đại bang chủ", "Trương cô nương"), forms whose candidate is itself a title form ("Mặc đại phu / Mặc lão"), nicknames ("Anh ngố", "cự hán", "cái bình") and hidden identities ("Kim Quang thượng nhân / Chu Nho", "Mặc Cư Nhân").

### Caveats

- One held-out run; the replays moved by up to 2 pairs between runs on dev.
- Labels are the lead's and not blind to the qwen3.5 v1 decisions.
- The generic and title lists were written from the G-31 differences and the title pairs of both halves, so they are not held-out-clean; the prompt choice and the two rule variants were chosen on dev only.
- Chapters 1-200 only.

### Commands

Tools are in `eval/pntt1/s2_pairs/tools/` and use the paths of this machine. Pairs: `record_pairs.py` (copy of `data/xn.db`, S2 rebuild 1-200 with qwen3.5, records each adjudication), `harvest_titles.py`. Replays, from the repo root:

```bash
uv run py -I eval/pntt1/s2_pairs/tools/replay.py --configs <data>/bench/g311/configs --prompt v1 --rules --mention-only --split held_out --out <data>/bench/g311/replay/final-q35-pairs-ho.jsonl
```

```bash
uv run py -I eval/pntt1/s2_pairs/tools/replay.py --configs <data>/bench/g311/configs --prompt v1 --rules --mention-only --split held_out --pairs eval/pntt1/s2_pairs/title_pairs.jsonl --out <data>/bench/g311/replay/final-q35-titles-ho.jsonl
```

```bash
uv run py -I eval/pntt1/s2_pairs/tools/replay.py --configs <data>/bench/g31-q38/configs --prompt v1 --out <data>/bench/g311/replay/q38-v1.jsonl
```

Title pairs are scored per mention: the merge counts only when exactly one candidate is "same". The full rebuild is `tools/final_rebuild.sh` (S2, S3, S4 and the gold report, as in G-31). All replay outputs are in `<data>/bench/g311/replay/`.

### G-31.1 R-2: guards against false merges, blind held-out

Date: 2026-11-08. The owner asked to keep working on false merges after R-1.

**Result: R-2 fails the held-out line, and the held-out reverses R-1's picture. On 148 blind-labelled pairs from chapters 201-400, qwen3.5 with all rules makes 15 false merges against 7 for production qwen3.8 (accuracy 0.878 against 0.919). The guards that removed 7 of 10 false merges on dev did not carry over: chapters 201-400 bring other error shapes.**

What was tried, on dev (all 195 R-1 pairs):
- **Confidence threshold.** Token logprobs give P(same) for every call (`tools/replay_prob.py`). qwen3.5's false merges sit at 0.72-0.998, mostly above 0.96 like its true matches, so a threshold that removes them removes many true matches first (0.98: 5 false merges, 32 missed, against 9 and 14 at 0.5). Not used.
- **Guards** (config `s2_rules`, code `S2Rules.blocks`, 16 unit tests), each ruling a candidate out before the adjudicator: a type clash (place against event, talisman against technique, by the name's type word or a marker word such as "phù"); a distinct name word (same type word and length, a differing word at edit distance >= 4 without diacritics); a place inside the candidate place (two or more extra words); and for characters, the same surname with titles of different ranks. On dev with the generic and title rules: false merges 10 -> 3, missed 14 -> 16, accuracy 0.877 -> 0.903.

Held-out 2 (`eval/pntt1/s2_pairs/heldout2_pairs.jsonl`): a fixed random sample of 160 of the 481 pairs S2 sent to the adjudicator on chapters 201-400 (seed 31), labelled without seeing any model decision; 22 same, 126 different, 12 unclear. One run each:

| Held-out 2 (148 pairs) | Accuracy | False merges | Missed |
|---|---|---|---|
| qwen3.8, v1, no rules (production) | 0.919 | 7 | 5 |
| qwen3.5, v1, all rules | 0.878 | 15 | 3 |

(qwen3.8 counts title-rule pairs as "different", since without rules they never reach it; counted as answered, it has 9 false merges and 4 missed, accuracy 0.912.) Most qwen3.5 false merges on held-out 2 are a name that extends or shortens a generic or near-generic candidate: "đan dược màu phấn hồng" / "đan dược", "phù lục màu xanh" / "phù lục", "Khôi lỗi thú" / "đôi khôi lỗi thú", "Huyết Thị" / "Huyết Thị đại nhân", "Tứ đại huyết thị" / "huyết thị", "Linh thuật" / "Tụ linh thuật", "Hoa Phương" / "phương". qwen3.8 is more conservative on these.

**Exploratory, computed after the held-out was scored (not a tuned or confirmed result):** a hybrid where qwen3.5 with the rules decides first and only its "same" answers go to qwen3.8 for confirmation would have made 5 false merges and 6 missed on held-out 2 (accuracy 0.926), calling qwen3.8 on 34 of 148 pairs; on the R-1 pairs (no rules) it gives 5 false merges but 25 missed (accuracy 0.821). With qwen3.8 on about a quarter of calls, mean latency would be about 2.3 s/call against 6.4. Confirming it needs fresh held-out pairs.

Commands: `tools/record_heldout2.py` (S2 rebuild 1-400 with the R-1 setup, records pairs from chapter 201), `tools/replay.py --configs <data>/bench/g311r2/configs --prompt v1 --rules --mention-only --pairs eval/pntt1/s2_pairs/heldout2_pairs.jsonl` and the same with `--configs <data>/bench/g31-q38/configs` and no `--rules` for qwen3.8. Outputs in `<data>/bench/g311r2/`.

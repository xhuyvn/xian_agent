# S1-T9 gold evaluation

Date: 2026-11-01. Goal: G-10.

**Extraction recall is now 0.98, above Sprint 1's 0.80, with precision 0.88.** Prompt v1 scored 0.68, so sub-goal G-10.1 wrote prompt v2. On the 10 held-out chapters v2 scored recall 1.00 and precision 0.90, with the caveats in "G-10.1 prompt v2". The gold did not change. Everything else measured here has no Sprint 1 threshold.

The gold was drafted by Claude from the chapter text, not by a person (`annotator: claude` in `eval/pntt1/gold/meta.json`), and committed before any scoring. It covers chapters 1-100 only. A person's spot-check would make these numbers firmer.

## Results

`qwen3.8:udiq4_hui` through the real S0-S3 code on chapters 1-100, then `pytest tests/eval -m gold`. Thresholds in brackets come from blueprint §3.8 and apply in Sprint 4, except recall at 0.80, which is Sprint 1's.

| Metric | Prompt v1 | Prompt v2 | Threshold |
|---|---|---|---|
| Entity recall | 0.68 (69 of 102) | **0.98** (100 of 102) | 0.80 now, 0.85 in S4 |
| Entity precision | 0.86 (11 false names) | 0.88 (14 false names) | 0.90 in S4 |
| Category accuracy | 0.92 | 0.91 | none |
| Event-type accuracy | 0.87 | 0.90 | 0.75 in S4 |
| Flag F1, `realm_breakthrough` | 1.00, on 1 gold chapter | 1.00 | 0.80 in S4 |
| Flag F1, `major_conflict` | 0.86 | 0.92 | 0.80 in S4 |
| Flag F1, `location_change` | 0.50, on 2 gold chapters | 0.33 | 0.80 in S4 |
| Alias pairwise F1 | 0.67 (precision 1.00, recall 0.50) | 0.64 (precision 1.00, recall 0.48) | 0.90 in S4 |
| Q&A citation hit rate, evidence only | 0.80 (12 of 15, none refused) | 0.93 (14 of 15, none refused) | 0.80 in S4 |
| Q&A context recall, evidence only | 0.72 | 0.91 | 0.75 in S2 |
| Spoiler leaks, evidence and suggestions | 0 of 10 probes | 0 of 10 probes | 0 |
| Evidence above the bound | 0 | 0 | 0 |
| S1 s/chapter | 20.4 | 34.7 | none |

Q&A and spoiler scores cover what the graph retrieves and shows before any cloud call, not the written answers. The spoiler result does not replace the Sprint 2 check on real answers. The Sprint 1 recheck and the live deep-dive below were run on v1, and were not repeated on v2. RAGAS and boundary F1 were not run.

## Why recall was low on prompt v1

- **The model writes fewer events than the gold has entities.** The mean is 4.0 events per chapter, with a range of 1-8 and none empty, against 5.1 gold entities per chapter. This fits the misses but does not prove the cause.
- **It misses most on crowded chapters.** Chapter 84 lost 5 of 8 entities: its three events are all techniques, and no character is named. Chapters 69 and 95 lost 4 of 7 and 4 of 8. Those three plus chapter 79 hold 16 of the 33 misses, and chapter 27 holds 6.
- **The protagonist is not always named.** "Hàn Lập" is missing from chapters 53, 58 and 79.
- **Malformed names are a smaller cause.** In chapter 27 four pills arrive as one comma-joined name, and "phi kiếm (ánh sáng mờ mờ" is cut mid-bracket. Counting a gold name inside any extracted name lifts recall only to 0.73, so better name cleaning alone will not reach 0.80.
- **Not yet known:** how many good events S1's evidence check dropped. Dropped events are logged, not stored, so this run cannot show it.

## Other findings on prompt v1

- **Alias recall is 0.50 because `seed_aliases` is empty.** All 20 wrong pairs are titles, nicknames and descriptions S2 can't guess, such as Hàn thần y, Mặc lão, Chu Nho and cự hán. It merged no pair that should stay apart, including Trương Thiết / Khúc Hồn. `entity_links` is empty, so no reveal was linked in the first 100 chapters.
- **`location_change` F1 rests on 2 chapters**, so 0.50 says little. This answers the S1-T7 question only loosely.
- **Q&A misses:** q05, q10 and q13. Their answer chapters are not in the evidence. The cause was not traced.
- **Seven names became two entities each**: Dã lang bang, Dư Tử Đồng, Dưỡng tinh đan, Thi Trùng Hoàn, Thiết nô, Thất huyền môn and Trát nhãn kiếm pháp, each under two categories. G-8 saw the same split.

## Sprint 1 criteria

1. **Identical row counts: met**, on v1 and again on v2. S0-S3 ran twice on the same database. v1 gave 100 chapters, 183 entities, 209 aliases, 399 events and 400 jobs both times, and v2 gave 100 chapters, 259 entities, 295 aliases, 846 events and 400 jobs both times. The second run made no model calls and wrote nothing.
2. **Deep-dive on 3 entities cites only chapters <= N: met live.** Through OpenRouter at N = 60, on the real ledger:

   | Entity | Citations | Grounding |
   |---|---|---|
   | Mặc đại phu | 15-59, 11 chapters | 86% |
   | Lệ Phi Vũ | 21, 33, 39, 43 | 40% |
   | Hàn Lập | 1-60, 13 chapters | 20% |

   Spend was $0.0025 for 3 answers. Grounding is the share of answer sentences that carry a citation, so most Hàn Lập sentences carry none. Two problems showed up on the way. One of the 5 deep-dive router calls failed on a context overflow (10,295 tokens against 10,240) and fell back to a refusal; the 25 router calls in the gold run all worked. OpenRouter also returned connection errors on 5 calls, and a repeat run answered.
3. **Extraction recall >= 0.80: met with prompt v2.** 0.98 across the 20 gold chapters; v1 scored 0.68.
4. **Measured s/chapter in the blueprint: met** by G-8, at 27.2 s/chapter.

## G-10.1 drop measurement

Prompt v1 on all 20 gold chapters, through real S1, with `python -m xn.gold_run`. Every second gold chapter is held out, so its names and scores stay hidden until the last round.

| Split | Chapters | Events kept | Dropped, low evidence | Dropped, invalid category |
|---|---|---|---|---|
| Dev | 1, 11, 22, 32, 43, 53, 64, 74, 84, 95 | 34 | 4 | 0 |
| Held out | 6, 17, 27, 37, 48, 58, 69, 79, 90, 100 | 37 | 0 | 0 |

S1 dropped 4 of 75 events, about 5%, so the evidence check is not the main cause of low recall and `EVIDENCE_MIN_SCORE` stays at 85. The model mostly never writes the missing events. Dev recall on this run is 0.62, with precision 0.91. It is the baseline for prompt v2.

## G-10.1 prompt v2

`prompts/s1_extract/v2.md` adds two rule blocks to v1: every named entity with a role gets its own event, the protagonist always does, and `name` holds exactly one verbatim name, never a joined list or a description. Two of 3 allowed attempts were used.

| Dev chapters | v1 | v2, attempt 1 | v2, attempt 2 (final) |
|---|---|---|---|
| Recall | 0.62 | 0.96 | **0.98** |
| Precision | 0.91 | 0.86 | 0.89 |
| Category accuracy | 0.96 | 0.92 | 0.94 |
| Event-type accuracy | 0.87 | 0.81 | 0.84 |
| Events kept | 34 | 89 | 87 |
| Events dropped | 4 | 0 | 0 |

These dev numbers are tuned, so they do not count toward the goal; the held-out chapters decide it.

**The hold-out is not fully clean.** The split was made after the 20-chapter v1 report, which showed misses and false names from all 20 chapters. Attempt 1 used two example phrases from that report that belong to held-out chapters 48 and 79, so attempt 2 replaced them. The rules themselves are general, but they were written after seeing that report, including the comma-joined name from chapter 27 and the missing protagonist in chapters 58 and 79. One replacement, "thanh trường kiếm", is a common phrase that also occurs in chapters 1-100. Scores on the held-out chapters may therefore read slightly high.

### Full run on prompt v2

Real S0-S3 on chapters 1-100, on a fresh database, with 100 S1 calls, none failed or retried. `ace67e0` pins the final prompt.

| Gold chapters | Recall | Precision | Category | Event type | False names |
|---|---|---|---|---|---|
| All 20 | **0.98** (100 of 102) | 0.88 | 0.91 | 0.90 | 14 |
| Dev, 10 | 0.96 (48 of 50) | 0.86 | 0.94 | 0.81 | 8 |
| Held out, 10 | **1.00** (52 of 52) | 0.90 | 0.88 | 0.98 | 6 |

- **Misses:** "bình thần bí" in chapter 11 and "Trừu tủy hoàn" in chapter 22, both dev.
- **Dev moved between runs.** The same prompt gave 0.98 and 0.89 on the 10-chapter run, and 0.96 and 0.86 here, so repeats differ by about one name and some false names.
- **Recall came from writing more.** v2 keeps 8.5 events per chapter (range 4-18) against 4.0 for v1, 846 events against 399. False names rose from 11 to 14, and they are descriptions such as "thiếu nữ" and "cái hộp đen".
- **S1 is slower.** It took 34.7 s/chapter, 58 minutes for 100 chapters. At that rate the 2,468-chapter book takes about 24 hours of S1, against about 14 at v1's rate.
- **More names split.** By exact name, 14 now sit under two entities, against 4 on v1. This is the problem G-8 saw.
- **Aliases did not improve.** F1 fell from 0.67 to 0.64, and precision stayed 1.00. The cause is still empty `seed_aliases`, not extraction.
- **Q&A rose** to 14 of 15, leaving q13. Context recall is 0.91, above the Sprint 2 bar of 0.75.
- **`location_change` is 0.33 on 2 gold chapters**, so it says little either way. `realm_breakthrough` has its one gold chapter in dev, so its held-out 0.00 means nothing.

## Sprint 1 recheck

An independent verifier pass at `c53d1e9` read every "Done when" line of G-1 to G-9 against the code as it stands, not against the round logs. All are met, or were superseded with the owner's yes (the extractor model, through G-8). Recall was the only failing criterion, and prompt v2 has since met it.

Open items it found, none blocking. G-11 has since fixed the first five; the last three remain:

- **`total_chapters` is stale.** Config, the `novels` row and the blueprint say 2,673, and the folder holds 2,468 contiguous chapters. A bare `xn run --stage s0` aborts on 205 missing chapters, and `xn ask --full` bounds at 2,673. The default raw folder does not match the real one either.
- **A follow-up may break on a future langgraph.** Each real follow-up logs that `RouterDecision`, `EvidencePack` and `Answer` are unregistered types that will be blocked later. `test_checkpoint_round_trip_gives_no_serializer_warning` can't see this, because the warning goes through logging.
- **`test_criterion_8` depends on the console encoding.** It fails when output is UTF-8 and the reader uses cp1252.
- **Not logged in `docs/GOALS.md`:** `cloud_spend_since` is a third exempt repository method, and `src/storage/qdrant_client.py` also keeps `src/config.py` alive.
- **Stale text:** `README.md` still names Redis, blueprint §3.9 still shows `FROM qwen2.5:14b`, and `s0_parse.py` line 43 raises a SyntaxWarning on every run.
- **Sprint 2 bar:** evidence-only context recall was 0.72 against 0.75 on v1. Prompt v2 lifts it to 0.91.
- **S1 speed:** v1 measured 20.4 s/chapter on chapters 1-100 and v2 measured 34.7, against the blueprint's 27.2 from 50 chapters across 1-1121 on v1. The blueprint cost table still shows v1.
- **Nobody owns:** CI, ruff and mypy (OQ-03).

## G-25: `qwen2.5:14b-instruct` against the production extractor

Date: 2026-11-05. Goal: G-25.

**`qwen2.5:14b-instruct` is 3.4 times faster, but its recall of 0.70 fails Sprint 1's 0.80 bar.** The production extractor stayed `qwen3.8:udiq4_hui` at the time (G-27 later switched it to `qwen3.5:9b_UD_Q6`). Nothing in config, the prompt or S1 changed. Running 2 chapters at once gave no speed-up, and a 16k context changed nothing but VRAM. A later run of `qwen3.5:9b_UD_Q6` did pass the recall bar, at 0.93, and is 2.1 times faster than production, but its precision is 0.79; see "`qwen3.5:9b_UD_Q6`" below.

Both models ran prompt v2 (`6f35c798`) on the G-13 set: 50 chapters from 1-100, the 20 gold chapters plus 30 others. Gold scores cover the 20 gold chapters. `qwen2.5:14b-instruct` ran as `qwen25-14b-10k` (`ollama/Modelfile.qwen25-14b-10k`: `num_ctx 10240`, temperature 0.1), with native JSON-schema output, one chapter at a time, on an RTX 5070 Ti 16 GB with nothing else on the GPU. The production column is the G-10.1 full run in `data/eval/g101-full.db`, read only and restricted to the same 50 chapters. The owner chose not to run it again.

| Metric | `qwen2.5:14b-instruct` | `qwen3.8:udiq4_hui` (G-10.1) |
|---|---|---|
| Schema success | 100% (50 of 50) | 100% (50 of 50) |
| First-try success | 100% | 100% |
| Entity recall, all 20 | 0.70 (71 of 102) | **0.98** (100 of 102) |
| Entity precision, all 20 | **0.95** (4 false names) | 0.88 (14 false names) |
| Recall, dev 10 / held out 10 | 0.60 / 0.79 | 0.96 / 1.00 |
| Precision, dev 10 / held out 10 | 0.94 / 0.95 | 0.86 / 0.90 |
| Category accuracy | 0.91 | 0.91 |
| Event-type accuracy | 0.81 | **0.90** |
| Flag F1, `location_change` (2 gold chapters) | 0.57 | 0.33 |
| Flag F1, `realm_breakthrough` (1 gold chapter) | 0.00 | 1.00 |
| Flag F1, `major_conflict` (6 gold chapters) | 0.75 | 0.92 |
| Events kept per chapter | 5.1 (range 0-8) | 8.7 (range 4-18) |
| Tokens per chapter, input / output | 2,977 / 656 | 2,679 / 947 |
| s/chapter, mean | **10.3** | 35.4 |
| s/chapter, p95 | **14.5** | 58.9 |
| Idle / peak VRAM (MiB) | 1,822 / 11,514 | not recorded in G-10.1; 15,425 peak in G-8 |
| S1 for all 2,468 chapters | about 7 hours | about 24 hours |

### Two chapters at once

The owner asked for a test of 2 chapters in parallel. `--workers 2` ran the same 50 chapters with 2 S1 processes at once, each on its own database with every second chapter. `OLLAMA_NUM_PARALLEL` is unset on this host and was not changed.

| Measure | One at a time | 2 workers |
|---|---|---|
| Time for 50 chapters | 516 s, summed calls | 537 s, wall |
| s/chapter, wall | 10.3 | 10.7 |
| s per call, mean / p95 | 10.3 / 14.5 | 21.1 / 27.0 |
| Peak VRAM (MiB) | 11,514 | 11,449 |
| Recall / precision, all 20 | 0.70 / 0.95 | 0.71 / 0.91 |

**No gain:** each call took twice as long, so throughput stayed the same. These numbers cannot show whether Ollama ran the two requests together on a GPU that one request already keeps busy, or queued one behind the other; either way S1 got no faster. A 2-chapter smoke test first looked faster (29.7 s of wall time against 42.8 s of summed call time). But those call times included the slowdown from the other request, so their sum was not a fair one-at-a-time figure. The gold scores moved by one name and a few false names, which is run-to-run variation at temperature 0.1.

### At 16k context

The owner asked for a run at 16k tokens. `qwen25-14b-16k` (`ollama/Modelfile.qwen25-14b-16k`: `num_ctx 16384`, temperature 0.1) ran the same 50 chapters, one at a time, on a fresh database. The GPU was checked free first.

| Metric | 10k (`qwen25-14b-10k`) | 16k (`qwen25-14b-16k`) |
|---|---|---|
| Schema success / first try | 100% / 100% | 100% / 100% |
| Entity recall, all 20 | 0.70 (71 of 102) | 0.68 (69 of 102) |
| Entity precision, all 20 | 0.95 (4 false names) | 0.91 (7 false names) |
| Recall, dev / held out | 0.60 / 0.79 | 0.68 / 0.67 |
| Precision, dev / held out | 0.94 / 0.95 | 0.94 / 0.88 |
| Category accuracy | 0.91 | 0.99 |
| Event-type accuracy | 0.81 | 0.74 |
| Flag F1, `location_change` / `realm_breakthrough` / `major_conflict` | 0.57 / 0.00 / 0.75 | 0.57 / 0.00 / 0.75 |
| Events kept per chapter | 5.1 (range 0-8) | 5.4 (range 0-10) |
| Events dropped for low evidence | not counted: only the last 40 console lines were kept | 25, 8 of them in chapter 79 |
| Tokens per chapter, input / output | 2,977 / 656 | 2,977 / 664 |
| s/chapter, mean / p95 | 10.3 / 14.5 | 10.4 / 14.7 |
| Idle / peak VRAM (MiB) | 1,822 / 11,514 | 2,367 / 14,360 |

**16k buys nothing here.** Chapters in the set average about 3,000 input and 660 output tokens, and the largest call used 3,583 input and 1,033 output, so 10k was never full, and the model saw the same prompt at both sizes. Recall, precision and category accuracy moved by a few names, about as much as the 2-worker run at 10k moved from the one-at-a-time run, which is run-to-run variation at temperature 0.1. Event-type accuracy (0.81 to 0.74) and held-out recall (0.79 to 0.67) moved more than that, but still by a handful of events and names, on one run each. The flag results are identical. Peak VRAM was 2,846 MiB higher (idle was 545 MiB higher), presumably the larger KV cache, and time did not change measurably. The 16k run is one run, on 2026-11-05 in the evening, hours after the 10k runs.

### `qwen3.5:9b_UD_Q6`

The owner asked for speed and quality of `qwen3.5:9b_UD_Q6` (8.95B, Q6_K, already pulled). It ran as `qwen35-9b-udq6-10k` (`ollama/Modelfile.qwen35-9b-udq6-10k`: `num_ctx 10240`, temperature 0.1) with thinking off, as `qwen3.5:Q8` did in G-7. It used the same 50 chapters and prompt v2, one at a time, on a fresh database, with the GPU checked free first.

| Metric | `qwen3.5:9b_UD_Q6` | `qwen2.5:14b-instruct` (10k) | `qwen3.8:udiq4_hui` (G-10.1) |
|---|---|---|---|
| Schema success / first try | 100% / 100% | 100% / 100% | 100% / 100% |
| Entity recall, all 20 | 0.93 (95 of 102) | 0.70 (71 of 102) | **0.98** (100 of 102) |
| Entity precision, all 20 | 0.79 (26 false names) | **0.95** (4) | 0.88 (14) |
| Recall, dev / held out | 0.94 / 0.92 | 0.60 / 0.79 | 0.96 / 1.00 |
| Precision, dev / held out | 0.75 / 0.83 | 0.94 / 0.95 | 0.86 / 0.90 |
| Category accuracy | 0.82 | 0.91 | 0.91 |
| Event-type accuracy | 0.88 | 0.81 | 0.90 |
| Flag F1, `location_change` (2 gold chapters) | 0.25 | 0.57 | 0.33 |
| Flag F1, `realm_breakthrough` (1 gold chapter) | 0.00 | 0.00 | 1.00 |
| Flag F1, `major_conflict` (6 gold chapters) | **1.00** | 0.75 | 0.92 |
| Events kept per chapter | 12.5 (range 4-25) | 5.1 (range 0-8) | 8.7 (range 4-18) |
| Events dropped for low evidence | 9 | not counted | not counted |
| Tokens per chapter, input / output | 2,728 / 1,342 | 2,977 / 656 | 2,679 / 947 |
| s/chapter, mean / p95 | 16.5 / 31.4 | **10.3** / 14.5 | 35.4 / 58.9 |
| Idle / peak VRAM (MiB) | 2,270 / 11,651 | 1,822 / 11,514 | 15,425 peak, from G-8 |
| S1 for all 2,468 chapters | about 11 hours | about 7 hours | about 24 hours |

- **Recall passes the 0.80 bar, and the misses are few.** 7 gold names were missed: "Dưỡng tinh đan" (43), "Mã phó môn chủ" (69), "La Yên bộ" and "Ngự Phong quyết" (84), and "Hàn Lập", "phi kiếm" and "Khu vật thuật" (90). Dev and held out score alike, 0.94 and 0.92. Prompt v2 was tuned on the production model, not this one.
- **Precision is the weak side.** Its 26 false names are mostly kinship terms and descriptions, not entities: "đại ca", "cha mẹ", "nhị ca", "thiếu nữ", "năm loại thảo dược", "bảy cái đầu quỷ", "chú chim nhỏ màu vàng". It writes the most events of the three, 12.5 per chapter, which is consistent with both the high recall and the extra names. These names would reach S2 as false entities. Precision is 0.79 against the 0.90 S4 target, and production's 0.88.
- **Speed sits between the other two.** Its output is the longest, 1,342 tokens per chapter, which fits with it being slower than qwen2.5 although it is the smallest model. The p95 is 31.4 s. The largest call used 3,312 input and 2,760 output tokens, so 10k was never full.
- **Flags:** `major_conflict` matched all 6 gold chapters with no extras. It missed the single gold `realm_breakthrough` (chapter 43), and marked `location_change` on 15 of 50 chapters: on gold chapter 100 correctly, on 5 gold chapters without one (37, 43, 79, 84, 95), and not on gold chapter 1. Flag gold rests on few positive chapters.
- **Category accuracy is lower**, at 0.82 against 0.91 for the others, so more events are filed under the wrong category.
- **One run**, on 2026-11-05 in the evening. The vision projector of this model was not used. G-7's warning about `qwen3.5:Q8` writing event descriptions as names shows here too, in a milder form: 5 names were not in the chapter text. `qwen3.5:Q8` had 222 in G-7, but on a different chapter set and prompt v1, so the two are not like for like.

**Recommendation (2026-11-05, superseded by G-27, which made `qwen3.5:9b_UD_Q6` with prompt v4 the production extractor):** production stays `qwen3.8:udiq4_hui`. `qwen3.5:9b_UD_Q6` passes the recall bar at about half production's time, about 11 hours for the book against 24. Whether to switch depends on whether a prompt change can cut its kinship and description names without losing recall, which would need its own goal and the owner's yes.

### How to read this

- **Recall fails the bar, and the gap is in named things, not people.** 31 gold names were missed. Most are techniques, items, places and sects: "Trường Xuân Công", "La Yên bộ" (three chapters), "Ngự Phong quyết", "Thanh Linh Tán", "Lạc Nhật Phong", "Thất huyền môn". Chapters 84 and 95 lose the Kim Quang thượng nhân group, and chapters 53 and 58 lose "Hàn Lập" himself. Dev recall was 0.60 and held-out 0.79; prompt v2 was tuned on the other model, so that split says little here.
- **It writes fewer events.** It never went above 8 events per chapter, and one chapter got none, where production writes up to 18. That is consistent with both the higher precision and the lower recall. Prompt v2 was tuned on `qwen3.8:udiq4_hui` in G-10.1, so a prompt tuned for this model might close some of the gap. That was not tried, because changing the prompt is outside G-25.
- **Some events fail the evidence check.** The log shows events dropped for evidence scores below 85 in chapters 91 and 93, where the model paraphrased the quote. The console output was cut short, so the total was not counted.
- **Flags:** it marked `major_conflict` on 30 of 50 chapters, against 19 for production, including 4 gold chapters with no conflict (17, 32, 53, 58). It missed the single gold `realm_breakthrough`, chapter 43. Flag gold rests on few positive chapters, so one chapter moves these scores a lot.
- **Timing is not from the same session.** The production numbers come from the G-10.1 run on 2026-11-01, which ran S1 over all 100 chapters as part of an S0-S3 run. The qwen2.5 run is from 2026-11-05. Both used prompt v2, `num_ctx 10240` and the same GPU. That difference was not measured, but it seems unlikely to account for a 3.4-times gap. G-10.1 recorded no VRAM, so the production VRAM figure is G-8's peak on 50 chapters across 1-1121, from prompt v1.
- **One run each**, from fresh databases. The gold was drafted by Claude, and the held-out chapters were already seen when prompt v2 was tuned, as "G-10.1 prompt v2" says.

**Recommendation:** keep `qwen3.8:udiq4_hui`. `qwen2.5:14b-instruct` would only make sense as the extractor if a prompt tuned for it reached recall 0.80 on the same gold, which would need a new goal. Running chapters in parallel on this one GPU does not speed up S1 for either model.

### Commands

Run from the repository root with `PYTHONIOENCODING=utf-8`. `<data>` is the main checkout's `data` folder.

```bash
ollama create qwen25-14b-10k -f ollama/Modelfile.qwen25-14b-10k
```

```bash
uv run py -m xn.bench --raw-dir <data>/novel/pham_nhan_tu_tien_1 --out <data>/bench/g25-qwen25 --chapter-max 100 --gold --model qwen25-14b-10k
```

```bash
uv run py -m xn.bench --raw-dir <data>/novel/pham_nhan_tu_tien_1 --out <data>/bench/g25-qwen25 --chapter-max 100 --gold --model qwen25-14b-10k --workers 2
```

```bash
ollama create qwen25-14b-16k -f ollama/Modelfile.qwen25-14b-16k
```

```bash
uv run py -m xn.bench --raw-dir <data>/novel/pham_nhan_tu_tien_1 --out <data>/bench/g25-qwen25-16k --chapter-max 100 --gold --model qwen25-14b-16k
```

```bash
ollama create qwen35-9b-udq6-10k -f ollama/Modelfile.qwen35-9b-udq6-10k
```

```bash
uv run py -m xn.bench --raw-dir <data>/novel/pham_nhan_tu_tien_1 --out <data>/bench/g25-qwen35-9b --chapter-max 100 --gold --model qwen35-9b-udq6-10k
```

The outputs are `report.md`, `report.json`, `parallel-qwen25-14b-10k-w2.md` and `.json` in `<data>/bench/g25-qwen25/`, and `report.md` and `report.json` in `<data>/bench/g25-qwen25-16k/`, with the console log in `<data>/bench/g25-qwen25-16k.log`; the same for `qwen3.5:9b_UD_Q6` in `<data>/bench/g25-qwen35-9b/`, with its log in `<data>/bench/g25-qwen35-9b.log`. The production column was read from `g101-full.db` with `xn.bench.pick_gold_chapters`, `_measure_runs` and `score_gold`, without writing to it.

## G-26: cutting the false names of `qwen3.5:9b_UD_Q6`

Date: 2026-11-05. Goal: G-26.

**`qwen3.5:9b_UD_Q6` on prompt v3 with an offline name filter reaches recall 0.95 and precision 0.88 on the 20 gold chapters (held out 0.94 / 0.89), at 16.3 s/chapter.** The filter does the work: prompt v3 alone did not cut false names. Production config, prompt v2 and S1 are unchanged.

In G-25, `qwen3.5:9b_UD_Q6` (served as `qwen35-9b-udq6-10k`: `num_ctx 10240`, thinking off, temperature 0.1) ran prompt v2 on the G-13 set at 16.5 s/chapter, with recall 0.93 (95 of 102) and precision 0.79 (26 false names) on the 20 gold chapters. Production (`qwen3.8:udiq4_hui`, G-10.1) has 0.98 and 0.88 at 35.4 s/chapter. G-26 tries to cut the false names without losing much recall: an offline name filter, then a prompt v3. Rules and prompt are tuned on the 10 dev gold chapters only (`xn.gold.gold_split`, the first of each pair); the 10 held-out chapters are scored once, in the last round.

### The 26 false names by type

The false names of the G-25 qwen3.5 run on all 20 gold chapters, as `score_extraction` lists them (chapter: name, category the model gave). Dev chapters are 1, 11, 22, 32, 43, 53, 64, 74, 84, 95; held out are 6, 17, 27, 37, 48, 58, 69, 79, 90, 100.

| Type | Count | Dev | Held out |
|---|---|---|---|
| Kinship and address terms | 6 | 1: Đại ca, Tiểu muội muội; 22: Cha mẹ, Đại ca, Nhị ca | 79: Gia hoả |
| People named by a description or role | 4 | 74: Tử y chưởng kỳ sứ; 84: Tên mập | 69: thiếu nữ; 79: Chấp pháp |
| Counted groups | 3 | 53: bảy cái đầu quỷ, bảy thanh dao nhọn | 27: năm loại thảo dược |
| Common nouns with descriptors, generic arts | 5 | 43: ngụy chân khí; 64: luyện thi thuật; 95: đạo phù lục vũ hình thanh tiểu kiếm | 27: cách điều chế hi hữu; 100: chú chim nhỏ màu vàng |
| Generic places | 4 | 1: Sơn biên tiểu thôn | 6: Sơn cốc; 79: Môn phái, Sân nhà Lý trưởng lão |
| Proper-looking names the gold does not list | 4 | 74: Ngũ sát Tam ưng Nhị báo, Thất kiệt Song hùng; 84: Vương môn | 100: Hương Lỗ đại vận hà |

16 of the 26 are in dev chapters. The last type may be gold gaps rather than model errors: group nicknames and place names that the annotation left out. No filter here can or should remove them.

### R-1: filter rules, scored offline on dev

`xn.name_filter` (not used by production S1) filters stored extractions and scores them with `xn.gold.score_extraction`. A filtered name loses its events; a filtered holder or `same_as` name is cleared, because S2 also makes entities from those.

- **caps**: keep a name only if the chapter writes it with a capital first letter somewhere other than at a sentence start. Matching ignores case and diacritics and must cover whole words. A match is at a sentence start when the text before it, ignoring spaces, tabs and quotes, is empty or ends in `.`, `!`, `?`, `:`, a newline, an ellipsis or a dash.
- **caps-proper** / **caps-character**: the same test, applied only to names in `character` and `dungeon`, or only `character`. Other categories pass, because this translation writes many items and techniques in lower case ("phi kiếm", "Trừu tủy hoàn").
- **words**: drop kinship and address terms ("đại ca", "cha mẹ", "sư huynh", "thiếu nữ", "tên mập" and others), names whose first word is a native numeral or a classifier ("bảy", "năm", "mấy", "cái", "con", "tên"), and names of two or more words ending in a demonstrative ("kia", "này", "đó"). The list is general Vietnamese kinship and address vocabulary plus the dev false names; the brief that started this goal also quoted three held-out false names ("thiếu nữ", "năm loại thảo dược", "chú chim nhỏ màu vàng"), and the first two are in the list because they are generic.
- Combinations: **caps+words**, **caps-proper+words**, **caps-character+words**.

Dev chapters only (10 chapters, 50 gold names). R is recall, P precision:

| Rule | qwen3.5 9B, prompt v2 (G-25) | production, prompt v2 (G-10.1) |
|---|---|---|
| none | R 0.94 (47/50), P 0.75 (16 false) | R 0.96 (48/50), P 0.86 (8 false) |
| caps | R 0.92 (46/50), P 0.94 (3 false) | R 0.96 (48/50), P 1.00 (0 false) |
| caps-proper | R 0.94 (47/50), P 0.85 (8 false) | R 0.96 (48/50), P 0.92 (4 false) |
| caps-character | R 0.94 (47/50), P 0.84 (9 false) | R 0.96 (48/50), P 0.91 (5 false) |
| words | R 0.94 (47/50), P 0.85 (8 false) | R 0.96 (48/50), P 0.92 (4 false) |
| caps+words | R 0.92 (46/50), P 0.94 (3 false) | R 0.96 (48/50), P 1.00 (0 false) |
| caps-proper+words | R 0.94 (47/50), P 0.89 (6 false) | R 0.96 (48/50), P 0.94 (3 false) |
| caps-character+words | R 0.94 (47/50), P 0.87 (7 false) | R 0.96 (48/50), P 0.92 (4 false) |

- **caps** removes 13 of the 16 dev false names of qwen3.5 and all 8 of production's, for one gold name: "bình thần bí" (chapter 11), which this chapter never capitalizes. The 3 it keeps are the proper-looking names of the last type above. On dev, adding **words** to caps changes nothing.
- **caps-proper+words** loses no dev gold name, and keeps 6 qwen3.5 false names: the 3 proper-looking ones and 3 lower-case items and arts ("ngụy chân khí", "luyện thi thuật", "đạo phù lục vũ hình thanh tiểu kiếm"), which only the capital test catches.
- The scratch check in the goal's brief reported production falling from 0.98 to 0.89 recall on all 20 chapters with this test. On dev, production loses no gold name here, and a version that matches with diacritics gives the same dev numbers, so the losses behind that figure would be in held-out chapters; they are measured in R-3.
- The two candidates for the last round are **caps** (best precision) and **caps-proper+words** (no dev recall loss). Held-out scores for every rule come in R-3.

Command (read-only on both databases):

```bash
uv run py -m xn.name_filter --split dev --db <data>/bench/g25-qwen35-9b/qwen35-9b-udq6-10k.db --db <data>/eval/g101-full.db
```

### R-2: prompt v3, two attempts on dev

`prompts/s1_extract/v3.md` is v2 with one added block of "not an entity" rules: kinship and address terms, people named only by a description, counted groups, and common nouns with descriptors or demonstratives. Every example comes from the dev chapters ("Đại ca", "Nhị ca", "Cha mẹ", "Tiểu muội muội", "sư huynh", "Tên mập", "bảy cái đầu quỷ", "bảy thanh dao nhọn", "cái bình kia", "đạo phù lục vũ hình thanh tiểu kiếm", "Sơn biên tiểu thôn"). It also says that lower-case item and technique names still count, with dev gold examples ("Trừu tủy hoàn", "Dưỡng tinh đan", "Thất quỷ phệ hồn đại pháp", "bình thần bí"), and adds a check for these cases to v2's closing self-check. `xn.bench` gained `--prompt <version>`, which sets `prompts.s1_extract` on a copy of the config, and `--gold-split dev|held_out`, which runs only that split's gold chapters and scores only that split. Production config still says v2.

Each attempt ran `qwen35-9b-udq6-10k` on the 10 dev chapters, one chapter at a time, in a new database, with the GPU checked free first (`ollama ps` empty, no other `xn` process). Attempt 1 put the block after v2's name rules. Attempt 2 moved it before v2's "cover every entity" block and marked it as overriding that block. The G-25 column is the v2 run on the same dev chapters, taken from its 50-chapter run.

| Dev chapters (10, 50 gold names) | v2 (G-25) | v3 attempt 1 (`882932be`) | v3 attempt 2 (`21fee82d`) |
|---|---|---|---|
| Recall, no filter | 0.94 (47) | **0.96** (48) | 0.88 (44) |
| Precision, no filter | 0.75 (16 false) | 0.76 (15 false) | 0.75 (15 false) |
| With caps: recall / precision | 0.92 / 0.94 | 0.94 / **0.98** | 0.86 / 0.98 |
| With caps-proper+words: recall / precision | 0.94 / 0.89 | **0.96** / 0.92 | 0.88 / 0.92 |
| Category / event-type accuracy | see G-25 (all 20 only) | 0.83 / 0.83 | 0.91 / 0.82 |
| s/chapter, mean / p95 | 16.5 / 31.4 (50 chapters) | 18.6 / 29.0 | 16.8 / 31.9 |
| Schema success / first try | 100% / 100% | 100% / 100% | 100% / 100% |

- **The prompt rules alone did not cut false names.** Both attempts still wrote "Đại ca", "Nhị ca", "Tên mập" and "đạo phù lục vũ hình thanh tiểu kiếm", the very examples the prompt names as not entities, usually as `MENTIONED` events. Attempt 2 also wrote "bảy cái đầu quỷ" and "bảy thanh dao nhọn". Precision stayed at 0.75-0.76.
- **The filter cuts them.** On v3 attempt 1, caps leaves 1 false name ("Bang chủ Dã Lang", chapter 95) and caps-proper+words leaves 4.
- Attempt 2 lost 4 more dev gold names ("Trừu tủy hoàn", "Lạc Nhật Phong", "Dã Lang bang", "Thất huyền môn"). With one run each at temperature 0.1, this may be noise rather than the prompt change.
- **Stopped after 2 of the 3 allowed attempts.** The model followed neither placement, and a third rewording had no better reason to work. `v3.md` is back to the attempt 1 text, which had the best dev recall.
- **The setup for R-3, chosen on dev before any held-out scoring:** prompt v3 (attempt 1) with the **caps-proper+words** filter. It keeps every dev gold name the model found, and its dev precision of 0.92 clears 0.88. Plain caps has higher dev precision (0.98), but it drops lower-case gold names such as "bình thần bí", and the goal's brief lists 11 gold names that are never capitalized mid-sentence. R-3 reports every rule on all 20 chapters, with held out apart.

Commands, from the repository root with `PYTHONIOENCODING=utf-8` (the attempt 2 prompt is commit `ba17ba8`):

```bash
uv run py -m xn.bench --raw-dir <data>/novel/pham_nhan_tu_tien_1 --out <data>/bench/g26-v3-a1 --chapter-max 100 --gold --gold-split dev --model qwen35-9b-udq6-10k --prompt v3
```

```bash
uv run py -m xn.name_filter --split dev --db <data>/bench/g26-v3-a1/qwen35-9b-udq6-10k.db
```

Attempt 2 used the same commands with `--out <data>/bench/g26-v3-a2`. Console logs are in `<data>/bench/g26-v3-a1.log` and `g26-v3-a2.log`.

### R-3: the chosen setup on all 50 chapters

`qwen35-9b-udq6-10k` ran prompt v3 (`882932be`) on the G-13 set (50 chapters from 1-100: the 20 gold chapters plus 30 others), one chapter at a time, in a new database, with the GPU checked free first. The filter was scored offline afterwards. This is the first time any G-26 setup was scored on the 10 held-out chapters. The qwen3.5 v2 columns are the G-25 run and the production columns are the G-10.1 run (`g101-full.db`), both read only, with the same filter applied.

| All 20 gold chapters | qwen3.5 9B, v3 + caps-proper+words (**G-26 setup**) | qwen3.5 9B, v3, no filter | qwen3.5 9B, v2, no filter (G-25) | qwen3.5 9B, v2 + caps-proper+words | production, v2, no filter (G-10.1) | production, v2 + caps-proper+words |
|---|---|---|---|---|---|---|
| Entity recall | **0.95** (97 of 102) | 0.96 (98) | 0.93 (95) | 0.91 (93) | 0.98 (100) | 0.96 (98) |
| Entity precision | **0.88** (13 false names) | 0.75 (32) | 0.79 (26) | 0.92 (8) | 0.88 (14) | 0.96 (4) |
| Recall, dev / held out | 0.96 / **0.94** | 0.96 / 0.96 | 0.94 / 0.92 | 0.94 / 0.88 | 0.96 / 1.00 | 0.96 / 0.96 |
| Precision, dev / held out | 0.87 / **0.89** | 0.73 / 0.78 | 0.75 / 0.83 | 0.89 / 0.96 | 0.86 / 0.90 | 0.94 / 0.98 |
| Category / event-type accuracy | 0.82 / 0.87 | 0.83 / 0.88 | 0.82 / 0.88 | 0.83 / 0.88 | 0.91 / 0.90 | 0.92 / 0.90 |
| Flag F1, `location_change` / `realm_breakthrough` / `major_conflict` | 0.44 / 0.00 / 1.00 | same | 0.25 / 0.00 / 1.00 | same | 0.33 / 1.00 / 0.92 | same |
| s/chapter, mean / p95 (50 chapters) | **16.3** / 31.6 | same run | 16.5 / 31.4 | same run | 35.4 / 58.9 | same run |

The precision of the G-26 setup is 97 / 110 = 0.882, just over the 0.88 target. The other run figures: schema success and first-try success 100%, 11.6 events per chapter (range 4-25), 3,069 input and 1,261 output tokens per chapter, idle / peak VRAM 2,656 / 12,175 MiB, 7 events dropped by S1's evidence check.

Every filter rule on the v3 run, all 20 chapters (dev / held out in brackets):

| Rule | Recall | Precision |
|---|---|---|
| none | 0.96 (0.96 / 0.96) | 0.75 (0.73 / 0.78) |
| caps | 0.89 (0.94 / 0.85) | 0.95 (0.94 / 0.96) |
| caps-proper | 0.95 (0.96 / 0.94) | 0.86 (0.84 / 0.88) |
| caps-character | 0.95 (0.96 / 0.94) | 0.80 (0.81 / 0.79) |
| words | 0.96 (0.96 / 0.96) | 0.82 (0.83 / 0.81) |
| caps+words | 0.89 (0.94 / 0.85) | 0.96 (0.94 / 0.98) |
| **caps-proper+words** | **0.95 (0.96 / 0.94)** | **0.88 (0.87 / 0.89)** |
| caps-character+words | 0.95 (0.96 / 0.94) | 0.82 (0.84 / 0.80) |

How to read this:

- **The targets are met, by the filter rather than the prompt.** Recall 0.95 >= 0.90, precision 0.88 >= 0.88 (0.882), 16.3 s/chapter <= 20. Prompt v3 alone gave 0.96 recall and 0.75 precision, no better than v2's 0.79. The filter took the false names from 32 to 13. Recall rose from 0.93 to 0.96 between the v2 and v3 runs, but one run each at temperature 0.1 cannot separate the prompt's effect from run-to-run variation; R-2's two dev attempts differed by 4 gold names with no clear cause.
- **v2 with the same filter also meets the targets** on these 20 chapters: recall 0.91, precision 0.92. Recall is the deciding difference: 0.95 for v3 against 0.91 for v2, at the same speed.
- **The held-out chapters confirm the dev choice.** Plain caps kept dev recall at 0.94 but fell to 0.85 on held out, losing lower-case gold names ("vô danh khẩu quyết", "bình nhỏ", "huyết tiến âm hồn chú", "phi kiếm", "Tiểu toán bàn" among them). caps-proper+words held at 0.94.
- **What the filter cost:** one gold name, "Tiểu toán bàn" (chapter 17), which this chapter never capitalizes mid-sentence. The model itself missed four others: "Mã phó môn chủ" (chapter 69; S1's evidence check dropped its event), "cự hán", "La Yên bộ" and "Ngự Phong quyết".
- **What it leaves:** 13 false names, mostly lower-case items and generic arts that the capital test does not touch, since items and techniques are exempt ("thuốc trị ngoại thương", "khẩu quyết", "luyện thi thuật", "dược vật", "chim nhỏ màu vàng"), plus proper-looking group and title names ("Ngũ sát Tam ưng Nhị báo", "Thất kiệt Song hùng", "Bang chủ Dã Lang").
- **It still trails production on quality**: category accuracy 0.82 against 0.91, and it missed the one gold `realm_breakthrough` (chapter 43). With the same filter, production reaches 0.96 / 0.96. qwen3.5 is 2.2 times faster.
- The model sometimes repeats the same event many times (in chapter 22 of the dev attempt 1 run, "Hàn Lập" `MENTIONED` alternates between two quotes about a dozen times), and one chapter hit the 25-event cap. Duplicates do not change the name scores, but they cost output tokens.

Caveats:

- **These 20 gold chapters have judged every extraction run since G-10.1**, and prompt v2 was tuned on them for the production model. The "held-out" chapters were held out from G-26's tuning only; G-10.1 had already seen them. The brief that started G-26 also quoted three held-out false names, two of which are in the word list (see R-1). The gold was drafted by Claude.
- **One run per setup** at temperature 0.1. Run-to-run variation on the dev chapters (R-2) was about 4 gold names, so differences of 1-3 names between columns are not meaningful, and the 0.882 precision clears 0.88 by one false name.
- **The filter is offline only.** Production S1 does not run it, and the precision above assumes something like `xn.name_filter` would run after S1. Putting it into S1 is a later owner decision. It also clears filtered holder and `same_as` names, which S2 would otherwise turn into entities.
- **Timing**: the owner set `OLLAMA_NUM_PARALLEL=4` during this goal. S1 sent one request at a time, the model stayed 100% on the GPU at context 10240, and the slowest call took 36.6 s, in line with G-25's p95 of 31.4 s, so the setting is not expected to affect these numbers. The production timing is from G-10.1 on 2026-11-01, as in G-25.

**Recommendation:** if a faster extractor is wanted, qwen3.5 9B on prompt v3 with the caps-proper+words filter in S1 meets this goal's bar at 2.2 times production speed, but production with the same filter is better on every quality measure here. Whether to change the extractor, and whether to add the filter to S1, are owner decisions.

Commands, from the repository root with `PYTHONIOENCODING=utf-8`:

```bash
uv run py -m xn.bench --raw-dir <data>/novel/pham_nhan_tu_tien_1 --out <data>/bench/g26-v3-full --chapter-max 100 --gold --model qwen35-9b-udq6-10k --prompt v3
```

```bash
uv run py -m xn.name_filter --split all --db <data>/bench/g26-v3-full/qwen35-9b-udq6-10k.db --db <data>/bench/g25-qwen35-9b/qwen35-9b-udq6-10k.db --db <data>/eval/g101-full.db
```

The same `xn.name_filter` command with `--split dev` and `--split held_out` gives the split columns. The console log is `<data>/bench/g26-v3-full.log`.

### Two chapters at once (owner request, after R-3)

The owner raised `OLLAMA_NUM_PARALLEL` to 4 and asked for a 2-worker test of the G-26 setup. `--workers 2` ran prompt v3 on the same 50 chapters with 2 S1 processes at once, each on its own new database with every second chapter. The GPU was checked free first.

| Measure | One at a time (R-3) | 2 workers |
|---|---|---|
| s/chapter, wall | 16.3 (summed calls) | 16.0 |
| s per call, mean / p95 | 16.3 / 31.6 | 31.4 / 44.2 |
| Peak VRAM (MiB) | 12,175 | 12,126 |
| Recall / precision, all 20, no filter | 0.96 / 0.75 | 0.97 / 0.75 |
| Recall / precision, all 20, caps-proper+words | 0.95 / 0.88 | 0.95 / 0.90 (dev 0.96 / 0.91, held out 0.94 / 0.89) |

**No gain, and Ollama says why.** For this model, Ollama's server log (`%LOCALAPPDATA%\Ollama\server.log`) records `model architecture does not currently support parallel requests` with `architecture=qwen35`, and the model loads with `n_seq_max = 1`: one slot, so the second request waits for the first. That is why each call took twice as long and the wall time did not move. More parallel slots cannot help this model on this Ollama version. Peak VRAM did not grow either, because no extra KV cache was allocated. The same log shows the running server started with `OLLAMA_NUM_PARALLEL:2`, so the new value of 4 had not reached it. For qwen3.5 that makes no difference. The gold scores moved by a few names, within run-to-run variation.

Command:

```bash
uv run py -m xn.bench --raw-dir <data>/novel/pham_nhan_tu_tien_1 --out <data>/bench/g26-v3-w2 --chapter-max 100 --gold --model qwen35-9b-udq6-10k --prompt v3 --workers 2
```

The filtered scores combine the two worker databases (`qwen35-9b-udq6-10k-w2-1.db` and `-2.db`), read only, with `xn.name_filter.read_db` and `score_rules`. The `xn.name_filter` CLI reads one database per column, so it cannot score a split run directly.

## G-27: `qwen3.5:9b_UD_Q6` as the S1 extractor

Date: 2026-11-06. Goal: G-27.

**As the S1 extractor, `qwen3.5:9b_UD_Q6` on prompt v4 with the new S1 clean-up step passes every extraction target, and runs at half production's time per chapter (17.5 s against 34.7). Switching the whole local model fails one target: Q&A citation hit rate falls from 0.93 to 0.80. The cause is qwen3.5 as the query router, not its extraction.** Production config is unchanged; the switch is the owner's decision.

Setup: `qwen35-9b-udq6-10k` (`num_ctx 10240`, thinking off, temperature 0.1). Prompt v4 and the clean-up were tuned on the 10 dev gold chapters only (`xn.gold.gold_split`, the first of each pair). The 10 held-out chapters were scored once, in the full test. The production column is the G-10.1 run of `qwen3.8:udiq4_hui` on prompt v2 (`data/eval/g101-full.db`, read only).

### Prompt v4 and the clean-up step

`prompts/s1_extract/v4.md` is v3 plus a one-line definition of each category, and a rule for `realm_breakthrough`. Under the definitions, pills, potions, talismans and treasures are `weapon_treasure`; arts and methods are `technique`; sects, gangs and places are `dungeon`; beasts and puppets, including a person made into a puppet, are `puppet_beast`. Under the rule, the protagonist reaching a new layer of his main cultivation method counts, even when the chapter tells it as past ("Trường xuân công của Hàn Lập đã luyện đến tầng thứ sáu", chapter 43). Every quoted example is a gold entity of a dev chapter. Some of them, such as "Hàn Lập" and "Dưỡng tinh đan", recur in held-out chapters too.

The S1 clean-up step (`src/xn/pipeline/s1_postprocess.py`) runs after S1's own category and evidence checks. It is off unless the config turns it on:

```yaml
s1:
  postprocess:
    name_filter: caps-proper+words   # a rule from xn.name_filter; null for none
    category_fix: true
    dedupe_events: true
```

- **name_filter** applies the G-26 rule `caps-proper+words`. It drops a name with its events, and clears the holder and `same_as` names that the rule drops.
- **category_fix** sets the category from the name's last word, lower case, with diacritics: "đan/hoàn/tán/thủy/phù" give `weapon_treasure`, "công/quyết/bộ/thuật/pháp/chú" give `technique`, and "môn/bang/cốc/phong/thôn" give `dungeon`. It never changes a `character`, and it ignores categories outside the profile.
- **dedupe_events** keeps the first of events that share name, category, event type, holder, `same_as` and realm, and differ only in quote and description.

With the section absent, S1 saves the same payload under the same job hash (the chapter's sha256) as before. With any fix on, the settings are hashed into the S1 job's input hash, so changing them reruns the chapter. `xn.name_filter --cleanup` scores the step offline on stored extractions. Its table now also shows category accuracy (C).

### Two v4 attempts on dev

Each attempt ran on the 10 dev chapters, one chapter at a time, in a new database, with the GPU checked free first. Attempt 2 also listed generic nouns as not entities, and asked for one event per entity and event type. The generic nouns were "thuốc trị ngoại thương", "bí dược", "kiếm mang", "căn nhà đá" and "Bang chủ Dã Lang", all dev false names of attempt 1.

| Dev chapters (10, 50 gold names) | v3 attempt 1 (G-26) | v4 attempt 1 (`6e05907`, **kept**) | v4 attempt 2 (`15066d4`) |
|---|---|---|---|
| Recall / precision, raw | 0.96 / 0.76 | 0.96 / 0.72 | 0.98 / 0.67 |
| Recall / precision, with clean-up | 0.96 / 0.92 | 0.94 / 0.85 | 0.98 / 0.84 |
| Category accuracy, raw / with clean-up | 0.83 / 0.94 | **1.00** / 1.00 | 0.98 / 0.98 |
| Event-type accuracy | 0.83 | 0.89 | 0.90 |
| `realm_breakthrough` F1 (gold in chapter 43 only) | 1.00 | 1.00 | 1.00 |
| Events per chapter | | 11.2 | 13.4 |
| s/chapter, mean / p95 | 18.6 / 29.0 | 19.6 / 35.5 | 20.5 / 37.4 |

- **The category definitions worked**: raw dev category accuracy rose from 0.83 to 1.00. Pills are no longer filed as `technique`.
- **The generic-noun rules did not.** In attempt 2 the model again wrote "thuốc trị ngoại thương", "bí dược" and "Bang chủ Dã Lang", the very examples it was told to skip. G-26 saw the same with v3. Attempt 2 also wrote more events and ran slower. v4 went back to the attempt 1 text (`fcf8787`), which has the same precision after clean-up and runs faster.
- The clean-up cost attempt 1 one dev gold name: "Khúc Hồn" (chapter 64). The model wrote it only as the holder of "căn nhà đá", which the name filter drops. S1's evidence check had already dropped its own event.

### Full test: S0-S3 on chapters 1-100

Real S0-S3 ran on chapters 1-100 in a new database, using a config copy with the qwen3.5 extractor, prompt v4 and every clean-up fix on. The GPU was checked free first. S1 made 100 calls in 29 min 21 s, with none failed or retried. The gold report then ran on that database. It ran again with the production config on a backup-API copy of `g101-full.db`, so that both reports used the same Qdrant chunks. The re-scored production column matches the G-10.1 numbers exactly.

| All 20 gold chapters | qwen3.5 9B, v4 + clean-up (**G-27**) | production, v2 (G-10.1) | Target |
|---|---|---|---|
| Entity recall | **0.96** (98 of 102) | 0.98 (100) | >= 0.90 |
| Entity precision | **0.90** (11 false names) | 0.88 (14) | >= 0.88 |
| Recall, dev / held out | 0.98 / **0.94** | 0.96 / 1.00 | |
| Precision, dev / held out | 0.89 / **0.91** | 0.86 / 0.90 | |
| Category accuracy | **0.98** (dev 0.98 / held out 0.98) | 0.91 | >= 0.90 |
| Event-type accuracy | 0.87 | 0.90 | |
| Flag F1, `location_change` / `realm_breakthrough` / `major_conflict` | 0.29 / 0.67 / 1.00 | 0.33 / 1.00 / 0.92 | |
| Alias F1 (precision / recall) | **0.67** (1.00 / 0.50) | 0.64 (1.00 / 0.48) | no worse |
| Q&A citation hit rate / context recall | **0.80** / 0.78 | 0.93 / 0.91 | no worse |
| Spoiler leaks (10 probes) | **0** | 0 | 0 |
| S1 s/chapter, mean / p95 (100 chapters) | **17.5** / 33.7 | 34.7 / 58.9 | <= 20 |
| Schema success / first try | 100% / 100% | 100% / 100% | |
| Events per chapter (after clean-up) | 8.2 (range 3-18) | 8.5 | |

- **Missed:** "Kim Quang thượng nhân" (chapter 84, dev), plus three held-out names: "Tiểu toán bàn" (17), "Mã phó môn chủ" (69) and "cự hán" (79). G-26 missed the same three.
- **False names left:** generic arts and items ("luyện công", "khẩu quyết", "bộ pháp", "kỹ xảo", "lệnh bài", "Trường xuân công khẩu quyết"), the dev group nicknames "Ngũ sát Tam ưng Nhị báo" and "Thất kiệt Song hùng" (possibly gold gaps), "Đinh tâm hoàn", "chẩm kích" and "đạo phù lục vũ hình thanh tiểu kiếm". Five of the 11 are in held-out chapters, three of them in chapter 48.
- **`realm_breakthrough`:** dev chapter 43 is now found; G-26 missed it. The F1 is 0.67 because of one flag set in a chapter whose gold has none. No held-out chapter has a gold breakthrough, so the held-out 0.00 means nothing.
- **Where the Q&A loss comes from.** `local_model(config)` also drives the S2 adjudicator and the query router, so this run swapped all three. Two cross-checks, each on a copy of its database, separate the parts:

| Ledger from | Router and adjudicator | Citation hit rate | Context recall | Alias F1 | Leaks |
|---|---|---|---|---|---|
| production (G-10.1) | production | 0.93 (miss q13) | 0.91 | 0.64 | 0 |
| **qwen3.5 (G-27)** | **production** | **0.93** (miss q14) | 0.91 | 0.67 | 0 |
| production (G-10.1) | qwen3.5 | 0.73 (miss q03, q08, q13, q15) | 0.71 | 0.67 | 0 |
| qwen3.5 (G-27) | qwen3.5 | 0.80 (miss q03, q08, q14) | 0.78 | 0.67 | 0 |

  With the production model routing, the qwen3.5 ledger loses nothing in Q&A. The qwen3.5 router misses q03 and q08 on either ledger. Alias F1 on the qwen3.5 ledger is 0.67 with either adjudicator.

Done-when status:

| Line | Status |
|---|---|
| Clean-up from config, off by default, with unit tests | met |
| Recall >= 0.90, precision >= 0.88, category >= 0.90 on the 20 gold chapters, held out apart | met: 0.96 / 0.90 / 0.98; held out 0.94 / 0.91 / 0.98 |
| Alias F1 and Q&A citation hit rate no worse than G-10.1, 0 leaks | aliases and leaks met. **Q&A not met** with the whole local model on qwen3.5 (0.80 against 0.93); met with qwen3.5 as the extractor only (0.93) |
| S1 <= 20 s/chapter mean | met: 17.5 |
| This section, with commands and caveats | met |
| `compileall` and unit tests | met |

**Recommendation:** use qwen3.5 9B with prompt v4 and the clean-up as the S1 extractor only, and keep `qwen3.8:udiq4_hui` as the query router and S2 adjudicator. That keeps production's Q&A and halves S1 time: about 12 hours for the 2,468 chapters at 17.5 s/chapter, against about 24. Today one config key, `models.local_extractor`, sets all three uses, so this needs a small code change: either a separate model key for the router and adjudicator, or an S1-only override. Both the change and the switch need the owner's yes. Switching the whole local model to qwen3.5 is not recommended, because of the Q&A loss.

Caveats:

- **These 20 gold chapters have judged every extraction run since G-10.1**, and prompt v2 was tuned on them for the production model. The held-out chapters were held out from G-26's and G-27's tuning only. The gold was drafted by Claude. Before attempt 2, a check confirmed that its new examples are not among G-26's held-out false names; the check printed nothing.
- **One run per setup** at temperature 0.1. Repeated dev runs have differed by up to 4 gold names (G-26 R-2), so differences of 1-3 names are not meaningful. The precision of 0.90 clears 0.88 by two false names.
- **The clean-up lists were drawn from dev errors.** The category word lists came from G-26's dev category errors, and the filter's word list partly from dev false names. Held-out category accuracy matched dev (0.98), but the v4 definitions had done most of that work before the fix ran.
- **Q&A ran against the live Qdrant** (`xn_chunks`, 704 points). Both reports and both cross-checks read the same collection, and S0-S3 do not write to it. With Qdrant off, the gate refuses nearly every question for both models (0.00 and 0.07), so the G-10.1 figure of 0.93 also relied on these chunks. The gold report's router and adjudicator calls write `llm_runs` rows, so the production runs used copies of `g101-full.db`, and the original was only read.
- **Timing:** S1 ran alone on the GPU. The production timing is from G-10.1 on 2026-11-01. `OLLAMA_NUM_PARALLEL` does not matter for qwen3.5, which loads with one slot (G-26).

Commands, from the repository root with `PYTHONIOENCODING=utf-8`:

- `<data>` is the main checkout's `data` folder.
- `<cfg>` is `<data>/bench/g27-full/configs`, a copy of `configs/`. Its `config.yaml` sets `models.local_extractor` to `qwen3.5:9b_UD_Q6` / `qwen35-9b-udq6-10k`, sets `prompts.s1_extract: v4`, and adds the `s1.postprocess` block above.
- `<prod>` is `<data>/bench/g27-full/prod-configs`, an unchanged copy of `configs/`.

```bash
uv run py -m xn.bench --raw-dir <data>/novel/pham_nhan_tu_tien_1 --out <data>/bench/g27-v4-a1 --chapter-max 100 --gold --gold-split dev --model qwen35-9b-udq6-10k --prompt v4
```

```bash
uv run py -m xn.name_filter --split dev --cleanup --db <data>/bench/g27-v4-a1/qwen35-9b-udq6-10k.db
```

Attempt 2 used the same commands with `--out <data>/bench/g27-v4-a2`. The full test runs stages `s0` (with `--raw-dir <data>/novel/pham_nhan_tu_tien_1`), `s1`, `s2` and `s3`, each like this:

```bash
uv run xn run --novel pntt1 --stage s1 --from 1 --to 100 --db <data>/bench/g27-full/g27-full.db --configs-dir <cfg>
```

```bash
uv run pytest tests/eval -m gold --novel pntt1 --gold-db=<data>/bench/g27-full/g27-full.db --gold-report=<data>/bench/g27-full/g27-full-report-q.json --gold-configs-dir=<cfg>
```

For the production column, run the same pytest command with `--gold-db=<data>/bench/g27-full/g101-copy.db --gold-configs-dir=<prod>`. The cross-checks run it on `g27-cross.db` with `<prod>`, and on `g101-cross.db` with `<cfg>`. The S1 timings come from `xn.bench._measure_runs` on each database.

All files are in `<data>/bench/g27-full/`:

- logs: `run.log`, `gold-q.log`, `gold-base-q.log`, `cross1.log` and `cross2.log`
- reports without Qdrant: `-report.json`
- reports with Qdrant: `-report-q.json`

### G-27.1: qwen3.5 for S1 only

A new optional `models.s1_extractor` block, shaped like `local_extractor`, sets the model S1 runs on. When it is set, S1 calls that model, applies its settings and records its serving name. The router, the S2 adjudicator, the S5 review and the prefix summaries stay on `local_extractor`. Without the block, every stage runs exactly as before. `xn.bench` puts its candidate on both keys, so a benchmark still runs the model it names.

Check: S2 (`--rebuild`) and S3 ran again on a copy of the G-27 database, followed by the gold report, all with a config copy. That config sets `s1_extractor` to qwen3.5, keeps `qwen3.8:udiq4_hui` as `local_extractor`, and sets prompt v4 with the clean-up on. The S1 extractions are the ones from the G-27 full test, since S1 does not depend on the other model. S2 made 42 adjudication calls on qwen3.8, and the report's 25 router and 10 adjudicator calls also ran on qwen3.8.

| All 20 gold chapters | qwen3.5 S1, qwen3.8 for the rest (**G-27.1**) | qwen3.5 for everything (G-27) | production (G-10.1) |
|---|---|---|---|
| Entity recall / precision | 0.96 / 0.90 | 0.96 / 0.90 | 0.98 / 0.88 |
| Category accuracy | 0.98 | 0.98 | 0.91 |
| Alias F1 | **0.644** | 0.667 | 0.644 |
| Q&A citation hit rate / context recall | **0.93** / 0.91 (miss q14) | 0.80 / 0.78 | 0.93 / 0.91 (miss q13) |
| Spoiler leaks | **0** | 0 | 0 |
| S1 s/chapter | 17.5 | 17.5 | 34.7 |

Every line of G-27.1 is met. Alias F1 equals production's to three places. In an earlier cross-check on the same ledger, the same production adjudicator scored 0.667, so alias F1 moves by about one pair between runs.

**Recommendation, updated:** in `configs/config.yaml`, add `models.s1_extractor` for `qwen3.5:9b_UD_Q6` (`qwen35-9b-udq6-10k`), set `prompts.s1_extract: v4`, and add the `s1.postprocess` block. Keep `local_extractor` on `qwen3.8:udiq4_hui`. That change needs the owner's yes. The new prompt version makes S1 run again on every chapter, which takes about 12 hours for the 2,468 chapters. S2 then has to be rebuilt.

Commands, with `<split>` = `<data>/bench/g27-full/split-configs` and `<db>` = `<data>/bench/g27-full/g27-split.db` (a copy of `g27-full.db`):

```bash
uv run xn run --novel pntt1 --stage s2 --from 1 --to 100 --rebuild --db <db> --configs-dir <split>
```

```bash
uv run xn run --novel pntt1 --stage s3 --from 1 --to 100 --db <db> --configs-dir <split>
```

```bash
uv run pytest tests/eval -m gold --novel pntt1 --gold-db=<db> --gold-report=<data>/bench/g27-full/g27-split-report.json --gold-configs-dir=<split>
```

The logs are `split-s2s3.log` and `gold-split.log` in `<data>/bench/g27-full/`.

## Reproduce

Paths outside the repo must use the `--option=value` form. With a space, pytest reads the path as a test target and takes its settings from the wrong checkout.

```bash
uv run xn run --novel pntt1 --stage s0 --from 1 --to 100 --raw-dir <raw chapters> --db <db>
```

Repeat with stages `s1`, `s2` and `s3`, then:

```bash
uv run pytest tests/eval -m gold --novel pntt1 --gold-db=<db> --gold-report=<report.json>
```

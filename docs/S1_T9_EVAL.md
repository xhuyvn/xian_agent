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

**`qwen2.5:14b-instruct` is 3.4 times faster, but its recall of 0.70 fails Sprint 1's 0.80 bar.** The production extractor stays `qwen3.8:udiq4_hui`. Nothing in config, the prompt or S1 changed. Running 2 chapters at once gave no speed-up.

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

The outputs are `report.md`, `report.json`, `parallel-qwen25-14b-10k-w2.md` and `.json` in `<data>/bench/g25-qwen25/`. The production column was read from `g101-full.db` with `xn.bench.pick_gold_chapters`, `_measure_runs` and `score_gold`, without writing to it.

## Reproduce

Paths outside the repo must use the `--option=value` form. With a space, pytest reads the path as a test target and takes its settings from the wrong checkout.

```bash
uv run xn run --novel pntt1 --stage s0 --from 1 --to 100 --raw-dir <raw chapters> --db <db>
```

Repeat with stages `s1`, `s2` and `s3`, then:

```bash
uv run pytest tests/eval -m gold --novel pntt1 --gold-db=<db> --gold-report=<report.json>
```

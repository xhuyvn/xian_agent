# S1-T9 gold evaluation

Date: 2026-11-01. Goal: G-10.

**Extraction recall is 0.68, below Sprint 1's 0.80.** Sprint 1 criterion 3 is not met. The gold and the prompt were left unchanged, as agreed. Everything else measured here has no Sprint 1 threshold.

The gold was drafted by Claude from the chapter text, not by a person (`annotator: claude` in `eval/pntt1/gold/meta.json`), and committed before any scoring. It covers chapters 1-100 only. A person's spot-check would make these numbers firmer.

## Results

`qwen3.8:udiq4_hui` through the real S0-S3 code on chapters 1-100, then `pytest tests/eval -m gold`. Thresholds in brackets come from blueprint §3.8 and apply in Sprint 4, except recall at 0.80, which is Sprint 1's.

| Metric | Result | Threshold |
|---|---|---|
| Entity recall | **0.68** (69 of 102) | 0.80 now, 0.85 in S4 |
| Entity precision | 0.86 (11 false names) | 0.90 in S4 |
| Category accuracy | 0.92 | none |
| Event-type accuracy | 0.87 | 0.75 in S4 |
| Flag F1, `realm_breakthrough` | 1.00, on 1 gold chapter | 0.80 in S4 |
| Flag F1, `major_conflict` | 0.86 | 0.80 in S4 |
| Flag F1, `location_change` | 0.50, on 2 gold chapters | 0.80 in S4 |
| Alias pairwise F1 | 0.67 (precision 1.00, recall 0.50) | 0.90 in S4 |
| Q&A citation hit rate, evidence only | 0.80 (12 of 15, none refused) | 0.80 in S4 |
| Q&A context recall, evidence only | 0.72 | 0.75 in S2 |
| Spoiler leaks, evidence and suggestions | 0 of 10 probes | 0 |
| Evidence above the bound | 0 | 0 |

Q&A and spoiler scores cover what the graph retrieves and shows before any cloud call, not the written answers. The spoiler result does not replace the Sprint 2 check on real answers. S1 took 34 minutes for 100 chapters, about 20.4 s/chapter. RAGAS and boundary F1 were not run.

## Why recall is low

- **The model writes fewer events than the gold has entities.** The mean is 4.0 events per chapter, with a range of 1-8 and none empty, against 5.1 gold entities per chapter. This fits the misses but does not prove the cause.
- **It misses most on crowded chapters.** Chapter 84 lost 5 of 8 entities: its three events are all techniques, and no character is named. Chapters 69 and 95 lost 4 of 7 and 4 of 8. Those three plus chapter 79 hold 16 of the 33 misses, and chapter 27 holds 6.
- **The protagonist is not always named.** "Hàn Lập" is missing from chapters 53, 58 and 79.
- **Malformed names are a smaller cause.** In chapter 27 four pills arrive as one comma-joined name, and "phi kiếm (ánh sáng mờ mờ" is cut mid-bracket. Counting a gold name inside any extracted name lifts recall only to 0.73, so better name cleaning alone will not reach 0.80.
- **Not yet known:** how many good events S1's evidence check dropped. Dropped events are logged, not stored, so this run cannot show it.

## Other findings

- **Alias recall is 0.50 because `seed_aliases` is empty.** All 20 wrong pairs are titles, nicknames and descriptions S2 can't guess, such as Hàn thần y, Mặc lão, Chu Nho and cự hán. It merged no pair that should stay apart, including Trương Thiết / Khúc Hồn. `entity_links` is empty, so no reveal was linked in the first 100 chapters.
- **`location_change` F1 rests on 2 chapters**, so 0.50 says little. This answers the S1-T7 question only loosely.
- **Q&A misses:** q05, q10 and q13. Their answer chapters are not in the evidence. The cause was not traced.
- **Seven names became two entities each**: Dã lang bang, Dư Tử Đồng, Dưỡng tinh đan, Thi Trùng Hoàn, Thiết nô, Thất huyền môn and Trát nhãn kiếm pháp, each under two categories. G-8 saw the same split.

## Sprint 1 criteria

1. **Identical row counts: met.** S0-S3 ran twice on the same database, with 100 chapters, 183 entities, 209 aliases, 399 events and 400 jobs both times. The second run made no model calls and wrote nothing.
2. **Deep-dive on 3 entities cites only chapters <= N: met live.** Through OpenRouter at N = 60, on the real ledger:

   | Entity | Citations | Grounding |
   |---|---|---|
   | Mặc đại phu | 15-59, 11 chapters | 86% |
   | Lệ Phi Vũ | 21, 33, 39, 43 | 40% |
   | Hàn Lập | 1-60, 13 chapters | 20% |

   Spend was $0.0025 for 3 answers. Grounding is the share of answer sentences that carry a citation, so most Hàn Lập sentences carry none. Two problems showed up on the way. One of the 5 deep-dive router calls failed on a context overflow (10,295 tokens against 10,240) and fell back to a refusal; the 25 router calls in the gold run all worked. OpenRouter also returned connection errors on 5 calls, and a repeat run answered.
3. **Extraction recall >= 0.80: not met.** 0.68.
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

## Sprint 1 recheck

An independent verifier pass at `c53d1e9` read every "Done when" line of G-1 to G-9 against the code as it stands, not against the round logs. All are met, or were superseded with the owner's yes (the extractor model, through G-8). Recall is the only failing criterion.

Open items it found, none blocking:

- **`total_chapters` is stale.** Config, the `novels` row and the blueprint say 2,673, and the folder holds 2,468 contiguous chapters. A bare `xn run --stage s0` aborts on 205 missing chapters, and `xn ask --full` bounds at 2,673. The default raw folder does not match the real one either.
- **A follow-up may break on a future langgraph.** Each real follow-up logs that `RouterDecision`, `EvidencePack` and `Answer` are unregistered types that will be blocked later. `test_checkpoint_round_trip_gives_no_serializer_warning` can't see this, because the warning goes through logging.
- **`test_criterion_8` depends on the console encoding.** It fails when output is UTF-8 and the reader uses cp1252.
- **Not logged in `docs/GOALS.md`:** `cloud_spend_since` is a third exempt repository method, and `src/storage/qdrant_client.py` also keeps `src/config.py` alive.
- **Stale text:** `README.md` still names Redis, blueprint §3.9 still shows `FROM qwen2.5:14b`, and `s0_parse.py` line 43 raises a SyntaxWarning on every run.
- **Sprint 2 starts below its bar:** evidence-only context recall is 0.72 against 0.75.
- **S1 speed:** this run measured 20.4 s/chapter on chapters 1-100, against the blueprint's 27.2 from 50 chapters across 1-1121.
- **Nobody owns:** CI, ruff and mypy (OQ-03).

## Reproduce

Paths outside the repo must use the `--option=value` form. With a space, pytest reads the path as a test target and takes its settings from the wrong checkout.

```bash
uv run xn run --novel pntt1 --stage s0 --from 1 --to 100 --raw-dir <raw chapters> --db <db>
```

Repeat with stages `s1`, `s2` and `s3`, then:

```bash
uv run pytest tests/eval -m gold --novel pntt1 --gold-db=<db> --gold-report=<report.json>
```

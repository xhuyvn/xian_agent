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

- **The model writes too few events.** The mean is 4.0 per chapter, with a range of 1-8 and none empty. The gold has 5.1 entities per chapter.
- **It misses most on crowded chapters.** Chapter 84 lost 5 of 8 entities: its three events are all techniques, and no character is named. Chapters 69 and 95 lost 4 of 7 and 4 of 8. Those three plus chapter 79 hold 16 of the 33 misses, and chapter 27 holds 6.
- **The protagonist is not always named.** "Hàn Lập" is missing from chapters 53, 58 and 79.
- **Malformed names are a smaller cause.** In chapter 27 four pills arrive as one comma-joined name, and "phi kiếm (ánh sáng mờ mờ" is cut mid-bracket. Counting a gold name inside any extracted name lifts recall only to 0.73, so better name cleaning alone will not reach 0.80.
- **Not yet known:** how many good events S1's evidence check dropped. Dropped events are logged, not stored, so this run cannot show it.

## Other findings

- **Alias recall is 0.50 because `seed_aliases` is empty.** All 20 wrong pairs are titles and nicknames S2 can't guess, such as Hàn thần y, Mặc lão and Chu Nho. It merged no pair that should stay apart, including Trương Thiết / Khúc Hồn. `entity_links` is empty, so no reveal was linked in the first 100 chapters.
- **`location_change` F1 rests on 2 chapters**, so 0.50 says little. This answers the S1-T7 question only loosely.
- **Q&A misses:** q05, q10 and q13. Their answer chapters are not in the evidence. The cause was not traced.
- **Four names became two entities each**: Dư Tử Đồng, Dưỡng tinh đan, Thi Trùng Hoàn and Trát nhãn kiếm pháp. S2 matches within one category, so a name filed under two categories splits, as G-8 saw.

## Sprint 1 criteria

1. **Identical row counts: met.** S0-S3 ran twice on the same database, with 100 chapters, 183 entities, 209 aliases, 399 events and 400 jobs both times. The second run made no model calls and wrote nothing.
2. **Deep-dive on 3 entities cites only chapters <= N: met live.** Through OpenRouter at N = 60, on the real ledger:

   | Entity | Citations | Grounding |
   |---|---|---|
   | Mặc đại phu | 15-59, 11 chapters | 86% |
   | Lệ Phi Vũ | 21, 33, 39, 43 | 40% |
   | Hàn Lập | 1-60, 13 chapters | 20% |

   Spend was $0.0025 for 3 answers. Grounding is the share of answer sentences that carry a citation, so most Hàn Lập sentences carry none. Two problems showed up on the way. One router call failed on a context overflow (10,295 tokens against 10,240) and fell back to a refusal; 29 of 30 router calls worked. OpenRouter also returned connection errors on 5 calls, and a repeat run answered.
3. **Extraction recall >= 0.80: not met.** 0.68.
4. **Measured s/chapter in the blueprint: met** by G-8, at 27.2 s/chapter.

## Reproduce

Paths outside the repo must use the `--option=value` form. With a space, pytest reads the path as a test target and takes its settings from the wrong checkout.

```bash
uv run xn run --novel pntt1 --stage s0 --from 1 --to 100 --raw-dir <raw chapters> --db <db>
```

Repeat with stages `s1`, `s2` and `s3`, then:

```bash
uv run pytest tests/eval -m gold --novel pntt1 --gold-db=<db> --gold-report=<report.json>
```

# S1 DeepSeek direct benchmark

Date: 2026-11-02. Goal: G-14.

**Two chapters per call did not beat one chapter per call, so one chapter per call stays.** The rule needed name recall and precision not to drop and at least one flag score to rise. Recall fell from 1.00 to 0.98, precision rose from 0.86 to 0.89, and no flag score rose. Two-chapter calls cost 15% less, but kept fewer events per chapter (9.0 against 10.1). The extractor has not changed: production S1 stays local, and raw text went to DeepSeek for this test only, as a one-off exception to A-04.

## Results

`deepseek-flash` (V4.1-Flash-1017) with thinking off and concurrency 10. Both modes ran the same 40 chapters: every fixed pair (1-2, 3-4, ...) in chapters 1-100 that holds a gold chapter. Scores cover the 20 gold chapters. The OpenRouter column is G-13, the same model through OpenRouter. The local column is the G-10.1 run of `qwen3.8:udiq4_hui`.

| Metric | DeepSeek, 1 chapter | DeepSeek, 2 chapters | OpenRouter | Local |
|---|---|---|---|---|
| Entity recall | 1.00 (102 of 102) | 0.98 (100 of 102) | 1.00 | 0.98 |
| Entity precision | 0.86 (17 false names) | 0.89 (12 false names) | 0.94 (6) | 0.88 (14) |
| Category accuracy | 0.94 | 0.98 | 0.97 | 0.91 |
| Event-type accuracy | 0.91 | 0.85 | 0.90 | 0.90 |
| Flag F1, `realm_breakthrough` | 1.00, on 1 gold chapter | 0.00 | 0.00 | 1.00 |
| Flag F1, `major_conflict` | 0.92 | 0.77 | 0.75 | 0.92 |
| Flag F1, `location_change` | 0.40 | 0.40 | 0.36 | 0.33 |

| Gold chapters | 1 chapter recall | 1 chapter precision | 2 chapters recall | 2 chapters precision |
|---|---|---|---|---|
| All 20 | 1.00 | 0.86 | 0.98 | 0.89 |
| Dev, 10 | 1.00 | 0.83 | 1.00 | 0.93 |
| Held out, 10 | 1.00 | 0.88 | 0.96 | 0.86 |

## Reliability, speed and cost

| Measure | 1 chapter | 2 chapters |
|---|---|---|
| Chapters saved | 40 of 40 | 40 of 40 |
| Calls | 40, with 2 schema retries | 20, with no retries |
| HTTP 429 errors | 0 | 0 |
| Seconds per call | mean 8.3, p95 10.7 | mean 13.7, p95 17.0 |
| Wall time | 36.5 s, 0.91 s/chapter | 32.0 s, 0.80 s/chapter |
| Tokens per chapter | 5,532 input, 1,870 output | 4,143 input, 1,541 output |
| Cache hit rate | 43% | 31% |
| Events kept | 404, 10.1 per chapter | 360, 9.0 per chapter |
| Events dropped | 1, low evidence | 1, misattributed |
| Cost, off-peak | $0.0642 | $0.0544 |

| All 2,468 chapters | 1 chapter | 2 chapters | OpenRouter |
|---|---|---|---|
| S1 time | about 38 minutes | about 33 minutes | about 1.3 hours |
| Cost, off-peak | about $3.96 | about $3.36 | about $5.41 |
| Cost, peak | about $7.92 | about $6.71 | about $5.41 |

These are straight-line estimates from 40 chapters in 1-100, so later, longer chapters may differ, and so may the cache hit rate. They assume the same prices and rate limits at full-book scale. Peak is 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday.

Cost is worked out from DeepSeek's listed prices and the tokens it reported, because the API reports no cost. The runner ignores DeepSeek's Chinese public holiday exception, but this run was off-peak either way. Both runs and the two pilot runs together come to $0.1265. Check that against the DeepSeek dashboard.

## How to read this

- **What two-chapter calls lost.** Two gold names went missing (chapter 48 and chapter 79), event-type accuracy fell, chapter 43's `realm_breakthrough` was missed, and `major_conflict` fell. Precision rose on the dev chapters but fell on the held-out chapters.
- **Misattribution was rare.** One event of the 361 that passed the category check quoted the partner chapter. The check only finds quotes in the wrong chapter. It can't find a wrong flag or summary.
- **The same model scored differently by route.** With one chapter per call, DeepSeek direct gave 11 more false names than OpenRouter, and scored higher on all three flags. The chapter sets differ for the non-gold chapters, but the 20 scored chapters are the same. The routes also differ in output mode: JSON schema on OpenRouter, a tool call on DeepSeek. The cause was not tested, and each route ran once.
- **Flag scores move a lot on one chapter.** `realm_breakthrough` rests on one gold chapter, and the other flags on few. Flags on the 20 non-gold chapters are not scored.
- **Name agreement with local** on the 20 non-gold chapters: Jaccard 0.69 for both modes. Nothing scores the names that differ.
- **Run order.** One-chapter calls ran first and may have warmed the cache for two-chapter calls, whose system prompt starts the same way. That can only flatter the two-chapter cost.
- **Not tested:** S2 and S3 on this output, concurrency above 10, and peak-hour speed. The hold-out has the same caveats as `docs/S1_T9_EVAL.md`, and the gold was drafted by Claude.

## Reproduce

Run from the checkout that holds the DeepSeek settings. This sends raw chapter text to DeepSeek.

```bash
uv run python -m xn.cloud_bench --db=data/eval/deepseek-single.db --out=data/eval/deepseek-single.json --raw-dir=data/novel/pham_nhan_tu_tien_1 --baseline-db=data/eval/g101-full.db --provider deepseek --gold-pairs --concurrency 10 --allow-raw-egress
```

```bash
uv run python -m xn.cloud_bench --db=data/eval/deepseek-pair.db --out=data/eval/deepseek-pair.json --raw-dir=data/novel/pham_nhan_tu_tien_1 --baseline-db=data/eval/g101-full.db --provider deepseek --gold-pairs --pairs --concurrency 10 --allow-raw-egress
```

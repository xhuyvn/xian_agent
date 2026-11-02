# S1 cloud extraction benchmark

Date: 2026-11-02. Goal: G-13.

**`deepseek/deepseek-v4.1-flash-1017` beat the local extractor on names and was far faster at concurrency 10, but it scored worse on `major_conflict` and `realm_breakthrough`.** Recall is 1.00 against 0.98 and precision 0.94 against 0.88. All 50 chapters cost $0.11. The extractor has not changed: production S1 stays local, and raw text left the machine for this benchmark only, as a one-off exception to A-04.

## Results

Prompt v2 (`6f35c798`), unchanged, on 50 chapters from 1-100: the 20 gold chapters and 30 others. Scores cover the 20 gold chapters. The local column is the G-10.1 full run of `qwen3.8:udiq4_hui` in `g101-full.db`.

| Metric | Cloud | Local |
|---|---|---|
| Entity recall | **1.00** (102 of 102) | 0.98 (100 of 102) |
| Entity precision | **0.94** (6 false names) | 0.88 (14 false names) |
| Category accuracy | 0.97 | 0.91 |
| Event-type accuracy | 0.90 | 0.90 |
| Flag F1, `realm_breakthrough` | 0.00, on 1 gold chapter | 1.00 |
| Flag F1, `major_conflict` | 0.75 | 0.92 |
| Flag F1, `location_change` | 0.36 | 0.33 |

| Gold chapters | Cloud recall | Cloud precision | Local recall | Local precision |
|---|---|---|---|---|
| All 20 | 1.00 | 0.94 | 0.98 | 0.88 |
| Dev, 10 | 1.00 | 0.94 | 0.96 | 0.86 |
| Held out, 10 | 1.00 | 0.95 | 1.00 | 0.90 |

## Reliability, speed and cost

Concurrency 10, on a fresh database.

| Measure | Result |
|---|---|
| Schema success | 100% (50 of 50) |
| First-try success | 88% (44 of 50) |
| Attempts | 55, with 5 connection errors and 1 schema retry |
| HTTP 429 errors | 0 |
| Seconds per call | mean 12.4, p95 31.8 |
| Wall time | 96.6 s, 1.9 s/chapter |
| Tokens per chapter | 4,873 input, 2,165 output |
| Cost | $0.1096 in total, $0.0022 per chapter |
| Events kept | 473, 9.5 per chapter |
| Events dropped | 1, low evidence |

| All 2,468 chapters | Cloud at concurrency 10 | Local |
|---|---|---|
| S1 time | about 1.3 hours | about 24 hours |
| Cost | about $5.41 | no API cost |

Both are straight-line estimates from chapters 1-100, so later, longer chapters may differ. They assume the same price, and the same rate limits at full-book scale. The 3-chapter pilot cost another $0.0028, so G-13 spent $0.1124 in total.

## How to read this

- **Cloud sets flags on more chapters than the gold does.** On `major_conflict` cloud marked 4 chapters that the gold says have none (17, 22, 53 and 58), where local marked one. On `location_change` it marked 7 extra chapters, against 3 for local. It also missed the one gold `realm_breakthrough`, chapter 43, so that 0.00 rests on a single chapter. Flag gold rests on few positive chapters, so these scores move a lot on one chapter.
- **Name agreement with local:** Jaccard 0.71 on the 30 non-gold chapters, with 202 names shared, 54 cloud only and 30 local only. Nothing scores those 84 differing names, so this shows how far the two differ, not which is right.
- **The connection errors came in one burst.** All 5 failed in about 0.3 s, near the start of the run, cost nothing and passed on retry. The cause was not traced. The pilot database shows 4 failed attempts: one connection error at concurrency 3, and 3 from an earlier launch that stopped before sending anything, because JSON-schema output was off.
- **Concurrency stayed at 10.** With no 429s, the 20 limit may also work, but it was not tested.
- **S2 and S3 were not run on the cloud output**, so nothing here shows how many duplicate entities it would produce, or the effect on Q&A and aliases.
- **The hold-out was already seen.** Prompt v2 was tuned on the local model and not changed here, but this is the same data as G-10.1, with the same caveats in `docs/S1_T9_EVAL.md`. The gold was drafted by Claude.
- **Reasoning was off**, matching `think: false` locally. One run of each model; repeats were not measured.
- **Model output mode:** pydantic-ai assumes DeepSeek models lack JSON-schema output, so `bench_model` turns it on for this benchmark. OpenRouter lists `structured_outputs` for this model, and all 50 chapters returned valid output.

## Reproduce

Run from the checkout that holds the OpenRouter settings. This sends raw chapter text to OpenRouter.

```bash
uv run python -m xn.cloud_bench --db=data/eval/cloud-bench.db --raw-dir=data/novel/pham_nhan_tu_tien_1 --out=data/eval/cloud-bench.json --baseline-db=data/eval/g101-full.db --concurrency 10 --allow-raw-egress
```

Counts of 429s, drops and wall time are written when a run ends. If a run is killed part-way, its saved chapters are kept but those counts are lost; tokens and cost come from `llm_runs`, so they stay right. Time spent on a failed chapter counts toward s/chapter, so that figure can read slightly high.

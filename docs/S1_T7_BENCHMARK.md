# S1-T7 extractor benchmark

Date: 2026-10-31. Goal: G-7. Settles BP-OQ-1 for the installed models.

**Result: `deepseek-r1:14b` is the production extractor**, served as `deepseek-r1-14b-12k` with `output_mode: native`.

Both models passed the schema gate, and deepseek-r1 has the higher recall by 13 points, outside the 2-point margin. The rule was fixed before the run.

## Results

50 chapters evenly spaced across 1-1121, through the real S0 and S1 code, one chapter at a time, on an RTX 5070 Ti 16 GB. Both used JSON-schema output, `num_ctx 12288` and temperature 0.1.

| Metric | `qwen3.5:Q8` (thinking off) | `deepseek-r1:14b` |
|---|---|---|
| Schema success | 100% | 100% |
| First-try success | 100% | 100% |
| Pooled recall | 0.51 | 0.64 |
| Names found in the chapter text | 114 | 143 |
| Names not in the chapter text | 222 | 34 |
| Events per chapter | 7.9 | 4.1 |
| s/chapter, mean | 14.8 | 18.3 |
| s/chapter, p95 | 28.2 | 29.7 |
| Input tokens per chapter, mean | 3,160 | 3,484 |
| Output tokens per chapter, mean | 1,008 | 1,145 |
| Peak VRAM, full run (MiB) | 12,944 | 12,775 |
| Peak VRAM, clean test (MiB) | 12,528-12,543 | 11,697-11,785 |

## How to read this

- **Recall is relative.** The reference is the union, per chapter, of both models' evidence-checked names that appear in the chapter text. It shows which model finds more, not how much either misses. It does not satisfy Sprint 1's recall >= 0.80, which stays with S1-T9.
- **qwen3.5 fills `name` with event descriptions**, such as "Hàn Lập đến phòng Nghiêm Thị". This explains its high event count and its 222 names not in the text. Those names would reach the S2 entity table as false entities.
- **deepseek-r1 is not clean either.** 36 of its names failed the text check, including combined names ("Hư Thiên Đỉnh và Bổ Thiên Đan") and English translations ("Jade Tablet"). It also extracts about half as many events.
- **deepseek-r1 cannot turn thinking off.** It still thinks with `think: false`, so its s/chapter includes reasoning.
- **deepseek-r1 ignores tool calling**, in one direct test on chapter 1. It needs `output_mode: native`.

## VRAM

The harness's idle reading for the first model was wrong in both runs (11,978 and 12,866 MiB). The cause was not found. It was not a model listed by `ollama ps`, and stopping an unloaded model does not load it. That makes the full-run peak for `qwen3.5:Q8` doubtful, so both models were remeasured.

The clean test stopped both models, read idle at about 1.8 GB, then ran the longest chapter (22,312 characters) on each model, twice. In this test `ollama ps` showed 10 GB and 100% GPU for both.

The two tests disagree by about 1 GB for deepseek-r1. Plan on the higher figure: about 12.8 GB, which fits the 16 GB card with room to spare. `OLLAMA_KV_CACHE_TYPE=q8_0` was set. `OLLAMA_NUM_PARALLEL` was not set, so two slots were not tested.

## Token calibration

`estimate_tokens` now divides by 3.3 instead of 1.6. Exact Ollama token counts on 40 chapters:

| Model | Chars per token, overall | Lowest chapter |
|---|---|---|
| `qwen35-q8-12k` | 3.78 | 3.68 |
| `deepseek-r1-14b-12k` | 3.39 | 3.29 |

At 1.6, 227 of the first 1,121 chapters counted as over the 7,000-token limit and would have split. At 3.3 that falls to about 3. The longest chapter reached 6,665 real tokens with `deepseek-r1-14b-12k`.

Output ran well past the blueprint's 1.5k budget for `qwen3.5:Q8` (up to 2,853 here, 3,322 in the 5-chapter run). `deepseek-r1:14b` peaked at 1,955.

## Full-book estimate

2,673 chapters at 18.3 s is about 13.6 h in one slot, against the 20 h target. Only 2,467 chapter files exist today, which is about 12.5 h. The benchmark saw no chapter split, and no retry, so failures in a full run would add time. Chapters after 1121 were not sampled.

## Limits of this run

- Chapter 1122 is missing, so S0 stops there and nothing after it was tested.
- `configs/novels/pntt1.yaml` has no realm ladder, seed aliases or cleaning patterns yet.
- The other 2-slot and `num_ctx` tuning in the blueprint was not tested.

## Reproduce

```bash
ollama create qwen35-q8-12k -f ollama/Modelfile.qwen35-q8-12k
```

```bash
ollama create deepseek-r1-14b-12k -f ollama/Modelfile.deepseek-r1-14b-12k
```

```bash
uv run python -m xn.bench --raw-dir <raw chapters> --out <output dir> --count 50
```

Reruns resume finished chapters, so use an empty output directory for a fresh measurement.

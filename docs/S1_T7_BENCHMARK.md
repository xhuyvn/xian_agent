# S1-T7 extractor benchmark

Date: 2026-10-31. Goal: G-7. Settles BP-OQ-1 for the installed models.

**Current production extractor: `qwen3.8:udiq4_hui`**, served as `qwen38-udiq4-hui-10k` with `output_mode: native` and `think: false`. The owner picked it in G-8; see the G-8 sections below.

The rest of this first part records the G-7 result, when `deepseek-r1:14b` won. Both models passed the schema gate, and deepseek-r1 has the higher recall by 13 points, outside the 2-point margin. The rule was fixed before the run.

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
- **deepseek-r1 is not clean either.** 34 of its names failed the text check, including combined names ("Hư Thiên Đỉnh và Bổ Thiên Đan") and English translations ("Jade Tablet"). It also extracts about half as many events.
- **deepseek-r1 cannot turn thinking off.** It still thinks with `think: false`, so its s/chapter includes reasoning.
- **deepseek-r1 ignores tool calling**, in one direct test on chapter 1. It needs `output_mode: native`.

## End-to-end check

`xn run` with the production config, S0 to S3, on chapters 1-15: every stage finished with 0 failures, with 15 S1 calls and 4 real S2 adjudications, none retried. S1 took 16.5 s/chapter. The run produced 26 entities, 27 aliases and 53 ledger events.

**Open quality problem: deepseek-r1 translates some names into English.** The 26 entities include "Han Li" next to "Hàn Lập", and "Mysterious Bottle", "Mysterious Vase" and "Small white vase" next to "Bình tử". S2 keeps these as separate entities, so one character or item is split. Names that combine two entities ("Hàn Lập và Trương Thiết") and one mixed-script name ("Chiếc bình神秘") also appear. The benchmark's text check keeps these out of recall, but S2 receives them. This should be fixed before a full ingest, probably in the prompt. It does not change the winner, because `qwen3.5:Q8` writes far more bad names.

S2's adjudicator also needed `output_mode: native`, so `src/xn/pipeline/s2_resolve.py` changed too. With tool output, every adjudication would have failed on this model.

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

At 1.6, 227 of the first 1,121 chapters counted as over the 7,000-token limit and would have split. At 3.3 none do. The longest chapter reached 6,665 real tokens with `deepseek-r1-14b-12k`.

Output ran well past the blueprint's 1.5k budget for `qwen3.5:Q8` (up to 2,853 here, 3,322 in the 5-chapter run). `deepseek-r1:14b` peaked at 1,955.

## Full-book estimate

2,673 chapters at 18.3 s is about 13.6 h in one slot, against the 20 h target. Only 2,467 chapter files exist today, which is about 12.5 h. The benchmark saw no chapter split, and no retry, so failures in a full run would add time. Chapters after 1121 were not sampled.

## Limits of this run

- Chapter 1122 was missing at the time, so S0 stopped there and nothing after it was tested. See the G-8 section.
- `configs/novels/pntt1.yaml` has no realm ladder, seed aliases or cleaning patterns yet.
- The blueprint's 2-slot setup and `num_ctx 10240` fallback were not tested.

## G-8: `qwen3.8:UD_Q3` against `deepseek-r1:14b`

Date: 2026-11-01. `qwen3.8:UD_Q3` beat `deepseek-r1:14b`. The next section compares it with `qwen3.8:udiq4_hui`, the model the owner picked.

The same 50 chapters through the real S0 and S1 code. `qwen3.8:UD_Q3` ran at `num_ctx 10240` with thinking off. `deepseek-r1:14b` keeps its G-7 results at 12288. Recall here uses a new pool of these two models, so these numbers can't be compared with the G-7 table.

| Metric | `qwen3.8:UD_Q3` | `deepseek-r1:14b` |
|---|---|---|
| Schema success | 100% | 100% |
| Pooled recall | **0.75** | 0.47 |
| Names found in the chapter text | 228 | 144 |
| Names not in the chapter text | 30 | 32 |
| Pure English names | 1 | 15 |
| Names with Chinese characters | 0 | 3 |
| Events per chapter | 5.4 | 4.1 |
| s/chapter, mean | 15.8 | 18.5 |
| s/chapter, p95 | 24.9 | 29.7 |
| Output tokens per chapter, mean | 673 | 1,141 |
| Peak VRAM (MiB) | 14,783 | 12,775 (G-7 run) |
| Chapters flagged `location_change` | 7 | 41 |
| Chapters flagged `major_conflict` | 26 | 45 |
| Chapters naming "Hàn Lập" (all 50 mention him) | 15 | 28 |

The harness reported 98% first-try success, 18.8 s/chapter and 10,919 MiB for `deepseek-r1:14b`. All three come from a repeat of chapter 1121, described below. The table counts that chapter once, and takes peak VRAM from the G-7 run. The English count uses a pattern match, and "Hoa Lam", the one hit for `qwen3.8:UD_Q3`, is an unaccented name rather than a translation.

Recall by category, as names found in the text:

| Category | `qwen3.8:UD_Q3` | `deepseek-r1:14b` |
|---|---|---|
| `weapon_treasure` | 76 | 32 |
| `technique` | 44 | 21 |
| `character` | 63 | 74 |
| `puppet_beast` | 28 | 11 |
| `dungeon` | 17 | 8 |

### End-to-end check

`xn run` S0 to S3 on chapters 1-15 with `qwen3.8:UD_Q3` selected in a temporary config: every stage finished with 0 failures, with 15 S1 calls and 3 real S2 adjudications, none retried. S1 took 10.1 s/chapter. The run produced 27 entities, 30 aliases and 42 ledger events.

The entity list has no English names and no combined names, and "Hàn Lập" is a single entity. In the same test `deepseek-r1:14b` produced "Han Li", "Mysterious Bottle" and "Hàn Lập và Trương Thiết". The list is not clean: the same bottle appears as "Bình bí ẩn" and "Thần bí bình tử", and "Thất Tuyệt đường" is filed as a `character`.

### Trade-offs

- **`qwen3.8:UD_Q3` is stronger on** recall, name quality and speed. It finds about twice as many items, techniques and places.
- **`qwen3.8:UD_Q3` is weaker on** memory and characters. It leaves about 1.5 GB of the 16 GB free, against about 3.4 GB. It lists the protagonist less often, and finds slightly fewer characters.
- **Query time may not fit.** Blueprint §3.9 keeps the local model and the 1.2 GB reranker on the GPU together. With this model that reaches about 16 GB, so it needs testing when the reranker exists, or the model must unload first.
- **Two slots are unlikely.** There is little memory left for a second slot.
- **The flags are unchecked.** `deepseek-r1:14b` flags a location change in 82% of chapters and `qwen3.8:UD_Q3` in 14%. The first looks like over-flagging, but no gold set exists to confirm either. These flags feed arc detection, so S1-T9 should check them.

### Context size

Tested on the longest chapter with thinking off, `qwen3.8:UD_Q3` stayed 100% on the GPU at every size. Peak VRAM was 14,766 MiB at 8192, 14,819 at 10240 and 14,895 at 12288. In the 50 chapters the largest input plus output was 4,867 tokens, and the longest chapter uses about 7,600. So 10240 leaves room. Thinking must stay off: at default thinking, chapter 1 used all of an 8192 context and returned nothing.

Thinking is off only because `think: false` is set. If this model is picked, config must carry that line.

### Full-book estimate

2,673 chapters at 15.8 s is about 11.7 h in one slot, against 13.7 h for `deepseek-r1:14b` at 18.5 s. The raw folder holds 2,468 chapters, which is about 10.8 h.

### Source changes during the benchmark

`chapter_1122.md` now exists, so the gap the G-7 sampling worked around is closed, and the folder holds chapters 1-2468 with none missing. `chapter_1121.md` also changed, with a new title and text. The 50 chapters are unchanged and both models here were scored on the new chapter 1121. Chapters after 1121 are still unsampled, and `total_chapters` still says 2673.

## G-8: `qwen3.8:udiq4_hui` against `qwen3.8:UD_Q3`

Date: 2026-11-01. **Picked by the owner: `qwen3.8:udiq4_hui`**, now set in `configs/config.yaml`. It wins under the agreed rule, with recall 8 points higher, and it closes `qwen3.8:UD_Q3`'s gap on characters. It is a close call: speed is not part of the rule outside the 2-point margin, and this model costs about 72% more time. Its estimate for 2,673 chapters is just over the 20 h target, and that estimate is a floor.

The same 50 chapters through the real S0 and S1 code, both at `num_ctx 10240` with thinking off. `qwen3.8:UD_Q3` keeps its results from the previous section. Recall uses a new pool of these two models, so these numbers can't be compared with the earlier tables.

| Metric | `qwen3.8:udiq4_hui` | `qwen3.8:UD_Q3` |
|---|---|---|
| Quantization | IQ4_XS | Q3_K_L |
| Schema success | 100% | 100% |
| Pooled recall | **0.84** | 0.76 |
| Names found in the chapter text | 253 | 228 |
| Names not in the chapter text | 27 | 30 |
| Pure English names | 1 | 1 |
| Events per chapter | 6.0 | 5.4 |
| s/chapter, mean | 27.2 | **15.8** |
| s/chapter, p95 | 40.2 | **24.9** |
| Output tokens per chapter, mean | 731 | 673 |
| Peak VRAM, 50 chapters (MiB) | 15,425 | **14,783** |
| Peak VRAM, longest chapter at 10240 (MiB) | 15,678 | **14,819** |
| GPU share | 92% | **100%** |
| Chapters flagged `location_change` | 19 | 7 |
| Chapters flagged `major_conflict` | 25 | 26 |
| Chapters naming "Hàn Lập" (all 50 mention him) | 16 | 15 |

The pool holds 301 names: 180 from both models, 73 only from `qwen3.8:udiq4_hui` and 48 only from `qwen3.8:UD_Q3`. The English counts are "Chu Nho" and "Hoa Lam", which are unaccented names rather than translations. All 50 chapters had the same text in both runs.

Names found in the text, by category:

| Category | `qwen3.8:udiq4_hui` | `qwen3.8:UD_Q3` |
|---|---|---|
| `character` | **84** | 63 |
| `weapon_treasure` | **83** | 76 |
| `technique` | 37 | **44** |
| `puppet_beast` | 27 | 28 |
| `dungeon` | **23** | 17 |

The first column adds up to 254, not 253, because one name was filed under two categories in the same chapter. The longest-chapter VRAM and GPU share come from a one-off test whose `ollama ps` output was not saved.

### End-to-end check

`xn run` S0 to S3 on chapters 1-15 with `qwen3.8:udiq4_hui` selected in a temporary config: every stage finished with 0 failures, with 15 S1 calls and 5 real S2 adjudications, none retried. S1 took 20.4 s/chapter. The run produced 35 entities, 40 aliases and 62 ledger events, against 27, 30 and 42 for `qwen3.8:UD_Q3`.

The list has no English or combined names, and "Hàn Lập" is a single entity. It does file two names under two categories: "Thất Huyền môn" and "Thái Hà sơn" each appear as `character` and as `dungeon`. S2 matches within one category, so each becomes two entities. `qwen3.8:UD_Q3` had one such error in the same chapters ("Thất Tuyệt đường" as `character`) but no name split this way.

### Trade-offs

- **Time:** 2,673 chapters at 27.2 s is about 20.2 h, just over the blueprint's 20 h target, against 11.7 h. The raw folder holds 2,468 chapters, which is about 18.6 h against 10.8 h. Every S1 rerun, such as after a prompt change, costs the same again.
- **Memory:** at its highest reading about 0.6 GB stays free, against 1.5 GB, and 8% of the model already runs on the CPU. Other GPU use during a long run could slow it further. The reranker will not fit alongside it, so the model must unload first at query time.
- **Coverage:** it finds 33% more characters than `qwen3.8:UD_Q3`, which was that model's main weakness.
- **Unchecked:** no gold set exists, so 0.84 means more names than the other model, not 84% of the true names. It was tested on 50 chapters from the first 1,121 only.

Choose `qwen3.8:UD_Q3` instead if you expect to rerun S1 often, or need the PC free during ingest.

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

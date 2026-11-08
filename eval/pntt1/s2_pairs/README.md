# S2 adjudication pairs (G-31.1)

`pairs.jsonl`: every pair the S2 resolver sent to the adjudicator while rebuilding S2 on chapters 1-200 of pntt1 (S1 from the G-29 run, `qwen3.5_12k:q8_mtp` with adjudicate prompt v1, 2026-11-08). One line per pair: the mention (name, category, chapter, context) and the candidate entity (aliases, up to 3 contexts) exactly as the adjudicator saw them.

- `label`: `same`, `different` or `unclear` (left out of scoring). Labelled by the lead (Claude) from the contexts, the chapter text and the names, before any scored replay. The recorded qwen3.5 decision was visible while labelling.
- `split`: `dev` for odd chapters, `held_out` for even chapters. Prompt examples and rule lists are tuned on `dev` only.
- Labelling policy, matching the adjudicate prompt: identity reveals, assumed names and hidden identities are `different` (handled by `identity_links`); a generic common noun with no proper part ("khẩu quyết", "chưởng môn", "phù lục") is `different` from a named entity even when it refers to it in that chapter, so it never becomes that entity's alias; a family, group, place or talisman is `different` from a person, member, event or technique of the same name.

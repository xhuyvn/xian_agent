"""G-31.1 replay: adjudicate the labelled pairs with one model / prompt / rules setup and score dev and held-out."""
import argparse, copy, json, sqlite3, time
from pathlib import Path
from xn.config import load_config
from xn.storage.database import init_schema
from xn.storage.repository import Repository
from xn.llm import local_model, s2_model_key
from xn.text import normalize_alias
from xn.pipeline.s2_resolve import Mention, Candidate, build_adjudicator, load_adjudicate_prompt, s2_rules_from_config

ap = argparse.ArgumentParser()
ap.add_argument("--configs", required=True); ap.add_argument("--prompt", default="v1")
ap.add_argument("--rules", action="store_true"); ap.add_argument("--votes", type=int, default=1)
ap.add_argument("--pairs", default="eval/pntt1/s2_pairs/pairs.jsonl"); ap.add_argument("--split", default="all")
ap.add_argument("--out", required=True); ap.add_argument("--reason-first", action="store_true"); ap.add_argument("--mention-only", action="store_true"); ap.add_argument("--prompts-dir", default="prompts")
a = ap.parse_args()
if a.reason_first:
    from pydantic import BaseModel, Field
    import xn.pipeline.s2_resolve as _s2
    class AdjudicationReasoned(BaseModel):
        """Reason first, then the decision."""
        reason: str = Field(max_length=400)
        same_entity: bool
    _s2.AdjudicationDecision = AdjudicationReasoned
cfg = copy.deepcopy(load_config("pntt1", configs_dir=Path(a.configs)))
cfg.setdefault("prompts", {})["s2_adjudicate"] = a.prompt
rules = s2_rules_from_config(cfg) if a.rules else None
conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; init_schema(conn); repo = Repository(conn)
key = s2_model_key(cfg)
adj = build_adjudicator(repo, novel_id="pntt1", config=cfg, model=local_model(cfg, key),
                        prompt=load_adjudicate_prompt(cfg, Path(a.prompts_dir)))
pairs = [json.loads(l) for l in open(a.pairs, encoding="utf-8")]
pairs = [p for p in pairs if p["label"] != "unclear" and (a.split == "all" or p["split"] == a.split)]
out, t0 = [], time.monotonic()
for p in pairs:
    m = Mention(name=p["name"], category=p["category"], chapter_id=p["chapter_id"], context=p["context"])
    c = Candidate(entity_id=p["cand_entity"], aliases=p["cand_aliases"], contexts=p["cand_contexts"])
    ruled = rules is not None and (rules.is_generic(normalize_alias(p["name"])) or (not a.mention_only and all(rules.is_generic(x) for x in p["cand_aliases"])))
    calls, t = 0, time.monotonic()
    if ruled:
        d = False
    else:
        d = adj(m, c); calls = 1
        for _ in range(a.votes - 1):
            if not d: break
            d = adj(m, c); calls += 1
    out.append({"id": p["id"], "split": p["split"], "label": p["label"], "same": bool(d), "ruled": ruled, "calls": calls, "s": round(time.monotonic() - t, 3)})
Path(a.out).write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in out) + "\n", encoding="utf-8")
print(f"mention_only={a.mention_only} reason_first={a.reason_first} model={cfg['models'][key]['serving_name']} prompt={a.prompt} rules={a.rules} votes={a.votes} wall={time.monotonic()-t0:.0f}s")
for sp in ("dev", "held_out"):
    rs = [o for o in out if o["split"] == sp]
    if not rs: continue
    fm = [o["id"] for o in rs if o["same"] and o["label"] == "different"]; mm = [o["id"] for o in rs if not o["same"] and o["label"] == "same"]
    calls = sum(o["calls"] for o in rs); lat = sum(o["s"] for o in rs if o["calls"])
    print(f"  {sp}: n={len(rs)} acc={1-(len(fm)+len(mm))/len(rs):.3f} false_merges={len(fm)} missed={len(mm)} calls={calls} s/call={lat/max(calls,1):.2f}")
    print(f"     fm={fm}\n     mm={mm}")

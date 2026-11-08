"""G-31.1 R-2: replay pairs once and record P(same_entity = true) from token logprobs."""
import argparse, copy, json, math, sqlite3, time
from pathlib import Path
from xn.config import load_config
from xn.storage.database import init_schema
from xn.storage.repository import Repository
from xn.llm import local_model, s2_model_key
import xn.pipeline.s2_resolve as s2
ap = argparse.ArgumentParser()
ap.add_argument("--configs", required=True); ap.add_argument("--pairs", nargs="+", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--prompt", default="v1"); ap.add_argument("--prompts-dir", default="prompts")
a = ap.parse_args()
cfg = copy.deepcopy(load_config("pntt1", configs_dir=Path(a.configs))); cfg.setdefault("prompts", {})["s2_adjudicate"] = a.prompt
real_settings = s2.extractor_settings
s2.extractor_settings = lambda c, k="local_extractor": {**real_settings(c, k), "openai_logprobs": True, "openai_top_logprobs": 5}
last = {}
real_run = s2.run_logged
def run_logged(agent, prompt, **kw):
    r = real_run(agent, prompt, **kw); last["r"] = r; return r
s2.run_logged = run_logged
def p_true(result):
    for msg in reversed(result.all_messages()):
        for part in [msg]:
            lps = (getattr(part, "provider_details", None) or {}).get("logprobs")
            if not lps: continue
            text = ""
            for i, t in enumerate(lps):
                text += t["token"]
                if "".join(text.split()).endswith('"same_entity":') and i + 1 < len(lps):
                    tops = lps[i + 1].get("top_logprobs") or [lps[i + 1]]
                    pt = sum(math.exp(x["logprob"]) for x in tops if x["token"].strip().startswith("true"))
                    pf = sum(math.exp(x["logprob"]) for x in tops if x["token"].strip().startswith("false"))
                    return pt / (pt + pf) if pt + pf else None
    return None
conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; init_schema(conn); repo = Repository(conn)
key = s2.s2_model_key(cfg)
adj = s2.build_adjudicator(repo, novel_id="pntt1", config=cfg, model=local_model(cfg, key), prompt=s2.load_adjudicate_prompt(cfg, Path(a.prompts_dir)))
out = []
for f in a.pairs:
    for l in open(f, encoding="utf-8"):
        p = json.loads(l)
        if p["label"] == "unclear": continue
        m = s2.Mention(name=p["name"], category=p["category"], chapter_id=p["chapter_id"], context=p["context"])
        c = s2.Candidate(entity_id=p["cand_entity"], aliases=p["cand_aliases"], contexts=p["cand_contexts"])
        t = time.monotonic(); d = adj(m, c)
        out.append({"id": p["id"], "split": p["split"], "kind": p.get("kind", "pair"), "label": p["label"], "same": bool(d), "p": p_true(last["r"]), "s": round(time.monotonic() - t, 3)})
Path(a.out).write_text("\n".join(json.dumps(o) for o in out) + "\n", encoding="utf-8")
print(len(out), sum(o["p"] is None for o in out), "no-prob")

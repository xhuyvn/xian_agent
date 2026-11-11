"""G-31.1: rebuild S2 on chapters 1-200 of a pntt1 copy with qwen3.5 (prompt v1) and record every adjudication pair."""
import json, sqlite3, sys, time
from pathlib import Path
from xn.config import load_config
from xn.storage.database import connect
from xn.storage.repository import Repository
from xn.pipeline import s2_resolve

D = Path("E:/Workspace/projects/xian_agent/data")
OUT = D / "bench/g311"
src = sqlite3.connect(f"file:{D}/xn.db?mode=ro", uri=True)
dst = sqlite3.connect(OUT / "pairs.db"); src.backup(dst); dst.close(); src.close()

pairs = []
real_make = s2_resolve.make_adjudicator
def make(agent, **kw):
    inner = real_make(agent, **kw)
    def adjudicate(m, c):
        t = time.monotonic(); d = inner(m, c)
        pairs.append({"chapter_id": m.chapter_id, "name": m.name, "category": m.category, "context": m.context,
                      "cand_entity": c.entity_id, "cand_aliases": c.aliases, "cand_contexts": c.contexts,
                      "decision_q35_v1": bool(d), "latency_s": round(time.monotonic() - t, 3)})
        return d
    return adjudicate
s2_resolve.make_adjudicator = make

conn = connect(OUT / "pairs.db"); repo = Repository(conn)
cfg = load_config("pntt1", configs_dir=D / "bench/g31-q35/configs")
res = s2_resolve.run_s2(repo, novel_id="pntt1", config=cfg, chapter_to=200, prompts_dir=Path("prompts"), rebuild=True)
print(res, len(pairs))
with open(OUT / "pairs-raw.jsonl", "w", encoding="utf-8") as f:
    for i, p in enumerate(pairs, 1):
        f.write(json.dumps({"id": f"p{i:03d}", **p}, ensure_ascii=False) + "\n")

"""G-31.1 R-2: S2 rebuild 1-400 of a pntt1 copy with the R-1 setup; record every adjudication from chapter 201 on."""
import json, sqlite3, time
from pathlib import Path
from xn.config import load_config
from xn.storage.database import connect
from xn.storage.repository import Repository
from xn.pipeline import s2_resolve
D = Path("E:/Workspace/projects/xian_agent/data"); OUT = D / "bench/g311r2"; OUT.mkdir(exist_ok=True)
src = sqlite3.connect(f"file:{D}/xn.db?mode=ro", uri=True); dst = sqlite3.connect(OUT / "pairs400.db"); src.backup(dst); dst.close()
cfg = load_config("pntt1", configs_dir=D / "bench/g311/configs")
pairs = []
real = s2_resolve.make_adjudicator
def make(agent, **kw):
    inner = real(agent, **kw)
    def adjudicate(m, c):
        d = inner(m, c)
        if m.chapter_id >= 201:
            pairs.append({"chapter_id": m.chapter_id, "name": m.name, "category": m.category, "context": m.context,
                          "cand_entity": c.entity_id, "cand_aliases": c.aliases, "cand_contexts": c.contexts, "decision": bool(d)})
        return d
    return adjudicate
s2_resolve.make_adjudicator = make
repo = Repository(connect(OUT / "pairs400.db"))
t = time.monotonic()
print(s2_resolve.run_s2(repo, novel_id="pntt1", config=cfg, chapter_to=400, prompts_dir=Path("prompts"), rebuild=True), len(pairs), round(time.monotonic() - t))
with open(OUT / "raw-with-decisions.jsonl", "w", encoding="utf-8") as f:
    for i, p in enumerate(pairs, 1): f.write(json.dumps({"id": f"h{i:03d}", **p}, ensure_ascii=False) + "\n")

import json, sqlite3
from pathlib import Path
from xn.config import load_config
from xn.storage.database import connect
from xn.storage.repository import Repository
from xn.pipeline import s2_resolve
D = Path("E:/Workspace/projects/xian_agent/data"); OUT = D / "bench/g311"
src = sqlite3.connect(f"file:{D}/xn.db?mode=ro", uri=True); dst = sqlite3.connect(OUT / "titles.db"); src.backup(dst); dst.close()
cfg = load_config("pntt1", configs_dir=OUT / "configs"); rules = s2_rules_from = s2_resolve.s2_rules_from_config(cfg)
pairs = []
def make(agent, **kw):
    def adjudicate(m, c):
        if rules.title_surname(s2_resolve.normalize_alias(m.name)) and m.category == "character":
            pairs.append({"chapter_id": m.chapter_id, "name": m.name, "category": m.category, "context": m.context,
                          "cand_entity": c.entity_id, "cand_aliases": c.aliases, "cand_contexts": c.contexts})
        return False
    return adjudicate
s2_resolve.make_adjudicator = make
s2_resolve.local_model = lambda *a, **k: None
repo = Repository(connect(OUT / "titles.db"))
print(s2_resolve.run_s2(repo, novel_id="pntt1", config=cfg, chapter_to=200, prompts_dir=Path("prompts"), rebuild=True), len(pairs))
with open(OUT / "titles-raw.jsonl", "w", encoding="utf-8") as f:
    for i, p in enumerate(pairs, 1): f.write(json.dumps({"id": f"t{i:03d}", **p}, ensure_ascii=False) + "\n")
for i, p in enumerate(pairs, 1):
    print(f"t{i:03d} ch{p['chapter_id']} {p['name']} => {' / '.join(p['cand_aliases'][:3])}")

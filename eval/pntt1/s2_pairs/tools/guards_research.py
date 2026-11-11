"""Candidate deterministic guards for S2 (G-31.1 R-2 research)."""
from xn.text import normalize_alias, strip_diacritics
CLASSES = {
    "place": ["đài", "sơn", "phong", "lĩnh", "thành", "trấn", "châu", "quốc", "đảo", "hải", "động phủ", "sơn mạch", "lâu", "tửu lâu", "các", "viện", "điện", "khu", "thảo nguyên", "sơn trang", "phủ"],
    "event": ["đại hội", "thí luyện", "hội nghị", "luận võ"],
    "org": ["môn", "phái", "tông", "bang", "giáo", "gia tộc", "tộc", "minh"],
    "technique": ["thuật", "công", "quyết", "kinh", "đại pháp", "kiếm pháp", "pháp quyết", "bí thuật", "thần thông", "trận", "đại trận"],
    "pill": ["đan", "hoàn", "tán", "linh dịch"],
}
def type_class(norm):
    toks = norm.split()
    if "phù" in toks or "phù lục" in norm:
        return "talisman"
    for n in (2, 1):
        tail = " ".join(toks[-n:])
        for cls, words in CLASSES.items():
            if tail in words:
                return cls
    return None
def edit(a, b):
    a, b = strip_diacritics(a), strip_diacritics(b)
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (ca != cb))
    return d[-1]
def g1(name, aliases):
    """Type clash: the mention's class differs from every candidate alias class (all known)."""
    c = type_class(normalize_alias(name))
    if c is None: return False
    cs = {type_class(a) for a in aliases}
    return None not in cs and c not in cs
def g2(name, aliases):
    """Distinct name part: for every alias with the same length and last word, a differing word is not a typo."""
    t = normalize_alias(name).split()
    if len(t) < 2 or type_class(" ".join(t)) is None: return False
    same_shape = [a.split() for a in aliases if len(a.split()) == len(t) and a.split()[-1] == t[-1]]
    if not same_shape or len(same_shape) < len(aliases): return False
    def distinct(u):
        diff = [(x, y) for x, y in zip(t, u) if x != y]
        return bool(diff) and any(edit(x, y) >= 2 for x, y in diff)
    return all(distinct(u) for u in same_shape)

RANKS = [
    {"môn chủ", "đại môn chủ", "bang chủ", "đại bang chủ", "chưởng môn", "đại chưởng môn", "giáo chủ", "tông chủ"},
    {"phó môn chủ", "phó bang chủ"},
    {"trưởng lão", "đại trưởng lão"},
    {"đạo trưởng", "đạo sĩ", "đạo nhân"},
    {"sư huynh", "huynh", "đại sư huynh"},
    {"sư đệ"}, {"sư tỷ"}, {"sư muội"}, {"sư thúc"}, {"sư bá"}, {"sư thúc tổ"}, {"sư tổ"},
    {"đường chủ", "đại đường chủ"}, {"hộ pháp"}, {"chấp sự"}, {"chưởng quỹ"},
]
def rank(title):
    for i, g in enumerate(RANKS):
        if title in g: return i
    return None
def g3(name, aliases):
    """Same surname, both '<surname> <title>', titles of different known ranks."""
    t = normalize_alias(name).split()
    if len(t) < 2: return False
    r = rank(" ".join(t[1:]))
    if r is None: return False
    for a in aliases:
        u = a.split()
        if len(u) < 2 or u[0] != t[0]: return False
        ra = rank(" ".join(u[1:]))
        if ra is None or ra == r: return False
    return True
PLACE_OR_ORG = set(CLASSES["place"]) | set(CLASSES["org"])
def g4(name, aliases):
    """The mention is a candidate place/org name followed by more words ending in a place word: a part of it."""
    n = normalize_alias(name)
    if type_class(n) != "place": return False
    for a in aliases:
        if not (n.startswith(a + " ") and type_class(a) in ("place", "org")): return False
    return bool(aliases)

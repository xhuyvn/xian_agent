"""Unit tests for S4 chunking and alias tagging."""

from xn.pipeline.s4_embed import AliasIndex, split_chunks
from xn.text import estimate_tokens

TARGET = 450
OVERLAP = 60


def para(word, tokens):
    """A paragraph of roughly the given token estimate."""
    return " ".join([word] * (int(tokens * 3.3) // (len(word) + 1)))


def split(text, target=TARGET, overlap=OVERLAP):
    return split_chunks(text, target=target, overlap=overlap)


def test_short_text_is_one_chunk():
    assert split("Một đoạn.\n\nHai đoạn.") == ["Một đoạn.\n\nHai đoạn."]


def test_blank_text_gives_no_chunks():
    assert split("") == []
    assert split(" \n\n \n") == []


def test_chunks_stay_within_target_and_keep_every_paragraph():
    paragraphs = [para(f"từ{i}", 150) for i in range(12)]
    chunks = split("\n\n".join(paragraphs))
    assert len(chunks) > 1
    assert all(estimate_tokens(c) <= TARGET for c in chunks)
    joined = "\n\n".join(chunks)
    assert all(p in joined for p in paragraphs)


def test_each_chunk_opens_with_the_tail_of_the_last():
    chunks = split("\n\n".join(para(f"từ{i}", 200) for i in range(4)))
    assert len(chunks) > 1
    for previous, chunk in zip(chunks, chunks[1:]):
        tail = chunk.split("\n\n")[0]
        assert previous.endswith(tail)
        assert 0 < estimate_tokens(tail) <= OVERLAP


def test_overlap_is_dropped_when_it_would_push_a_chunk_over_target():
    chunks = split(para("một", TARGET - 5) + "\n\n" + para("hai", TARGET - 5))
    assert len(chunks) == 2
    assert all(estimate_tokens(c) <= TARGET for c in chunks)
    assert "một" not in chunks[1]


def test_long_paragraph_splits_at_sentences():
    sentence = para("câu", 100) + "."
    chunks = split(" ".join([sentence] * 8))
    assert len(chunks) > 1
    assert all(estimate_tokens(c) <= TARGET for c in chunks)


def test_long_sentence_and_long_word_still_fit():
    for text in (para("không", 1000), "x" * 5000):
        chunks = split(text)
        assert len(chunks) > 1
        assert all(estimate_tokens(c) <= TARGET for c in chunks)


def test_a_chunk_never_holds_only_overlap():
    chunks = split(para("một", 300) + "\n\n" + para("hai", 300))
    assert len(chunks) == 2
    assert "hai" in chunks[1]


def test_chunking_is_repeatable():
    text = "\n\n".join(para(f"từ{i}", 170) for i in range(9))
    assert split(text) == split(text)


def alias(name, entity_id, first_chapter=1):
    return {"alias_norm": name, "entity_id": entity_id, "first_chapter": first_chapter}


def test_tags_multi_word_aliases_on_whole_words():
    index = AliasIndex([alias("hàn lập", 1), alias("mặc đại phu", 2), alias("lập", 3)])
    assert index.entities_in("Hàn Lập gặp Mặc đại phu.", chapter_id=5) == [1, 2, 3]


def test_does_not_tag_inside_a_longer_word():
    index = AliasIndex([alias("thỏ", 1), alias("hàn lập", 2)])
    assert index.entities_in("Thỏa thuận với Hàn. Lập trường.", chapter_id=5) == [2]


def test_an_alias_is_invisible_before_its_first_chapter():
    index = AliasIndex([alias("vô danh", 1, first_chapter=10)])
    text = "Vô Danh xuất hiện."
    assert index.entities_in(text, chapter_id=9) == []
    assert index.entities_in(text, chapter_id=10) == [1]


def test_a_shared_alias_tags_every_entity():
    index = AliasIndex([alias("trưởng lão", 7), alias("trưởng lão", 4)])
    assert index.entities_in("Vị trưởng lão đến.", chapter_id=1) == [4, 7]


def test_a_shared_alias_respects_each_entitys_first_chapter():
    index = AliasIndex([alias("trưởng lão", 7, first_chapter=3), alias("trưởng lão", 4, first_chapter=8)])
    assert index.entities_in("Vị trưởng lão đến.", chapter_id=5) == [7]


def test_an_alias_of_only_punctuation_is_ignored():
    index = AliasIndex([alias("...", 1)])
    assert index.entities_in("...", chapter_id=1) == []


def test_text_with_no_alias_gives_no_entities():
    assert AliasIndex([alias("hàn lập", 1)]).entities_in("Không có ai.", chapter_id=1) == []
    assert AliasIndex([]).entities_in("Hàn Lập", chapter_id=1) == []

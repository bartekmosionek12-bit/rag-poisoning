import pytest

from ragpoison.text import chunk_text, read_lines


def test_short_text_is_one_chunk_with_whitespace_collapsed():
    assert chunk_text("a  b\n\tc", size=10, overlap=2) == ["a b c"]


def test_chunks_overlap_and_cover_the_text():
    text = "".join(chr(97 + i % 26) for i in range(1200))
    chunks = chunk_text(text, size=500, overlap=50)
    assert [len(c) for c in chunks] == [500, 500, 300]
    assert chunks[0][-50:] == chunks[1][:50]
    assert chunks[-1].endswith(text[-10:])


def test_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        chunk_text("x" * 100, size=10, overlap=10)


def test_read_lines_skips_blanks_and_comments(tmp_path):
    p = tmp_path / "q.txt"
    p.write_text("# header\n\nfirst query\n  second query  \n")
    assert read_lines(p) == ["first query", "second query"]


def test_bundled_query_sets_are_disjoint():
    from ragpoison.config import Settings
    s = Settings()
    attacker, canary = set(read_lines(s.attacker_queries)), set(read_lines(s.canary_queries))
    assert len(attacker) == 40 and len(canary) == 14
    assert not attacker & canary

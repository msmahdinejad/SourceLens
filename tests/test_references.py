from paperlens.tools.references import ReferenceAnalyzer


def test_reference_year_parser_counts_one_publication_year_per_entry() -> None:
    text = """
- [1] A. Author. First paper. Conference, 2018.
- [2] B. Author. Preprint arXiv:1901.00001, 2019.
- [3] C. Author. Older work, 1998. Revised in 2001.
"""

    counts = ReferenceAnalyzer.count_reference_years(text)

    assert counts == {2001: 1, 2018: 1, 2019: 1}
    assert counts[1998] == 0

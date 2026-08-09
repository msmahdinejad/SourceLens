# Pinned Wikidata retrieval fixture

This fixture contains structured data from pinned Wikidata entity revisions. Wikidata
main-namespace structured data is dedicated under CC0-1.0. `provenance.json` records the
revision URL and SHA-256 of every source response. SourceLens renders selected facts into
short bilingual pages; it does not copy Wikipedia narrative or media.

Normal tests are offline. Reproduce the checked-in fixture from its pinned revisions with:

```powershell
uv run python scripts/build_retrieval_fixture.py
```

Use `--refresh` only when deliberately versioning a new source snapshot and reviewing the
resulting questions, provenance, and measured thresholds.

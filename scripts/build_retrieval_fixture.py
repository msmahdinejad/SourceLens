"""Build the pinned CC0 Wikidata retrieval fixture.

Network access is explicit. Normal tests consume the checked-in derived fixture only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

ENTITY_IDS = (
    "Q42",
    "Q937",
    "Q7186",
    "Q7259",
    "Q76",
    "Q868",
    "Q5",
    "Q30",
    "Q183",
    "Q39",
    "Q36",
    "Q142",
    "Q668",
)
WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
LICENSE_URL = "https://www.wikidata.org/wiki/Wikidata:Licensing"
PROPERTY_NAMES = {
    "P17": "country",
    "P19": "place of birth",
    "P20": "place of death",
    "P27": "country of citizenship",
    "P31": "instance of",
    "P36": "capital",
    "P37": "official language",
    "P39": "position held",
    "P47": "shares border with",
    "P106": "occupation",
    "P131": "located in administrative entity",
    "P136": "genre",
    "P155": "follows",
    "P156": "followed by",
    "P361": "part of",
    "P463": "member of",
    "P495": "country of origin",
    "P569": "date of birth",
    "P570": "date of death",
    "P571": "inception",
    "P576": "dissolved or abolished",
    "P577": "publication date",
    "P580": "start time",
    "P582": "end time",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("fixtures/retrieval_wikidata"),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch current revisions. Without this flag, fetch pinned revisions.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pinned = _load_pins(args.output) if not args.refresh else {}
    entities, sources = _fetch_entities(pinned)
    documents = _documents(entities)
    questions = _questions(entities)
    _write_json(
        args.output / "documents.json",
        {
            "version": 1,
            "license": "CC0-1.0",
            "authored_for_sourcelens": False,
            "source_kind": "wikidata",
            "source_count": len(sources),
            "documents": documents,
        },
    )
    _write_json(
        args.output / "questions.json",
        {
            "benchmark_id": "sourcelens-wikidata-retrieval-v1",
            "version": 1,
            "cases": questions,
            "thresholds": {
                "recall_at_6": 0.85,
                "citation_precision": 0.75,
                "abstention_accuracy": 0.70,
                "p95_latency_ms": 250.0,
            },
        },
    )
    _write_json(
        args.output / "provenance.json",
        {
            "license": "CC0-1.0",
            "license_url": LICENSE_URL,
            "derivation": (
                "Selected labels, aliases, statements, dates, and identifiers "
                "rendered as pages."
            ),
            "sources": sources,
        },
    )
    (args.output / "README.md").write_text(
        """# Pinned Wikidata retrieval fixture

This fixture contains structured data from pinned Wikidata entity revisions. Wikidata
main-namespace structured data is dedicated under CC0-1.0. `provenance.json` records the
revision URL and SHA-256 of every source response. SourceLens renders selected facts into
short bilingual pages; it does not copy Wikipedia narrative or media.

Normal tests are offline. A maintainer may reproduce the checked-in fixture from its pinned
revisions with:

```powershell
uv run python scripts/build_retrieval_fixture.py
```

Use `--refresh` only when deliberately versioning a new source snapshot and reviewing the
resulting questions, provenance, and measured thresholds.
""",
        encoding="utf-8",
    )
    print(f"Wrote {len(documents)} documents and {len(questions)} cases to {args.output}")


def _load_pins(path: Path) -> dict[str, int]:
    provenance_path = path / "provenance.json"
    if not provenance_path.is_file():
        raise SystemExit("No pinned provenance exists; use --refresh for the first snapshot")
    payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    return {item["entity_id"]: int(item["revision_id"]) for item in payload["sources"]}


def _fetch_entities(pinned: dict[str, int]) -> tuple[dict[str, dict[str, Any]], list[dict]]:
    entities: dict[str, dict[str, Any]] = {}
    sources: list[dict] = []
    headers = {
        "User-Agent": (
            "SourceLens/2.0 retrieval-fixture "
            "(https://github.com/msmahdinejad/SourceLens)"
        ),
        "Accept": "application/json",
    }
    with httpx.Client(headers=headers, follow_redirects=True, timeout=30.0) as client:
        for entity_id in ENTITY_IDS:
            revision = pinned.get(entity_id)
            url = WIKIDATA_ENTITY_URL.format(entity_id=entity_id)
            if revision is not None:
                url = f"{url}?revision={revision}"
            response = client.get(url)
            response.raise_for_status()
            raw = response.content
            entity = response.json()["entities"][entity_id]
            revision = int(entity["lastrevid"])
            entities[entity_id] = entity
            sources.append(
                {
                    "entity_id": entity_id,
                    "revision_id": revision,
                    "modified": entity.get("modified"),
                    "url": f"{WIKIDATA_ENTITY_URL.format(entity_id=entity_id)}?revision={revision}",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    return entities, sources


def _documents(entities: dict[str, dict[str, Any]]) -> list[dict]:
    labels = {entity_id: _label(entity, "en") for entity_id, entity in entities.items()}
    documents = []
    for index, (entity_id, entity) in enumerate(entities.items()):
        en_label = labels[entity_id]
        fa_label = _label(entity, "fa")
        en_description = _description(entity, "en")
        fa_description = _description(entity, "fa")
        aliases_en = _aliases(entity, "en")
        aliases_fa = _aliases(entity, "fa")
        item_statements, scalar_statements = _statements(entity, labels)
        pages = [
            {
                "page": 1,
                "text": (
                    f"Wikidata identity. Item identifier: {entity_id}. English label: {en_label}. "
                    f"Persian label: {fa_label}. English description: {en_description}. "
                    f"Persian description: {fa_description}."
                ),
            },
            {
                "page": 2,
                "text": (
                    f"Names and aliases for {entity_id} ({en_label}; {fa_label}). "
                    f"English aliases: {', '.join(aliases_en) or 'none recorded'}. "
                    f"Persian aliases: {', '.join(aliases_fa) or 'none recorded'}."
                ),
            },
            {
                "page": 3,
                "text": (
                    f"Entity relations for {entity_id} ({en_label}). "
                    + (" ".join(item_statements) or "No selected item relations recorded.")
                ),
            },
            {
                "page": 4,
                "text": (
                    f"Dates and scalar claims for {entity_id} ({en_label}). "
                    + (" ".join(scalar_statements) or "No selected scalar claims recorded.")
                    + _lifespan_equation(entity)
                    + f" Source revision {entity['lastrevid']}; modified "
                    f"{entity.get('modified', 'unknown')}."
                ),
            },
        ]
        documents.append(
            {
                "document_id": entity_id,
                "title": f"Wikidata {entity_id}: {en_label}",
                "language": "fa" if index % 2 else "en",
                "pages": pages,
            }
        )
    return documents


def _questions(entities: dict[str, dict[str, Any]]) -> list[dict]:
    labels = {entity_id: _label(entity, "en") for entity_id, entity in entities.items()}
    cases: list[dict] = []

    def add(
        category: str,
        query: str,
        document_ids: list[str],
        pages: list[tuple[str, int | list[int]]],
        *,
        language: str = "en",
    ) -> None:
        case_number = len(cases) + 1
        cases.append(
            {
                "case_id": f"wd-{case_number:02d}",
                "query": query,
                "language": language,
                "category": category,
                "scope": "in_scope" if document_ids else "out_of_scope",
                "relevant_document_ids": document_ids,
                "expected_citations": [
                    {
                        "document_id": document_id,
                        "pages": page if isinstance(page, list) else [page],
                    }
                    for document_id, page in pages
                ],
                "split": "calibration" if case_number % 4 == 1 else "test",
            }
        )

    for index, entity_id in enumerate(ENTITY_IDS[:8]):
        label = labels[entity_id]
        if index % 2:
            add(
                "exact_identifier",
                f"شناسه دقیق ویکی‌داده برای { _label(entities[entity_id], 'fa') } چیست؟",
                [entity_id],
                [(entity_id, [1, 2])],
                language="fa",
            )
        else:
            add(
                "exact_identifier",
                f"What is the exact Wikidata item identifier for {label}?",
                [entity_id],
                [(entity_id, [1, 2])],
            )

    alias_candidates = []
    for entity_id in ENTITY_IDS:
        for language in ("en", "fa"):
            aliases = _aliases(entities[entity_id], language)
            if aliases:
                alias_candidates.append((entity_id, language, aliases[0]))
                break
    for entity_id, language, alias in alias_candidates[:6]:
        query = (
            f"نام اصلی موجودیتی که نام مستعار «{alias}» دارد چیست؟"
            if language == "fa"
            else f"Which entity has the alias '{alias}'? Give its canonical label."
        )
        add("entity_alias", query, [entity_id], [(entity_id, 2)], language=language)

    temporal_candidates = [
        (entity_id, prop, value)
        for entity_id in ENTITY_IDS
        for prop, value in _time_values(entities[entity_id])
    ]
    for index, (entity_id, prop, _value) in enumerate(temporal_candidates[:6]):
        label = labels[entity_id]
        query = (
            f"در صفحه تاریخ‌ها و ادعاهای عددی، برای {label} مقدار زمانی ویژگی {prop} چیست؟"
            if index % 2
            else (
                f"What temporal value does {label} record for {prop} "
                f"({PROPERTY_NAMES.get(prop, 'date')})?"
            )
        )
        add("temporal", query, [entity_id], [(entity_id, 4)], language="fa" if index % 2 else "en")

    lifespan = [
        entity_id
        for entity_id in ENTITY_IDS
        if _first_time(entities[entity_id], "P569") and _first_time(entities[entity_id], "P570")
    ]
    for entity_id in lifespan[:3]:
        add(
            "equation",
            f"For {labels[entity_id]}, evaluate Δyears = year(P570) - year(P569) "
            "from the recorded dates.",
            [entity_id],
            [(entity_id, 4)],
        )

    for entity_id in ENTITY_IDS[:5]:
        add(
            "citation",
            "On the Wikidata identity page, cite where both the English and Persian "
            f"labels for {labels[entity_id]} are recorded.",
            [entity_id],
            [(entity_id, [1, 2])],
        )

    links = _selected_links(entities)
    link_cases = []
    for entity_id in ENTITY_IDS:
        for prop, target_id in links[entity_id]:
            if target_id in entities and target_id != entity_id:
                link_cases.append((entity_id, prop, target_id))
    for entity_id, prop, target_id in link_cases[:5]:
        add(
            "multi_hop",
            f"Using {prop}, connect {labels[entity_id]} to {labels[target_id]} and "
            "identify both Wikidata items.",
            [entity_id, target_id],
            [(entity_id, 3), (target_id, [1, 2])],
        )

    contradiction_candidates = []
    for entity_id in ENTITY_IDS:
        selected = links[entity_id]
        if len(selected) >= 2:
            contradiction_candidates.append((entity_id, selected[0], selected[1]))
    for entity_id, first, second in contradiction_candidates[:4]:
        _prop_a, target_a = first
        prop_b, target_b = second
        add(
            "contradiction",
            f"Check the claim that {labels[entity_id]} links only to "
            f"{labels[target_a]}; does the record also link it to "
            f"{labels[target_b]} via {prop_b}?",
            [entity_id],
            [(entity_id, 3)],
        )

    out_of_scope = [
        "What is the pressure reading from the fictional Aurora-9 submarine trial?",
        "Give the checksum for the unpublished Zephyr lattice experiment.",
        "در گزارش خیالی باران-۷ مقدار حسگر کوانتومی چند است؟",
        "Which reagent was used in the nonexistent Borealis protein assay?",
        "What was the 2099 revenue of the fictional company Amber Quasar?",
        "صفحه مربوط به مأموریت ساختگی آفتاب-۹۹ را ذکر کن.",
        "Who won the imaginary Northbridge theorem prize in 2088?",
        "State the orbital period of the invented moon Caligo Prime.",
    ]
    for index, query in enumerate(out_of_scope):
        add("abstention", query, [], [], language="fa" if index in {2, 5} else "en")

    if not 40 <= len(cases) <= 50:
        raise RuntimeError(f"Expected 40-50 benchmark cases, produced {len(cases)}")
    return cases


def _label(entity: dict[str, Any], language: str) -> str:
    label = entity.get("labels", {}).get(language)
    if label:
        return str(label["value"])
    fallback = entity.get("labels", {}).get("en")
    return str(fallback["value"]) if fallback else entity["id"]


def _description(entity: dict[str, Any], language: str) -> str:
    value = entity.get("descriptions", {}).get(language)
    return str(value["value"]) if value else "none recorded"


def _aliases(entity: dict[str, Any], language: str) -> list[str]:
    return [str(item["value"]) for item in entity.get("aliases", {}).get(language, [])[:8]]


def _selected_links(entities: dict[str, dict[str, Any]]) -> dict[str, list[tuple[str, str]]]:
    selected_ids = set(entities)
    result = {}
    for entity_id, entity in entities.items():
        links = []
        for prop, claims in entity.get("claims", {}).items():
            for claim in claims:
                value = _datavalue(claim)
                if isinstance(value, dict) and value.get("entity-type") == "item":
                    target_id = value.get("id")
                    if target_id in selected_ids:
                        links.append((prop, str(target_id)))
        result[entity_id] = sorted(set(links))
    return result


def _statements(
    entity: dict[str, Any], labels: dict[str, str]
) -> tuple[list[str], list[str]]:
    item_statements: list[str] = []
    scalar_statements: list[str] = []
    for prop in sorted(entity.get("claims", {})):
        name = PROPERTY_NAMES.get(prop, "property")
        for claim in entity["claims"][prop]:
            value = _datavalue(claim)
            if value is None:
                continue
            if isinstance(value, dict) and value.get("entity-type") == "item":
                target_id = str(value.get("id"))
                if target_id in labels:
                    item_statements.append(
                        f"{prop} ({name}) points to {target_id} ({labels[target_id]})."
                    )
            elif prop in PROPERTY_NAMES:
                scalar_statements.append(f"{prop} ({name}) = {_format_value(value)}.")
    return item_statements[:24], scalar_statements[:24]


def _datavalue(claim: dict[str, Any]) -> Any:
    snak = claim.get("mainsnak", {})
    return snak.get("datavalue", {}).get("value")


def _format_value(value: Any) -> str:
    if isinstance(value, dict):
        if "time" in value:
            return str(value["time"])
        if "amount" in value:
            unit = str(value.get("unit", "1")).rsplit("/", 1)[-1]
            return f"{value['amount']} {unit}"
        if "text" in value:
            return str(value["text"])
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _time_values(entity: dict[str, Any]) -> list[tuple[str, str]]:
    values = []
    for prop in ("P569", "P570", "P571", "P576", "P577", "P580", "P582"):
        for claim in entity.get("claims", {}).get(prop, []):
            value = _datavalue(claim)
            if isinstance(value, dict) and value.get("time"):
                values.append((prop, str(value["time"])))
    return values


def _first_time(entity: dict[str, Any], prop: str) -> str | None:
    values = _time_values(entity)
    return next((value for candidate, value in values if candidate == prop), None)


def _lifespan_equation(entity: dict[str, Any]) -> str:
    birth = _first_time(entity, "P569")
    death = _first_time(entity, "P570")
    if not birth or not death:
        return ""
    birth_year = int(birth[1:5])
    death_year = int(death[1:5])
    return (
        f" Derived equation: Δyears = year(P570) - year(P569) = "
        f"{death_year} - {birth_year} = {death_year - birth_year}."
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

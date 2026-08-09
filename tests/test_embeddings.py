from types import SimpleNamespace

import numpy as np

from paperlens.corpus.index import (
    ChromaEmbeddingIndex,
    GeminiEmbedder,
    OpenAICompatibleEmbedder,
)


class FakeModels:
    def __init__(self) -> None:
        self.calls = []

    def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        contents = kwargs["contents"]
        vectors = []
        for index, _ in enumerate(contents, start=1):
            vectors.append(SimpleNamespace(values=[float(index), 1.0, 0.0]))
        return SimpleNamespace(embeddings=vectors)


def test_gemini_embedding_uses_asymmetric_retrieval_prompts() -> None:
    models = FakeModels()
    client = SimpleNamespace(models=models)
    embedder = GeminiEmbedder(
        "gemini-embedding-2",
        "test-key",
        dimensions=3,
        client=client,
    )

    passages = embedder.embed_passages(
        ["Residual connections", "Compound scaling"],
        ["HomeCharge", "SolarHub"],
    )
    query = embedder.embed_query("How is depth scaled?")

    passage_texts = [content.parts[0].text for content in models.calls[0]["contents"]]
    query_text = models.calls[1]["contents"][0].parts[0].text
    assert passage_texts == [
        "title: HomeCharge | text: Residual connections",
        "title: SolarHub | text: Compound scaling",
    ]
    assert query_text == "task: search result | query: How is depth scaled?"
    assert passages.shape == (2, 3)
    assert query.shape == (3,)
    assert np.allclose(np.linalg.norm(passages, axis=1), 1.0)
    assert np.isclose(np.linalg.norm(query), 1.0)


def test_openai_compatible_embedding_provider_is_normalized_and_dimension_safe() -> None:
    class FakeEmbeddings:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            data = [
                SimpleNamespace(index=index, embedding=[float(index + 1), 1.0, 0.0])
                for index, _ in enumerate(kwargs["input"])
            ]
            return SimpleNamespace(data=data)

    embeddings = FakeEmbeddings()
    client = SimpleNamespace(embeddings=embeddings)
    embedder = OpenAICompatibleEmbedder(
        "custom-embedding-model",
        ["secret"],
        base_url="https://provider.example/v1",
        dimensions=3,
        client=client,
    )

    passages = embedder.embed_passages(["one", "two"], ["A", "B"])
    query = embedder.embed_query("question")

    assert embeddings.calls[0]["input"] == ["title: A | text: one", "title: B | text: two"]
    assert embeddings.calls[1]["input"] == ["question"]
    assert passages.shape == (2, 3)
    assert query.shape == (3,)
    assert np.allclose(np.linalg.norm(passages, axis=1), 1.0)


def test_balanced_retrieval_embeds_the_query_only_once() -> None:
    class CountingEmbedder:
        model_name = "counting"

        def __init__(self) -> None:
            self.calls = 0

        def embed_query(self, _text: str) -> np.ndarray:
            self.calls += 1
            return np.asarray([1.0, 0.0], dtype=np.float32)

    class FakeCollection:
        def count(self) -> int:
            return 4

        def query(self, **kwargs):
            where = kwargs["where"]
            paper_id = where["$and"][0]["paper_id"]
            metadata = {
                "paper_id": paper_id,
                "paper_title": paper_id,
                "section": "Experiments",
                "section_type": "experiments",
                "page_start": 1,
                "page_end": 1,
            }
            return {
                "ids": [[f"{paper_id}-1"]],
                "documents": [[f"Evidence for {paper_id}"]],
                "metadatas": [[metadata]],
                "distances": [[0.1]],
            }

    index = ChromaEmbeddingIndex.__new__(ChromaEmbeddingIndex)
    index.embedder = CountingEmbedder()
    index.collection = FakeCollection()

    results = index.search_balanced(
        "compare four papers",
        ("homecharge", "retailcharge", "smartcharge", "solarhub"),
        top_k=4,
        per_paper=1,
        section_types={"experiments"},
    )

    assert index.embedder.calls == 1
    assert {item.paper_id for item in results} == {
        "homecharge",
        "retailcharge",
        "smartcharge",
        "solarhub",
    }

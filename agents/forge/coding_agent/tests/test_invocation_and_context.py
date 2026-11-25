import pytest

from coding_agent.core.context import ForgeRAGContextProvider, MarkdownContextProvider, build_context_provider
from coding_agent.core.models import CodingRequest, InvocationContext, InvocationSource
from coding_agent.core.registry import get_role
from coding_agent.core.workspace import Workspace


class FakeRetriever:
    def __init__(self, hits=None, fail=False):
        self.hits = hits or []
        self.fail = fail
        self.calls = []

    def search(self, query, faction_ids, top_k=5, source_id=None):
        self.calls.append({"query": query, "factions": faction_ids, "top_k": top_k, "source_id": source_id})
        if self.fail:
            raise ConnectionError("qdrant down")
        return self.hits


# ------------------------------------------------------------- invocation rules
@pytest.mark.parametrize("source", [InvocationSource.API, InvocationSource.UI, InvocationSource.CLI])
def test_only_forge_may_attach_rag(source):
    with pytest.raises(ValueError, match="Only a Forge invocation"):
        InvocationContext(source=source, knowledge=FakeRetriever())
    assert not InvocationContext(source=source).rag_enabled


def test_forge_without_retriever_uses_markdown(tmp_path):
    invocation = InvocationContext(source=InvocationSource.FORGE)
    assert not invocation.rag_enabled
    assert isinstance(build_context_provider(Workspace(tmp_path), invocation), MarkdownContextProvider)


def test_forge_with_retriever_uses_rag(tmp_path):
    invocation = InvocationContext(source=InvocationSource.FORGE, knowledge=FakeRetriever(),
                                   knowledge_factions=("forge", "pentagram"), knowledge_source_id="src-1")
    provider = build_context_provider(Workspace(tmp_path), invocation)
    assert isinstance(provider, ForgeRAGContextProvider)
    assert provider.factions == ["forge", "pentagram"] and provider.source_id == "src-1"


def test_request_payload_validation():
    request = CodingRequest.from_payload({"task": "x", "workspace": "w", "constraints": ["a"]})
    assert request.constraints == ["a"] and request.mode == "apply"
    with pytest.raises(ValueError, match="unknown request fields"):
        CodingRequest.from_payload({"task": "x", "workspace": "w", "knowledge": "sneaky"})
    with pytest.raises(ValueError):
        CodingRequest.from_payload({"task": " ", "workspace": "w"})
    with pytest.raises(ValueError):
        CodingRequest.from_payload({"task": "x", "workspace": "w", "constraints": "not a list"})


# ------------------------------------------------------------ markdown context
def _workspace(tmp_path):
    (tmp_path / "README.md").write_text("# Billing service\nUses SQLite.", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "invoice.py").write_text("def total_invoice(items):\n    return sum(items)\n", encoding="utf-8")
    (tmp_path / "src" / "unrelated.py").write_text("def hello():\n    pass\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    memory = tmp_path / ".coding-agent" / "memory"
    memory.mkdir(parents=True)
    (memory / "CONVENTIONS.md").write_text("Use snake_case everywhere.", encoding="utf-8")
    return Workspace(tmp_path)


def test_markdown_context_includes_docs_memory_and_ranked_source(tmp_path):
    provider = MarkdownContextProvider(_workspace(tmp_path))
    provider.remember("Task: earlier run\n- Decided to keep SQLite")
    text = provider.gather("fix the invoice total", get_role("requirements_analyst")).render(20000)
    assert "Billing service" in text
    assert "snake_case" in text
    assert "Decided to keep SQLite" in text
    assert "Related source src/invoice.py" in text
    assert "unrelated.py" not in text.split("Workspace file map")[0]
    assert ".env" not in text


def test_markdown_context_shows_assigned_files_through_reader(tmp_path):
    provider = MarkdownContextProvider(_workspace(tmp_path))
    reader = lambda path: "PROPOSED CONTENT" if path == "src/invoice.py" else None  # noqa: E731
    text = provider.gather("x", get_role("python_developer"), focus_files=["src/invoice.py", "src/new.py"],
                           reader=reader).render(20000)
    assert "PROPOSED CONTENT" in text
    assert "src/new.py" in text and "does not exist yet" in text


def test_memory_is_compacted(tmp_path):
    provider = MarkdownContextProvider(Workspace(tmp_path))
    for i in range(30):
        provider.remember(f"Task: run {i}")
    memory = (tmp_path / ".coding-agent" / "memory" / "PROJECT_MEMORY.md").read_text(encoding="utf-8")
    assert "run 29" in memory and "run 0\n" not in memory
    assert memory.count("\n## ") == 15


def test_render_respects_budget(tmp_path):
    provider = MarkdownContextProvider(_workspace(tmp_path))
    text = provider.gather("invoice", get_role("requirements_analyst")).render(400)
    assert len(text) < 700


# ---------------------------------------------------------------- RAG context
def test_rag_context_adds_forge_knowledge(tmp_path):
    retriever = FakeRetriever([
        {"relative_path": "docs/billing.md", "text": "Invoices are rounded half-even.", "score": 0.91,
         "faction_id": "forge"},
        {"relative_path": "src/invoice.py", "text": "duplicate of focus file", "score": 0.5},
    ])
    invocation = InvocationContext(source=InvocationSource.FORGE, knowledge=retriever)
    provider = build_context_provider(_workspace(tmp_path), invocation)
    text = provider.gather("invoice rounding", get_role("python_developer"),
                           focus_files=["src/invoice.py"]).render(20000)
    assert "Invoices are rounded half-even." in text
    assert "Forge knowledge: docs/billing.md" in text
    assert "duplicate of focus file" not in text
    assert retriever.calls[0]["factions"] == ["forge"]
    assert "Python Developer" in retriever.calls[0]["query"]


def test_rag_failure_falls_back_to_lexical(tmp_path):
    invocation = InvocationContext(source=InvocationSource.FORGE, knowledge=FakeRetriever(fail=True))
    provider = build_context_provider(_workspace(tmp_path), invocation)
    text = provider.gather("invoice total", get_role("python_developer")).render(20000)
    assert "Forge retrieval was unavailable" in text
    assert "Related source src/invoice.py" in text

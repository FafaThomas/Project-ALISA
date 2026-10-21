from __future__ import annotations

import hashlib
from pathlib import Path

from codebase_interpreter_agent.interpretation import (
    interpret_codebase_question,
)


class FakeClient:
    def __init__(self, response="Grounded answer [E1].", error=None):
        self.response = response
        self.error = error
        self.calls = []

    def chat(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if self.error:
            raise self.error
        return self.response


def _make_map(root: Path, relative_path: str, source: str) -> dict:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "nodes": [{
            "path": relative_path.replace("\\", "/"),
            "language": "python",
            "sha256": digest,
            "symbols": [{
                "name": "unrelated_name",
                "qualified_name": "unrelated_name",
                "kind": "function",
                "line": 1,
                "end_line": len(source.splitlines()),
            }],
        }]
    }


def test_retrieves_source_by_content_even_when_symbol_name_is_unrelated(tmp_path):
    source = (
        "def unrelated_name():\n"
        "    # Retrieve source and check whether indexed source is stale.\n"
        "    source_hash = calculate_sha256()\n"
        "    if source_hash != indexed_hash:\n"
        "        return 'stale source'\n"
        "    return 'fresh source'\n"
    )
    codebase_map = _make_map(tmp_path, "src/retrieval.py", source)
    client = FakeClient()

    result = interpret_codebase_question(
        codebase_map,
        tmp_path,
        "How does this project retrieve source and detect stale indexed source?",
        client=client,
    )

    assert result["status"] == "answered"
    assert len(client.calls) == 1
    assert result["evidence"]
    assert result["evidence"][0]["path"] == "src/retrieval.py"
    assert "indexed_hash" in result["evidence"][0]["snippet"]
    assert "src/retrieval.py:" in client.calls[0][1]


def test_marks_source_stale_when_current_hash_differs(tmp_path):
    relative_path = "src/retrieval.py"
    codebase_map = _make_map(
        tmp_path,
        relative_path,
        "def retrieve_source():\n    return 'old source retrieval'\n",
    )
    (tmp_path / relative_path).write_text(
        "def retrieve_source():\n"
        "    # retrieve source and stale indexed content\n"
        "    return 'new source retrieval'\n",
        encoding="utf-8",
    )
    client = FakeClient()

    result = interpret_codebase_question(
        codebase_map,
        tmp_path,
        "How does source retrieval handle stale indexed content?",
        client=client,
    )

    assert result["status"] == "answered"
    assert result["evidence"][0]["stale"] is True
    assert "differs from the indexed snapshot" in result["evidence"][0]["warning"]
    assert result["metadata"]["stale_evidence_count"] >= 1


def test_returns_insufficient_evidence_when_no_source_matches(tmp_path):
    codebase_map = _make_map(
        tmp_path,
        "src/unrelated.py",
        "def calculate_total():\n    return 42\n",
    )
    client = FakeClient()

    result = interpret_codebase_question(
        codebase_map,
        tmp_path,
        "Explain quantum entanglement in distant galaxies",
        client=client,
    )

    assert result["status"] == "insufficient_evidence"
    assert client.calls == []


def test_reports_ollama_failure_without_crashing(tmp_path):
    codebase_map = _make_map(
        tmp_path,
        "src/retrieval.py",
        "def retrieve_source():\n"
        "    # retrieve source from indexed file\n"
        "    return 'source'\n",
    )
    client = FakeClient(error=RuntimeError("Ollama unavailable"))

    result = interpret_codebase_question(
        codebase_map,
        tmp_path,
        "How does retrieve source work?",
        client=client,
    )

    assert result["status"] == "llm_error"
    assert "Ollama unavailable" in result["answer"]
    assert result["evidence"]

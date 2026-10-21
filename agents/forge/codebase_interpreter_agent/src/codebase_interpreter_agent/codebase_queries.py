
from __future__ import annotations


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def find_symbol(codebase_map: dict, name: str) -> dict:
    """Find symbols by exact name or qualified name."""
    query = name.strip()

    matches = []

    for node in codebase_map.get("nodes", []):
        for symbol in node.get("symbols", []):
            if (
                symbol.get("name") == query
                or symbol.get("qualified_name") == query
            ):
                matches.append({
                    "name": symbol["name"],
                    "qualified_name": symbol["qualified_name"],
                    "kind": symbol["kind"],
                    "path": _normalize_path(symbol["path"]),
                    "line": symbol["line"],
                    "end_line": symbol["end_line"],
                })

    matches.sort(
        key=lambda item: (
            item["path"],
            item["line"],
            item["qualified_name"],
        )
    )

    return {
        "query": query,
        "match_count": len(matches),
        "matches": matches,
    }


def find_dependents(codebase_map: dict, target_path: str) -> dict:
    """Find import edges that target a particular local file."""
    target = _normalize_path(target_path)
    dependents = []

    for edge in codebase_map.get("edges", []):
        targets = [
            _normalize_path(path)
            for path in edge.get("target_paths", [])
        ]

        if target not in targets:
            continue

        dependents.append({
            "source_path": _normalize_path(edge["source_path"]),
            "line": edge["line"],
            "imported_module": edge["imported_module"],
            "imported_names": edge["imported_names"],
            "target_paths": targets,
            "status": edge["status"],
        })

    dependents.sort(
        key=lambda item: (
            item["source_path"],
            item["line"],
        )
    )

    return {
        "target_path": target,
        "dependent_count": len(dependents),
        "dependents": dependents,
    }


def answer_codebase_question(
    codebase_map: dict,
    question: str,
) -> dict:
    """Answer supported codebase questions using map evidence only."""
    normalized_question = question.strip().lower()

    if not normalized_question:
        return {
            "status": "unsupported",
            "answer": "Please provide a codebase question.",
            "evidence": [],
        }

    known_symbols = {}

    for node in codebase_map.get("nodes", []):
        for symbol in node.get("symbols", []):
            name = symbol.get("name", "")
            if name:
                known_symbols[name.lower()] = name

    # Match known symbol names explicitly mentioned in the question.
    mentioned = [
        original
        for lowered, original in known_symbols.items()
        if lowered in normalized_question
    ]

    if not mentioned:
        return {
            "status": "no_match",
            "answer": (
                "I couldn't find a known symbol name in the question. "
                "Try asking about an exact class or function name."
            ),
            "evidence": [],
        }

    # Prefer the longest name if one symbol name contains another.
    mentioned.sort(key=len, reverse=True)
    symbol_name = mentioned[0]
    symbol_result = find_symbol(codebase_map, symbol_name)
    matches = symbol_result["matches"]

    if not matches:
        return {
            "status": "no_match",
            "answer": f"No definition was found for {symbol_name}.",
            "evidence": [],
        }

    asks_definition = (
        "where" in normalized_question
        or "defined" in normalized_question
        or "definition" in normalized_question
        or "located" in normalized_question
    )

    asks_dependents = any(
        term in normalized_question
        for term in (
            "depend",
            "import",
            "use",
            "used by",
            "which files",
        )
    )

    if asks_definition and not asks_dependents:
        evidence = [
            {
                "type": "symbol_definition",
                **match,
            }
            for match in matches
        ]

        descriptions = [
            "{} '{}' is defined in {} at lines {}-{}.".format(
                match["kind"].replace("_", " "),
                match["qualified_name"],
                match["path"],
                match["line"],
                match["end_line"],
            )
            for match in matches
        ]

        return {
            "status": "answered",
            "answer": "\n".join(descriptions),
            "evidence": evidence,
        }

    
    if asks_dependents:
        evidence = []
        seen = set()

        for match in matches:
            dependent_result = find_dependents(
                codebase_map,
                match["path"],
            )

            for dependent in dependent_result["dependents"]:
                # Only count imports that explicitly name this symbol.
                # Importing the containing module alone is not enough.
                if symbol_name not in dependent["imported_names"]:
                    continue

                key = (
                    dependent["source_path"],
                    dependent["line"],
                    match["path"],
                )

                if key in seen:
                    continue

                seen.add(key)
                evidence.append({
                    "type": "symbol_import",
                    "symbol": match["qualified_name"],
                    "symbol_path": match["path"],
                    **dependent,
                })

        evidence.sort(
            key=lambda item: (
                item["source_path"],
                item["line"],
            )
        )

        if not evidence:
            answer = (
                f"No explicit local imports of {symbol_name} were found "
                "in the indexed dependency map. This does not rule out "
                "dynamic imports or runtime dependencies."
            )
        else:
            sources = sorted({
                item["source_path"] for item in evidence
            })
            answer = (
                f"{symbol_name} is explicitly imported in "
                f"{len(evidence)} import statement(s), across "
                f"{len(sources)} distinct file(s). This establishes "
                "import relationships, not proof of runtime usage."
            )

        return {
            "status": "answered",
            "answer": answer,
            "evidence": evidence,
        }

    
    return {
        "status": "unsupported",
        "answer": (
            "I found the symbol, but this question type isn't supported "
            "yet. Try asking where it is defined or which files import "
            "its module."
        ),
        "evidence": [
            {"type": "symbol_definition", **match}
            for match in matches
        ],
    }


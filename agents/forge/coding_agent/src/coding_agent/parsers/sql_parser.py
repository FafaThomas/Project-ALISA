"""SQL parser with DDL, column, CTE and table-reference extraction."""

from __future__ import annotations

import re

from coding_agent.parsers.base import ImportRecord, ParsedSource, ParseFailure, ReferenceRecord, Symbol
from coding_agent.parsers.treesitter_parser import TreeSitterParser


_IDENTIFIER = r'(?:"(?:""|[^"])+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)'
_QUALIFIED = rf'{_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER})*'


class SQLParser(TreeSitterParser):
    language = "sql"
    grammar_name = "sql"
    extensions = (".sql",)

    _OBJECT_PATTERNS = (
        ("table", re.compile(rf"\bCREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>{_QUALIFIED})", re.I)),
        ("view", re.compile(rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>{_QUALIFIED})", re.I)),
        ("index", re.compile(rf"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>{_QUALIFIED})", re.I)),
        ("procedure", re.compile(rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\s+(?P<name>{_QUALIFIED})", re.I)),
        ("function", re.compile(rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?P<name>{_QUALIFIED})", re.I)),
        ("trigger", re.compile(rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+(?P<name>{_QUALIFIED})", re.I)),
        ("schema", re.compile(rf"\bCREATE\s+SCHEMA\s+(?P<name>{_QUALIFIED})", re.I)),
        ("type", re.compile(rf"\bCREATE\s+TYPE\s+(?P<name>{_QUALIFIED})", re.I)),
    )

    def parse(self, relative_path: str, source: str) -> ParsedSource:
        # Tree-sitter remains the syntax authority; extraction below adds
        # stable DDL semantics independent of grammar-specific node naming.
        tree = self._get_parser().parse(source.encode("utf-8"))
        if tree is None:
            raise ParseFailure(
                f"Tree-sitter could not parse {relative_path!r} as SQL."
            )
        if tree.root_node.has_error:
            raise ParseFailure(
                f"Syntax errors detected while parsing {relative_path!r} as SQL.",
                self._collect_syntax_diagnostics(tree.root_node),
            )
        masked = self._mask_comments_and_literals(source)
        symbols: list[Symbol] = []
        references: list[ReferenceRecord] = []

        for kind, pattern in self._OBJECT_PATTERNS:
            for match in pattern.finditer(masked):
                name = self._clean_identifier(match.group("name"))
                line = source.count("\n", 0, match.start()) + 1
                object_end = match.end()
                if kind == "table":
                    opening = masked.find("(", match.end())
                    closing = self._matching_paren(masked, opening) if opening >= 0 else None
                    if closing is not None:
                        object_end = closing
                else:
                    terminator = masked.find(";", match.end())
                    if terminator >= 0:
                        object_end = terminator
                symbols.append(Symbol(
                    name=name.split(".")[-1],
                    qualified_name=name,
                    kind=kind,
                    relative_path=relative_path,
                    line=line,
                    end_line=source.count("\n", 0, object_end) + 1,
                ))

        # Extract columns and inline/table-level foreign keys from CREATE TABLE.
        table_pattern = re.compile(
            rf"\bCREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>{_QUALIFIED})\s*\(",
            re.I,
        )
        for match in table_pattern.finditer(masked):
            table_name = self._clean_identifier(match.group("name"))
            open_paren = masked.find("(", match.start())
            close_paren = self._matching_paren(masked, open_paren)
            if close_paren is None:
                continue
            body = source[open_paren + 1:close_paren]
            masked_body = masked[open_paren + 1:close_paren]
            body_offset = open_paren + 1
            for masked_definition, offset in self._split_top_level(masked_body):
                definition = body[offset:offset + len(masked_definition)]
                cleaned = definition.strip()
                cleaned_code = masked_definition.strip()
                if not cleaned_code:
                    continue
                line = source.count("\n", 0, body_offset + offset) + 1
                constraint = re.match(r"(?is)^(?:CONSTRAINT\s+(?P<cname>\S+)\s+)?(?P<kind>PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b", cleaned_code)
                if constraint:
                    constraint_kind = re.sub(r"\s+", "_", constraint.group("kind").lower())
                    constraint_name = constraint.group("cname") or f"{constraint_kind}_{line}"
                    symbols.append(Symbol(
                        name=self._clean_identifier(constraint_name),
                        qualified_name=f"{table_name}.{self._clean_identifier(constraint_name)}",
                        kind="constraint",
                        relative_path=relative_path,
                        line=line,
                        end_line=source.count("\n", 0, body_offset + offset + len(definition)) + 1,
                        signature=cleaned,
                    ))
                    if constraint.group("kind").upper().startswith("FOREIGN"):
                        ref = re.search(rf"(?is)REFERENCES\s+(?P<target>{_QUALIFIED})", cleaned_code)
                        if ref:
                            target = self._clean_identifier(ref.group("target"))
                            references.append(ReferenceRecord(target, "foreign_key", line, table_name))
                    continue
                column = re.match(rf"(?is)^(?P<column>{_IDENTIFIER})\s+(?P<type>[A-Za-z_][\w]*(?:\s*\([^)]*\))?)", cleaned_code)
                if not column:
                    continue
                column_name = self._clean_identifier(column.group("column"))
                data_type = re.sub(r"\s+", " ", column.group("type").strip())
                column_end_line = source.count("\n", 0, body_offset + offset + len(definition)) + 1
                symbols.append(Symbol(
                    name=column_name,
                    qualified_name=f"{table_name}.{column_name}",
                    kind="column",
                    relative_path=relative_path,
                    line=line,
                    end_line=column_end_line,
                    signature=data_type,
                ))
                inline_constraints = re.findall(r"(?i)\b(PRIMARY\s+KEY|NOT\s+NULL|UNIQUE|CHECK)\b", cleaned_code)
                if re.search(r"(?i)\bREFERENCES\b", cleaned_code):
                    inline_constraints.append("FOREIGN KEY")
                for constraint_kind in dict.fromkeys(inline_constraints):
                    normalized_kind = re.sub(r"\s+", "_", constraint_kind.lower())
                    constraint_name = f"{column_name}_{normalized_kind}"
                    symbols.append(Symbol(
                        name=constraint_name,
                        qualified_name=f"{table_name}.{constraint_name}",
                        kind="constraint",
                        relative_path=relative_path,
                        line=line,
                        end_line=column_end_line,
                        signature=constraint_kind.upper(),
                    ))
                ref = re.search(rf"(?is)REFERENCES\s+(?P<target>{_QUALIFIED})", cleaned_code)
                if ref:
                    target = self._clean_identifier(ref.group("target"))
                    references.append(ReferenceRecord(target, "foreign_key", line, f"{table_name}.{column_name}"))

        # CTE declarations are named query-scoped structures.
        cte_pattern = re.compile(rf"(?is)(?:WITH\s+(?:RECURSIVE\s+)?|,)\s*(?P<name>{_IDENTIFIER})\s*(?:\([^)]*\))?\s+AS\s*\(")
        for match in cte_pattern.finditer(masked):
            name = self._clean_identifier(match.group("name"))
            line = source.count("\n", 0, match.start()) + 1
            symbols.append(Symbol(name, name, "cte", relative_path, line, line))

        # Capture table references in common relational clauses.
        ref_pattern = re.compile(rf"(?is)\b(?:FROM|JOIN|UPDATE|INTO)\s+(?P<target>{_QUALIFIED})")
        for match in ref_pattern.finditer(masked):
            target = self._clean_identifier(match.group("target"))
            if target.upper() in {"SELECT", "VALUES", "UNNEST"}:
                continue
            line = source.count("\n", 0, match.start()) + 1
            references.append(ReferenceRecord(target, "table_reference", line))

        index_target_pattern = re.compile(
            rf"(?is)\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+{_QUALIFIED}\s+ON\s+(?P<target>{_QUALIFIED})"
        )
        for match in index_target_pattern.finditer(masked):
            references.append(ReferenceRecord(
                self._clean_identifier(match.group("target")),
                "index_target",
                source.count("\n", 0, match.start()) + 1,
            ))

        symbols.sort(key=lambda item: (item.line, item.qualified_name, item.kind))
        references.sort(key=lambda item: (item.line, item.target, item.kind))
        return ParsedSource(
            symbols=tuple(symbols),
            imports=(),
            references=tuple(references),
        )

    @staticmethod
    def _clean_identifier(value: str) -> str:
        parts = []
        for part in re.split(r"\s*\.\s*", value.strip()):
            parts.append(part.strip().strip('"`[]'))
        return ".".join(parts)

    @staticmethod
    def _mask_comments_and_literals(source: str) -> str:
        """Replace comments and quoted literals with spaces, preserving offsets."""
        pattern = re.compile(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'", re.MULTILINE)
        return pattern.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), source)

    @staticmethod
    def _matching_paren(source: str, opening: int) -> int | None:
        depth = 0
        for index in range(opening, len(source)):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    return index
        return None

    @staticmethod
    def _split_top_level(source: str) -> list[tuple[str, int]]:
        parts = []
        depth = 0
        start = 0
        quote = None
        for index, char in enumerate(source):
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in "'\"`":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif char == "," and depth == 0:
                parts.append((source[start:index], start))
                start = index + 1
        parts.append((source[start:], start))
        return parts

"""Language-specific Tree-sitter parser for dockerfile."""

import re

from coding_agent.parsers.base import ReferenceRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class DockerfileParser(GrammarParser):
    """Extract structural syntax information from dockerfile files."""
    language = 'dockerfile'
    grammar_name = 'dockerfile'
    extensions = ()
    filenames = ('Dockerfile', 'Containerfile')
    symbol_node_kinds = {'from_instruction': 'base_image', 'run_instruction': 'run_instruction', 'copy_instruction': 'copy_instruction', 'workdir_instruction': 'workdir', 'expose_instruction': 'expose'}
    name_fields = ('name', 'source', 'image')

    def _extract_additional_symbols(self, root, *, relative_path: str, source_bytes: bytes):
        source = source_bytes.decode("utf-8", "replace")
        symbols = []
        for match in re.finditer(r"(?im)^\s*FROM\s+(?P<image>[^\s]+)(?:\s+AS\s+(?P<stage>[A-Za-z_][\w.-]*))?", source):
            if match.group("stage"):
                line = source.count("\n", 0, match.start()) + 1
                symbols.append(Symbol(
                    name=match.group("stage"),
                    qualified_name=match.group("stage"),
                    kind="build_stage",
                    relative_path=relative_path,
                    line=line,
                    end_line=line,
                    declared_type=match.group("image"),
                ))
        return symbols

    def _extract_references(self, root, source_bytes: bytes):
        source = source_bytes.decode("utf-8", "replace")
        references = []
        for match in re.finditer(r"(?im)^\s*FROM\s+(?P<image>[^\s]+)", source):
            references.append(ReferenceRecord(
                match.group("image"), "docker_base_image",
                source.count("\n", 0, match.start()) + 1,
            ))
        for match in re.finditer(r"(?im)^\s*COPY\s+--from=(?P<stage>[^\s]+)", source):
            references.append(ReferenceRecord(
                match.group("stage"), "docker_stage_reference",
                source.count("\n", 0, match.start()) + 1,
            ))
        return references

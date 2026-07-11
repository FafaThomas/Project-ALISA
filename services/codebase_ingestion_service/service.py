from pathlib import Path
import json

from parsers.parser_dispatcher import ParserDispatcher
from models.parsed_document import ParsedDocument

from collectors.workspace_collector import WorkspaceCollector
from collectors.source_collector import SourceCollector
from collectors.manifest_collector import ManifestCollector

from models.project_context import ProjectContext

from extractors.extractor_dispatcher import ExtractorDispatcher
from extractors.python_import_extractor import PythonImportExtractor
from extractors.chunk_dispatcher import ChunkDispatcher
from extractors.metadata_dispatcher import MetadataDispatcher
from builders.dependency_graph_builder import DependencyGraphBuilder
from resolvers.dependency_resolver import DependencyResolver
from extractors.call_dispatcher import CallDispatcher
from builders.call_graph_builder import CallGraphBuilder

class CodebaseIngestionService:

    def __init__(self):

        self.workspace = WorkspaceCollector()

        self.source = SourceCollector()

        self.manifest = ManifestCollector()

        self.parser_dispatcher = ParserDispatcher()

        self.extractor_dispatcher = ExtractorDispatcher()

        self.chunk_dispatcher = ChunkDispatcher()

        self.metadata_dispatcher = MetadataDispatcher()

        self.dependency_builder = DependencyGraphBuilder()

        self.dependency_resolver = DependencyResolver()

        self.call_dispatcher = CallDispatcher()

        self.call_graph_builder = CallGraphBuilder()


    def parse_sources(self, source_collection):

        parsed_documents = []

        for source in source_collection.files:

            parser = self.parser_dispatcher.get_parser(source.parser)

            parse_result = parser.parse(source)

            extractor = self.extractor_dispatcher.get(source.parser)

            symbols = []

            if extractor and parse_result.tree is not None:

                symbols = extractor.extract(parse_result.tree)

            imports = []

            if parse_result.tree is not None:

                import_extractor = PythonImportExtractor()

                imports = import_extractor.extract(parse_result.tree)

            chunks = []

            if parse_result.tree is not None:

                chunk_extractor = self.chunk_dispatcher.get(source.parser)

                if chunk_extractor:

                    chunks = chunk_extractor.extract(parse_result)

            metadata = {}

            metadata_extractor = self.metadata_dispatcher.get(source.parser)

            if metadata_extractor:

                metadata = metadata_extractor.extract(parse_result)

            calls = []

            if parse_result.tree is not None:

                call_extractor = self.call_dispatcher.get(source.parser)

                if call_extractor:

                    calls = call_extractor.extract(parse_result)

            document = ParsedDocument(

                relative_path=str(source.relative_path),

                language=source.language,

                parser=source.parser,

                interpreter=source.interpreter,

                symbols=symbols,

                imports=imports,

                chunks=chunks,

                metadata=metadata,

                calls=calls

            )

            parsed_documents.append(document)

        return parsed_documents

    def create_project(self, project_path: str | Path):

        workspace = self.workspace.collect(project_path)

        source_collection = self.source.collect(workspace)

        manifest_collection = self.manifest.collect(workspace)

        parsed_documents = self.parse_sources(source_collection)

        dependency_graph = self.dependency_builder.build(parsed_documents)

        dependency_graph = self.dependency_resolver.resolve(
            dependency_graph
        )

        call_graph = self.call_graph_builder.build(parsed_documents)

        self.save_json(
            "parsed_documents.json",
            [
                document.model_dump(mode="json")
                for document in parsed_documents
            ]
        )

        self.save_json(
            "dependency_graph.json",
            dependency_graph.model_dump(mode="json")
        )

        self.save_json(
            "call_graph.json",
            call_graph.model_dump(mode="json")
        )

        context = ProjectContext(

            workspace=workspace,

            source_collection=source_collection,

            manifest_collection=manifest_collection,

        )

        self.save_project_context(context)

        print()

        print("=" * 60)
        print(workspace.name)
        print("=" * 60)

        print()

        print(f"Source Files      : {context.source_collection.total_files}")

        print(f"Manifest Files    : {context.manifest_collection.total_documents}")

        print(f"Parsed Documents : {len(parsed_documents)}")

        print()
        return context

    def save_json(self, filename: str, data):

        output = Path("output")

        output.mkdir(exist_ok=True)

        output_file = output / filename

        output_file.write_text(

            json.dumps(

                data,

                indent=4,

                ensure_ascii=False,

            ),

            encoding="utf-8",

        )

    def save_project_context(self, context: ProjectContext):

        output = Path("output")

        output.mkdir(exist_ok=True)

        output_file = output / "project_context.json"

        output_file.write_text(

            context.model_dump_json(

                indent=4

            ),

            encoding="utf-8"

        )

    def update_project(self, project_path: str | Path):

        print("Not implemented.")

    def rebuild_project(self, project_path: str | Path):

        print("Not implemented.")

    def delete_project(self, project_name: str):

        print("Not implemented.")

    def get_project(self, project_name: str):

        print("Not implemented.")

    def list_projects(self):

        print("Not implemented.")
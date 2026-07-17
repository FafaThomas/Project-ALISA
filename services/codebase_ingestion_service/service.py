from pathlib import Path
import json
from parsers.parser_dispatcher import ParserDispatcher
from models.parsed_document import ParsedDocument
from parsers.manifest_parser import ManifestParser
from extractors.manifest_chunk_extractor import ManifestChunkExtractor
from collectors.workspace_collector import WorkspaceCollector
from collectors.source_collector import SourceCollector
from collectors.manifest_collector import ManifestCollector
from collectors.sql_collector import SQLCollector
from sql.scanners.sql_scanner import SQLScanner
from sql.parsers.parser_dispatcher import SQLParserDispatcher
from sql.builders.parsed_document_builder import ParsedDocumentBuilder
from models.project_context import ProjectContext
from extractors.extractor_dispatcher import ExtractorDispatcher
from extractors.python_import_extractor import PythonImportExtractor
from extractors.chunk_dispatcher import ChunkDispatcher
from extractors.metadata_dispatcher import MetadataDispatcher
from builders.dependency_graph_builder import DependencyGraphBuilder
from resolvers.dependency_resolver import DependencyResolver
from extractors.call_dispatcher import CallDispatcher
from builders.call_graph_builder import CallGraphBuilder
from resolvers.call_resolver import CallResolver
from storage.vector_storage import VectorStorage

class CodebaseIngestionService:

    def __init__(self):

        self.workspace = WorkspaceCollector()

        self.source = SourceCollector()

        self.manifest = ManifestCollector()

        self.sql = SQLCollector()

        self.sql_scanner = SQLScanner()

        self.sql_parser_dispatcher = SQLParserDispatcher()

        self.sql_parsed_document_builder = ParsedDocumentBuilder()

        self.parser_dispatcher = ParserDispatcher()

        self.extractor_dispatcher = ExtractorDispatcher()

        self.chunk_dispatcher = ChunkDispatcher()

        self.metadata_dispatcher = MetadataDispatcher()

        self.dependency_builder = DependencyGraphBuilder()

        self.dependency_resolver = DependencyResolver()

        self.call_dispatcher = CallDispatcher()

        self.call_graph_builder = CallGraphBuilder()

        self.call_resolver = CallResolver()

        self.vector_storage = VectorStorage()


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

    def parse_manifests(
        self,
        manifest_collection,
    ):

        parsed_documents = []

        parser = ManifestParser()

        chunk_extractor = ManifestChunkExtractor()

        for manifest in manifest_collection.documents:

            parse_result = parser.parse(manifest)

            chunks = chunk_extractor.extract(parse_result)

            metadata = {

                "document_type": manifest.document_type

            }

            document = ParsedDocument(

                relative_path=manifest.relative_path,

                language="manifest",

                parser="manifest",

                interpreter="",

                symbols=[],

                imports=[],

                chunks=chunks,

                metadata=metadata,

                calls=[]

            )

            parsed_documents.append(document)

        return parsed_documents

    def parse_sql(self, sql_collection):

        sql_collection = self.sql_scanner.scan(sql_collection)

        parsed_documents = []

        for document in sql_collection.documents:

            parser = self.sql_parser_dispatcher.get_parser(
                document.parser
            )

            parse_result = parser.parse(document)

            parsed_document = self.sql_parsed_document_builder.build(
                parse_result
            )

            parsed_documents.append(parsed_document)

        return parsed_documents

    def create_project(self, project_path: str | Path):

        workspace = self.workspace.collect(project_path)

        source_collection = self.source.collect(workspace)

        sql_collection = self.sql.collect(workspace)

        manifest_collection = self.manifest.collect(workspace)

        source_documents = self.parse_sources(source_collection)

        sql_documents = self.parse_sql(sql_collection)

        manifest_documents = self.parse_manifests(manifest_collection)

        parsed_documents = [

            *source_documents,

            *sql_documents,

            *manifest_documents,

        ]

        dependency_graph = self.dependency_builder.build(parsed_documents)

        dependency_graph = self.dependency_resolver.resolve(
            dependency_graph
        )

        call_graph = self.call_graph_builder.build(parsed_documents)

        call_graph = self.call_resolver.resolve(
            call_graph,
            parsed_documents
        )

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

            sql_collection=sql_collection,

            manifest_collection=manifest_collection,

        )

        self.save_project_context(context)

        self.vector_storage.store(
            project_name=workspace.name,
            parsed_documents=parsed_documents,
        )

        print()

        print("=" * 60)
        print(workspace.name)
        print("=" * 60)

        print()

        print(f"Source Files      : {context.source_collection.total_files}")

        print(f"SQL Files         : {context.sql_collection.total_documents}")

        print(f"Manifest Files    : {context.manifest_collection.total_documents}")

        print(f"Parsed Documents  : {len(parsed_documents)}")

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
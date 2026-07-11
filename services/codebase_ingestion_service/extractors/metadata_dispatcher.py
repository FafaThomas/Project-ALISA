from extractors.generic_metadata_extractor import GenericMetadataExtractor
from extractors.python_metadata_extractor import PythonMetadataExtractor


class MetadataDispatcher:

    def __init__(self):

        self.extractors = {
            "tree_sitter_python": PythonMetadataExtractor(),
            "generic": GenericMetadataExtractor(),
        }

    def get(self, parser_name: str):

        return self.extractors.get(
            parser_name,
            GenericMetadataExtractor(),
        )
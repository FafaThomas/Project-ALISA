from models.sql_detection import SQLDetectionResult


class GenericDetector:

    def detect(self, document):

        return SQLDetectionResult(

            dialect="generic",

            parser="tree_sitter_sql",

            confidence=1.0,

            evidence=["Default SQL"]

        )
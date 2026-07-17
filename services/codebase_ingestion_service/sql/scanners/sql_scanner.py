from sql.scanners.detector_dispatcher import (
    SQLDetectorDispatcher
)


class SQLScanner:

    def __init__(self):

        self.dispatcher = SQLDetectorDispatcher()

    def scan(self, sql_collection):

        for document in sql_collection.documents:

            detection = self.dispatcher.detect(document)

            document.parser = detection.parser

            document.dialect = detection.dialect

            document.confidence = detection.confidence

            document.evidence = detection.evidence

        return sql_collection
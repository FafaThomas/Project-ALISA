from sql.models.sql_detection import SQLDetectionResult
from sql.enums.sql_dialect import SQLDialect

from sql.scanners.mysql_detector import MySQLDetector
from sql.scanners.oracle_detector import OracleDetector
from sql.scanners.postgres_detector import PostgreSQLDetector
from sql.scanners.sqlite_detector import SQLiteDetector
from sql.scanners.tsql_detector import TSQLDetector


class SQLDetectorDispatcher:

    def __init__(self):

        self.detectors = [

            TSQLDetector(),

            PostgreSQLDetector(),

            MySQLDetector(),

            SQLiteDetector(),

            OracleDetector(),

        ]

    def detect(self, document):

        results = []

        #
        # Ask every detector
        #
        for detector in self.detectors:

            result = detector.detect(document)

            if result is not None:

                results.append(result)

        #
        # Nobody matched
        #
        if not results:

            return SQLDetectionResult(

                parser="tree_sitter_sql",

                dialect=SQLDialect.ANSI,

                confidence=0,

                evidence=[],

            )

        #
        # Highest confidence wins
        #
        best = max(
            results,
            key=lambda r: r.confidence,
        )

        return best
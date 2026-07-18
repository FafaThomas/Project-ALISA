from sql.enums.sql_dialect import SQLDialect
from sql.models.sql_detection import SQLDetectionResult

from .base_detector import BaseDetector


class SQLiteDetector(BaseDetector):

    def detect(self, document):

        sql = document.raw_source.upper()

        score = 0

        evidence = []


        keywords = {

            "PRAGMA":5,

            "AUTOINCREMENT":5,

            "WITHOUT ROWID":5,

        }


        for keyword,value in keywords.items():

            if keyword in sql:

                score += value

                evidence.append(keyword)

        if not evidence:
            return None

        return SQLDetectionResult(

            parser="sqlite",

            dialect=SQLDialect.SQLITE,

            confidence=len(evidence),

            evidence=evidence,

        )
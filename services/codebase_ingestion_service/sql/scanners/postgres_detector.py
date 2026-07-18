from sql.enums.sql_dialect import SQLDialect
from sql.models.sql_detection import SQLDetectionResult

from .base_detector import BaseDetector


class PostgreSQLDetector(BaseDetector):

    def detect(self, document):

        sql = document.raw_source.upper()

        score = 0

        evidence = []


        keywords = {

            "SERIAL":5,

            "BIGSERIAL":5,

            "RETURNING":4,

            "ILIKE":5,

            "UUID_GENERATE_V4":5,

            "JSONB":5,

        }


        for keyword,value in keywords.items():

            if keyword in sql:

                score += value

                evidence.append(keyword)


        if not evidence:
            return None

        return SQLDetectionResult(

            parser="postgres",

            dialect=SQLDialect.POSTGRESQL,

            confidence=len(evidence),

            evidence=evidence,

        )
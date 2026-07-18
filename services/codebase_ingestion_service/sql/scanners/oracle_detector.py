from sql.enums.sql_dialect import SQLDialect
from sql.models.sql_detection import SQLDetectionResult

from .base_detector import BaseDetector


class OracleDetector(BaseDetector):

    def detect(self, document):

        sql = document.raw_source.upper()

        score = 0

        evidence = []


        keywords = {

            "VARCHAR2":5,

            "NUMBER":4,

            "PACKAGE":5,

            "CREATE OR REPLACE":5,

            "SEQUENCE":2,

        }


        for keyword,value in keywords.items():

            if keyword in sql:

                score += value

                evidence.append(keyword)


        if not evidence:
            return None

        return SQLDetectionResult(

            parser="oracle",

            dialect=SQLDialect.ORACLE,

            confidence=len(evidence),

            evidence=evidence,

        )
from sql.enums.sql_dialect import SQLDialect
from sql.models.sql_detection import SQLDetectionResult

from .base_detector import BaseDetector


class MySQLDetector(BaseDetector):

    def detect(self, document):

        sql = document.raw_source.upper()

        score = 0

        evidence = []


        keywords = {

            "AUTO_INCREMENT":5,

            "ENGINE=":5,

            "UNSIGNED":3,

            "TINYINT":2,

        }


        for keyword,value in keywords.items():

            if keyword in sql:

                score += value

                evidence.append(keyword)

        if not evidence:
            return None

        return SQLDetectionResult(

            parser="mysql",

            dialect=SQLDialect.MYSQL,

            confidence=len(evidence),

            evidence=evidence,

        )
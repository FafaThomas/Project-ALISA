from abc import ABC, abstractmethod

from sql.models.sql_detection import SQLDetectionResult


class BaseDetector(ABC):

    @abstractmethod
    def detect(
        self,
        sql: str,
    ) -> SQLDetectionResult:
        pass
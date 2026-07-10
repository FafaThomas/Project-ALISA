from abc import ABC, abstractmethod

from models.source import SourceDocument
from models.parse_result import ParseResult


class BaseParser(ABC):

    @abstractmethod
    def parse(self, source: SourceDocument) -> ParseResult:
        pass
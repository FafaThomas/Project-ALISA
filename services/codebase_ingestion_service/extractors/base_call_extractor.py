from abc import ABC, abstractmethod

from models.parse_result import ParseResult
from models.call import Call


class BaseCallExtractor(ABC):

    @abstractmethod
    def extract(
        self,
        parse_result: ParseResult
    ) -> list[Call]:

        pass
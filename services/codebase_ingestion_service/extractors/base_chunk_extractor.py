from abc import ABC, abstractmethod


class BaseChunkExtractor(ABC):

    @abstractmethod
    def extract(self, parse_result):
        pass
from abc import ABC, abstractmethod


class SQLParser(ABC):

    @abstractmethod

    def parse(

        self,

        document,

        detection,

    ):

        pass
class EmbeddedSource:

    def __init__(
        self,
        host_source,
        raw_source,
    ):

        self.relative_path = (
            host_source.relative_path
        )

        self.extension = (
            host_source.extension
        )

        self.language = (
            host_source.language
        )

        self.parser = (
            host_source.parser
        )

        self.interpreter = (
            host_source.interpreter
        )

        self.raw_source = raw_source
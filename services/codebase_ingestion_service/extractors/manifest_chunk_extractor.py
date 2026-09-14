from models.chunk import Chunk


class ManifestChunkExtractor:

    MAX_CHARS = 6000

    def extract(
        self,
        parse_result,
    ):

        text = parse_result.source.content

        if not text.strip():
            return []

        lines = text.splitlines()

        chunks = []

        current_lines = []

        current_chars = 0

        start_line = 1

        for index, line in enumerate(
            lines,
            start=1,
        ):

            line_chars = len(line) + 1

            if (
                current_lines
                and current_chars + line_chars
                > self.MAX_CHARS
            ):

                end_line = index - 1

                chunks.append(
                    self.create_chunk(
                        parse_result,
                        current_lines,
                        start_line,
                        end_line,
                    )
                )

                current_lines = []

                current_chars = 0

                start_line = index

            current_lines.append(line)

            current_chars += line_chars

        if current_lines:

            chunks.append(
                self.create_chunk(
                    parse_result,
                    current_lines,
                    start_line,
                    len(lines),
                )
            )

        return chunks

    def create_chunk(
        self,
        parse_result,
        lines,
        start_line,
        end_line,
    ):

        return Chunk(

            id=f"{start_line}:{end_line}",

            type="document",

            name=str(
                parse_result.source.relative_path
            ),

            start_line=start_line,

            end_line=end_line,

            content="\n".join(lines),

        )

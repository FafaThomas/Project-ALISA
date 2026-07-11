from ollama import Client


class OllamaEmbedder:

    def __init__(self):

        self.client = Client(
            host="http://localhost:11434"
        )

        self.model = "nomic-embed-text"

    def embed(
        self,
        text: str
    ) -> list[float]:

        response = self.client.embeddings(
            model=self.model,
            prompt=text
        )

        return response["embedding"]
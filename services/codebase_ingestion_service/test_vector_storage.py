import json

from models.parsed_document import ParsedDocument
from storage.vector_storage import VectorStorage


with open(
    "output/parsed_documents.json",
    "r",
    encoding="utf-8",
) as f:

    data = json.load(f)


parsed_documents = [

    ParsedDocument.model_validate(document)

    for document in data

]


storage = VectorStorage()

storage.store(
    "Project-ALISA",
    parsed_documents,
)

print()

print("========================================")
print("Vector Storage Test Complete")
print("========================================")
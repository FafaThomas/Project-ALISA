import json
import sys
from pathlib import Path

import ollama


MODEL = "qwen2.5:14b"


SYSTEM_PROMPT = """
You are a coding agent.

Your job is to generate source code based on the user's instruction.

You will receive:
- A target directory
- A coding instruction
- A description of the existing codebase

For this task, generate the files necessary to satisfy the instruction.

Return ONLY valid JSON in this exact structure:

{
    "files": [
        {
            "path": "relative/path/to/file.py",
            "content": "complete file contents"
        }
    ]
}

Rules:
- File paths must be relative to the target directory.
- Do not use absolute paths.
- Do not include Markdown code fences.
- Do not include explanations outside the JSON.
- Generate complete files.
"""


def load_request(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def generate_code(request: dict) -> dict:
    prompt = f"""
TARGET DIRECTORY:
{request["target_directory"]}

INSTRUCTION:
{request["instruction"]}

CODEBASE MAP:
{json.dumps(request["codebase_map"], indent=2)}
"""

    response = ollama.chat(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        format="json",
    )

    return json.loads(response["message"]["content"])


def write_files(target_directory: str, result: dict) -> None:
    root = Path(target_directory).resolve()

    for file in result["files"]:
        relative_path = Path(file["path"])

        if relative_path.is_absolute():
            raise ValueError(
                f"Generated path must be relative: {file['path']}"
            )

        destination = (root / relative_path).resolve()

        if root not in destination.parents and destination != root:
            raise ValueError(
                f"Generated path escapes target directory: {file['path']}"
            )

        destination.parent.mkdir(parents=True, exist_ok=True)

        destination.write_text(
            file["content"],
            encoding="utf-8",
        )

        print(f"Created: {destination}")


def main(request_path: str) -> None:
    request = load_request(request_path)

    result = generate_code(request)

    print("\n=== MODEL RESPONSE ===")
    print(json.dumps(result, indent=2))

    write_files(
        request["target_directory"],
        result,
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python agent.py <request.json>")
        sys.exit(1)

    main(sys.argv[1])
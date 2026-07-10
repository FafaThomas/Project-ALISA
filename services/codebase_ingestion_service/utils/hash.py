import hashlib

from pathlib import Path


def sha256_file(file: str | Path) -> str:

    file = Path(file)

    sha = hashlib.sha256()

    with open(file, "rb") as f:

        while chunk := f.read(65536):

            sha.update(chunk)

    return sha.hexdigest()
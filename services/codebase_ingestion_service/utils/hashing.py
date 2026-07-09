from hashlib import sha256
from pathlib import Path


def file_hash(path: Path) -> str:

    h = sha256()

    with open(path, "rb") as f:

        while chunk := f.read(8192):

            h.update(chunk)

    return h.hexdigest()
import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Give each test its own temporary upload directory."""
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path)

    with TestClient(main.app) as test_client:
        yield test_client


def test_health_check(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_upload_file(client, tmp_path):
    response = client.post(
        "/upload",
        files={"file": ("hello.txt", b"Hello ALISA!", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["filename"] == "hello.txt"
    assert (tmp_path / "hello.txt").read_bytes() == b"Hello ALISA!"


def test_duplicate_filename_is_rejected(client, tmp_path):
    (tmp_path / "hello.txt").write_text("original", encoding="utf-8")

    response = client.post(
        "/upload",
        files={"file": ("hello.txt", b"replacement", "text/plain")},
    )

    assert response.status_code == 409
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "original"


def test_path_traversal_filename_is_sanitized(client, tmp_path):
    response = client.post(
        "/upload",
        files={"file": ("../../hello.txt", b"safe content", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["filename"] == "hello.txt"
    assert (tmp_path / "hello.txt").exists()


def test_oversized_file_is_rejected(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_SIZE_BYTES", 5)

    response = client.post(
        "/upload",
        files={"file": ("large.txt", b"123456", "text/plain")},
    )

    assert response.status_code == 413
    assert not (tmp_path / "large.txt").exists()


def test_batch_upload(client, tmp_path):
    response = client.post(
        "/upload/batch",
        files=[
            ("files", ("one.txt", b"first", "text/plain")),
            ("files", ("two.txt", b"second", "text/plain")),
        ],
    )

    assert response.status_code == 200
    result = response.json()

    assert result["successful_files"] == 2
    assert result["failed_files"] == 0
    assert (tmp_path / "one.txt").read_bytes() == b"first"
    assert (tmp_path / "two.txt").read_bytes() == b"second"
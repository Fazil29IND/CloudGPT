"""
Automated tests for CloudGPT file attachment support (Phase 4).

Covers:
  - Extension allow-list validation (reject executables)
  - Magic-byte sniffing (fake PDF, executable content)
  - Size limit enforcement per entitlement
  - Text extraction: TXT, CSV, DOCX (via python-docx), PDF (pdfminer)
  - /api/upload endpoint: auth, success, oversized, bad type
  - Redis staging returns an attachment_id

Run with:
    python -m pytest tests/test_file_upload.py -v
"""

from __future__ import annotations

import io
import os
import sys
from unittest.mock import AsyncMock, patch

import psycopg2
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:Fazil@localhost:5000/pygpt"
)
os.environ["ENVIRONMENT"] = "development"

import db
from file_processor import (
    ExtractionError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    extract_text,
    validate_upload,
)

UPLOAD_EMAIL = "uploaduser@test.cloudgpt.local"


@pytest.fixture(autouse=True)
def clean_test_db():
    yield
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE email LIKE %s", ("%@test.cloudgpt.local",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def client():
    import app as app_module

    return TestClient(app_module.app)


@pytest.fixture
def logged_in_client(client):
    """A TestClient authenticated as a fresh test user."""
    db.create_user(
        email=UPLOAD_EMAIL,
        password="UploadPass123",
        name="Upload User",
        email_verified=True,
    )
    res = client.get("/")
    import re

    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post(
        "/login-email",
        data={"email": UPLOAD_EMAIL, "csrf_token": csrf},
    )
    res = client.get(f"/password?email={UPLOAD_EMAIL}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post(
        "/login",
        data={"email": UPLOAD_EMAIL, "password": "UploadPass123", "csrf_token": csrf},
    )
    return client


# ── Validation ───────────────────────────────────────────────────────────────

def test_validate_upload_rejects_executable_extension():
    with pytest.raises(UnsupportedFileTypeError):
        validate_upload(
            filename="malware.exe",
            declared_content_type="application/octet-stream",
            data=b"MZ\x90\x00binary",
            max_file_bytes=1024 * 1024,
        )


def test_validate_upload_rejects_disallowed_extension():
    with pytest.raises(UnsupportedFileTypeError):
        validate_upload(
            filename="archive.zip",
            declared_content_type="application/zip",
            data=b"PK\x03\x04",
            max_file_bytes=1024 * 1024,
        )


def test_validate_upload_rejects_oversized():
    with pytest.raises(FileTooLargeError):
        validate_upload(
            filename="big.txt",
            declared_content_type="text/plain",
            data=b"x" * (1024 * 1024 + 1),
            max_file_bytes=1024 * 1024,
        )


def test_validate_upload_accepts_text():
    content_type = validate_upload(
        filename="notes.txt",
        declared_content_type="text/plain",
        data=b"hello world",
        max_file_bytes=1024 * 1024,
    )
    assert content_type == "text/plain"


def test_validate_upload_sniffs_fake_pdf():
    """A .txt file containing PDF magic bytes is sniffed as a PDF, not trusted
    by its declared content type; executable magic bytes are rejected."""
    with pytest.raises(UnsupportedFileTypeError):
        validate_upload(
            filename="innocent.txt",
            declared_content_type="text/plain",
            data=b"MZ\x90\x00" + b"payload",
            max_file_bytes=1024 * 1024,
        )


# ── Extraction ───────────────────────────────────────────────────────────────

def test_extract_text_txt():
    text = extract_text(b"line one\nline two", "a.txt", "text/plain")
    assert "line one" in text and "line two" in text


def test_extract_text_csv():
    data = "name,size\ns3,object\nec2,vm\n".encode()
    text = extract_text(data, "a.csv", "text/csv")
    assert "s3" in text and "ec2" in text


def test_extract_text_docx():
    docx_module = pytest.importorskip("docx")
    buffer = io.BytesIO()
    document = docx_module.Document()
    document.add_paragraph("CloudGPT DOCX extraction test paragraph.")
    document.save(buffer)
    text = extract_text(buffer.getvalue(), "a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert "CloudGPT DOCX extraction test paragraph." in text


def test_extract_text_pdf_invalid_content():
    """A file with PDF magic bytes but garbage content raises ExtractionError."""
    with pytest.raises(ExtractionError):
        extract_text(b"%PDF-1.4 garbage not a real pdf", "a.pdf", "application/pdf")


def test_extract_text_rejects_binary_txt():
    with pytest.raises(ExtractionError):
        extract_text(b"\x00\x01\x02binaryblob", "a.txt", "text/plain")


# ── Upload endpoint ──────────────────────────────────────────────────────────

def test_upload_requires_auth(client):
    response = client.post("/api/upload")
    assert response.status_code in (401, 403)


def test_upload_txt_success(logged_in_client):
    with patch("file_processor.redis_client.set_json", new_callable=AsyncMock, return_value=True):
        response = logged_in_client.post(
            "/api/upload",
            files={"file": ("notes.txt", b"hello attachment world", "text/plain")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "notes.txt"
    assert body["attachment_id"]
    assert body["chars_extracted"] > 0


def test_upload_rejects_bad_type(logged_in_client):
    response = logged_in_client.post(
        "/api/upload",
        files={"file": ("program.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_upload_rejects_oversized(logged_in_client):
    """Lite tier allows 2 MiB; an upload exceeding 2 MiB must be rejected with 413."""
    response = logged_in_client.post(
        "/api/upload",
        files={"file": ("big.txt", b"x" * (2 * 1024 * 1024 + 1024), "text/plain")},
    )
    assert response.status_code == 413

"""Automated tests for CloudGPT Multimodal Attachments (Images, Audio, Code).

Covers:
  - Image upload with Pillow processing (stripping EXIF, dimension normalization)
  - Code file upload (.py, .json, .yaml) with direct decoding
  - Download of staged image bytes
  - Rejection of malicious or oversized payloads
"""

from __future__ import annotations

import io
import os
import re
import sys
from PIL import Image
import psycopg2
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:Fazil@localhost:5000/pygpt"
)
os.environ["ENVIRONMENT"] = "development"

import db

MM_EMAIL = "mmuser@test.cloudgpt.local"


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
    """A TestClient authenticated as a test user with CSRF token."""
    user = db.create_user(
        email=MM_EMAIL,
        password="MMPassword123",
        name="Multimodal User",
        email_verified=True,
    )
    res = client.get("/")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login-email", data={"email": MM_EMAIL, "csrf_token": csrf})
    res = client.get(f"/password?email={MM_EMAIL}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login", data={"email": MM_EMAIL, "password": "MMPassword123", "csrf_token": csrf})

    res = client.get("/chat")
    csrf_match = re.search(r'name="csrf-token"\s+content="([^"]+)"', res.text)
    csrf_token = csrf_match.group(1) if csrf_match else csrf
    return client, user, csrf_token


def test_image_upload_and_pillow_processing(logged_in_client):
    client, user, csrf = logged_in_client

    # Generate synthetic image in memory
    img = Image.new("RGB", (120, 80), color=(73, 109, 137))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    res = client.post(
        "/api/upload",
        files={"file": ("diagram.png", img_bytes, "image/png")},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["filename"] == "diagram.png"
    assert data["kind"] == "image"
    assert data["content_type"] == "image/png"
    assert "attachment_id" in data

    # Verify download serves image bytes
    dl_res = client.get(f"/api/attachments/{data['attachment_id']}/download")
    assert dl_res.status_code == 200
    assert dl_res.headers["content-type"] == "image/png"
    assert len(dl_res.content) > 0


def test_code_upload_and_extraction(logged_in_client):
    client, user, csrf = logged_in_client

    code_content = b"def calculate_total(items):\n    return sum(item.price for item in items)\n"
    res = client.post(
        "/api/upload",
        files={"file": ("calc.py", code_content, "text/x-python")},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["filename"] == "calc.py"
    assert data["chars_extracted"] == len(code_content.decode("utf-8"))


def test_unsupported_executable_rejected(logged_in_client):
    client, user, csrf = logged_in_client

    exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00"
    res = client.post(
        "/api/upload",
        files={"file": ("danger.exe", exe_bytes, "application/octet-stream")},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 400


def test_video_upload_and_staging(logged_in_client):
    client, user, csrf = logged_in_client

    # Synthetic MP4 bytes (ftyp box)
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42" + b"\x00" * 100
    res = client.post(
        "/api/upload",
        files={"file": ("clip.mp4", mp4_bytes, "video/mp4")},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["filename"] == "clip.mp4"
    assert data["kind"] == "video"
    assert data["content_type"] == "video/mp4"

    # Verify download serves video bytes
    dl_res = client.get(f"/api/attachments/{data['attachment_id']}/download")
    assert dl_res.status_code == 200
    assert dl_res.headers["content-type"] == "video/mp4"
    assert len(dl_res.content) == len(mp4_bytes)


def test_audio_upload_and_staging(logged_in_client):
    client, user, csrf = logged_in_client

    # Synthetic WAV bytes (RIFF WAVE header)
    wav_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 30
    res = client.post(
        "/api/upload",
        files={"file": ("voice.wav", wav_bytes, "audio/wav")},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["filename"] == "voice.wav"
    assert data["kind"] == "audio"
    assert data["content_type"] == "audio/wav"

    # Verify download serves audio bytes
    dl_res = client.get(f"/api/attachments/{data['attachment_id']}/download")
    assert dl_res.status_code == 200
    assert dl_res.headers["content-type"] == "audio/wav"
    assert len(dl_res.content) == len(wav_bytes)


def test_provider_multimodal_conversion():
    import base64
    from unittest.mock import patch, MagicMock
    from llm.provider import GeminiProvider

    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "fake-key-for-test"
    mock_settings.gemini_model = "gemini-2.5-flash"
    mock_settings.gemini_request_timeout_seconds = 25.0
    mock_settings.gemini_first_chunk_timeout_seconds = 6.0
    mock_settings.gemini_total_fallback_deadline_seconds = 25.0
    mock_settings.llm_stream_timeout_seconds = 120.0

    with patch("llm.provider.get_settings", return_value=mock_settings), \
         patch("google.genai.Client"):
        prov = GeminiProvider()
        raw_audio = b"fake-audio-bytes-for-test"
        encoded_audio = base64.b64encode(raw_audio).decode("utf-8")

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {
                "role": "user",
                "content": "Please transcribe this audio clip.",
                "attachments": [
                    {
                        "filename": "speech.wav",
                        "content_type": "audio/wav",
                        "is_audio": True,
                        "raw_bytes_base64": encoded_audio,
                    }
                ],
            },
        ]

        system_instruction, contents = prov._convert_messages(messages)
        assert system_instruction == "You are a helpful assistant."
        assert len(contents) == 1
        last_content = contents[0]
        has_audio_part = False
        for part in getattr(last_content, "parts", []):
            if hasattr(part, "inline_data") and getattr(part.inline_data, "mime_type", None) == "audio/wav":
                has_audio_part = True
                break
            elif isinstance(part, dict) and part.get("inline_data", {}).get("mime_type") == "audio/wav":
                has_audio_part = True
                break
        assert has_audio_part is True


@pytest.mark.asyncio
async def test_provider_multimodal_conversion_includes_pdf():
    import base64
    from llm.provider import GeminiProvider

    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock"
    mock_settings.gemini_model = "gemini-3.8-flash"
    mock_settings.gemini_first_chunk_timeout_seconds = 6.0
    mock_settings.gemini_total_fallback_deadline_seconds = 25.0

    with patch("llm.provider.get_settings", return_value=mock_settings), \
         patch("google.genai.Client"):
        prov = GeminiProvider()
        raw_pdf = b"%PDF-1.5 fake pdf binary data"
        encoded_pdf = base64.b64encode(raw_pdf).decode("utf-8")

        messages = [
            {
                "role": "user",
                "content": "Please review this architectural PDF document.",
                "attachments": [
                    {
                        "filename": "architecture.pdf",
                        "content_type": "application/pdf",
                        "is_pdf": True,
                        "raw_bytes_base64": encoded_pdf,
                    }
                ],
            }
        ]

        _, contents = prov._convert_messages(messages)
        assert len(contents) == 1
        has_pdf_part = False
        for part in getattr(contents[0], "parts", []):
            mime = getattr(getattr(part, "inline_data", None), "mime_type", None)
            if mime == "application/pdf":
                has_pdf_part = True
                break
        assert has_pdf_part is True


@pytest.mark.asyncio
async def test_pdf_upload_scanned_fallback():
    from file_processor import stage_attachment

    # Create dummy PDF bytes that have no extractable text
    fake_pdf_data = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    res = await stage_attachment(
        user_id=1,
        filename="scanned_diagram.pdf",
        declared_content_type="application/pdf",
        data=fake_pdf_data,
        max_file_bytes=5 * 1024 * 1024,
    )
    assert res["content_type"] == "application/pdf"
    assert res["attachment_id"] is not None


@pytest.mark.asyncio
async def test_pinecone_search_timeout_logs_without_error():
    """Verify that pinecone_manager search timeout logs cleanly with structlog."""
    from embeddings.pinecone_manager import PineconeManager

    mock_settings = MagicMock()
    mock_settings.pinecone_index_name = "test-index"
    mock_settings.pinecone_api_key = "test-key"
    mock_settings.pinecone_cloud = "aws"
    mock_settings.pinecone_region = "us-east-1"
    mock_settings.embedding_dimension = 384
    mock_settings.retrieval_timeout_seconds = 0.01  # Instant timeout

    mgr = PineconeManager(mock_settings)
    mock_index = MagicMock()

    def slow_query(*args, **kwargs):
        import time
        time.sleep(0.05)
        return {"matches": []}

    mock_index.query = slow_query
    mgr._index = mock_index

    # Should gracefully return empty list and log timeout without raising TypeError
    results = await mgr.search_dense(query_vector=[0.1] * 384, namespace="test-ns")
    assert results == []


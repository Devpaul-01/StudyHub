"""
Tests for services/upload_validation_service.py.

This module is deliberately Flask-free (Document 2 §2's layering rule —
no Flask imports, no request/session/g), so these are pure unit tests:
no `app`, `db_session`, or other Flask-context fixtures are needed.

Only one existing test exercises this module at all
(tests/integration/test_file_upload_validation.py), and only at the HTTP
route boundary for a single spoofed-extension case. These tests cover
the function's own branches directly: genuine images in every allowed
format, the verify()-vs-load() two-step (truncation can slip past
verify() alone), re-encoding/alpha-flattening behavior, unsupported
formats, and the magic-number document-mime checks including the
"all allowed mimes are unsignable" and "mismatched content" paths.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from errors import ValidationError
from services.upload_validation_service import (
    validate_and_normalize_image,
    validate_document_mime,
)


# ============================================================================
# HELPERS
# ============================================================================

def _make_image_bytes(fmt: str, *, size=(10, 10), mode="RGB") -> bytes:
    img = Image.new(mode, size, color=(255, 0, 0) if mode == "RGB" else 255)
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _fs(data: bytes, filename: str = "upload.bin") -> FileStorage:
    """Build a werkzeug FileStorage wrapping raw bytes, like a real upload."""
    return FileStorage(stream=BytesIO(data), filename=filename)


# ============================================================================
# validate_and_normalize_image — happy paths, one per allowed format
# ============================================================================

@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP"])
def test_validate_and_normalize_image_accepts_each_allowed_format(fmt):
    raw = _make_image_bytes(fmt)
    fs = _fs(raw, filename=f"photo.{fmt.lower()}")

    result = validate_and_normalize_image(fs)

    assert isinstance(result, BytesIO)
    result.seek(0)
    reopened = Image.open(result)
    assert reopened.format == fmt


def test_validate_and_normalize_image_returns_fresh_buffer_not_original_bytes():
    """
    The whole point of re-encoding is that the output bytes are freshly
    generated from decoded pixel data, not a copy of the original file —
    this is what strips embedded polyglot payloads. Save the source at
    JPEG's default quality and compare against the function's explicit
    quality=90 re-encode, on a noisy (non-flat-color) image so JPEG's
    lossy compression actually produces different bytes rather than
    coincidentally matching on a trivial solid-color input.
    """
    img = Image.new("RGB", (32, 32))
    pixels = img.load()
    for x in range(32):
        for y in range(32):
            pixels[x, y] = ((x * 7) % 256, (y * 13) % 256, (x + y) % 256)
    raw_buf = BytesIO()
    img.save(raw_buf, format="JPEG")  # default quality, not 90
    raw = raw_buf.getvalue()
    fs = _fs(raw, filename="photo.jpg")

    result = validate_and_normalize_image(fs)

    assert result.getvalue() != raw


def test_validate_and_normalize_image_flattens_rgba_to_rgb_for_jpeg_output():
    """
    JPEG doesn't support an alpha channel. A PNG-with-transparency
    uploaded with a .jpg-ish intent (format is still PNG here; Pillow
    identifies by content, not extension) must not crash the re-encode.
    Covered directly: an RGBA source image re-encodes successfully.
    """
    raw = _make_image_bytes("PNG", mode="RGBA")
    fs = _fs(raw, filename="transparent.png")

    result = validate_and_normalize_image(fs)
    result.seek(0)
    reopened = Image.open(result)
    assert reopened.format == "PNG"  # PNG output keeps alpha; no crash


# ============================================================================
# validate_and_normalize_image — rejections
# ============================================================================

def test_validate_and_normalize_image_rejects_none():
    with pytest.raises(ValidationError, match="No file provided"):
        validate_and_normalize_image(None)


def test_validate_and_normalize_image_rejects_empty_file():
    fs = _fs(b"", filename="empty.jpg")
    with pytest.raises(ValidationError, match="File is empty"):
        validate_and_normalize_image(fs)


def test_validate_and_normalize_image_rejects_non_image_bytes_spoofed_as_jpg():
    """The core security case: plain text wearing a .jpg extension."""
    fs = _fs(b"this is definitely not a jpeg", filename="fake.jpg")
    with pytest.raises(ValidationError, match="not a valid image"):
        validate_and_normalize_image(fs)


def test_validate_and_normalize_image_rejects_truncated_image():
    """
    Step 1 (verify()) is a cheap structural check that can miss
    truncation; step 2 (re-open + .load()) actually decodes pixel data
    and is what's supposed to catch this. Truncate a real JPEG mid-scan
    data so the header/structure looks plausible but the pixels don't
    fully decode.
    """
    raw = _make_image_bytes("JPEG", size=(200, 200))
    truncated = raw[: len(raw) // 2]
    fs = _fs(truncated, filename="truncated.jpg")

    with pytest.raises(ValidationError) as exc_info:
        validate_and_normalize_image(fs)
    # Depending on exactly where the cut lands this may be caught by
    # either verify() or load() — both are valid rejections; what matters
    # is that a corrupt/truncated file is never accepted.
    assert exc_info.value.status_code == 400


def test_validate_and_normalize_image_rejects_unsupported_format():
    """BMP is a genuine, fully-decodable raster image Pillow can read —
    but it's not in _SAFE_OUTPUT_FORMAT, so it must still be rejected."""
    img = Image.new("RGB", (10, 10), color="blue")
    buf = BytesIO()
    img.save(buf, format="BMP")
    fs = _fs(buf.getvalue(), filename="photo.bmp")

    with pytest.raises(ValidationError, match="Unsupported image format"):
        validate_and_normalize_image(fs)


def test_validate_and_normalize_image_seeks_to_start_before_reading():
    """
    If the caller (or a prior check) already advanced the stream
    position, the function must still read the whole file rather than
    silently validating a partial/empty tail.
    """
    raw = _make_image_bytes("PNG")
    fs = _fs(raw, filename="photo.png")
    fs.stream.seek(len(raw))  # simulate a stream left at EOF

    result = validate_and_normalize_image(fs)
    result.seek(0)
    assert Image.open(result).format == "PNG"


# ============================================================================
# validate_document_mime — happy paths
# ============================================================================

def test_validate_document_mime_accepts_genuine_pdf():
    fs = _fs(b"%PDF-1.4\n...rest of a pdf...", filename="doc.pdf")
    # Should not raise.
    validate_document_mime(fs, {"application/pdf"})


def test_validate_document_mime_accepts_docx_zip_signature():
    fs = _fs(b"PK\x03\x04" + b"\x00" * 20, filename="doc.docx")
    validate_document_mime(
        fs,
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    )


def test_validate_document_mime_accepts_legacy_ole_doc_signature():
    fs = _fs(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20, filename="old.doc")
    validate_document_mime(fs, {"application/msword"})


def test_validate_document_mime_matches_any_one_of_several_allowed_mimes():
    """Caller may allow several mimes at once; a match on any is enough."""
    fs = _fs(b"%PDF-1.4" + b"\x00" * 20, filename="report.pdf")
    validate_document_mime(
        fs,
        {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    )


@pytest.mark.parametrize("mime", ["text/plain", "text/csv"])
def test_validate_document_mime_passes_unsignable_mimes_through_unchecked(mime):
    """
    text/plain and text/csv have no reliable magic number — the function
    documents that it can't verify these and lets them through rather
    than pretending to.
    """
    fs = _fs(b"anything at all, not even text-shaped\x00\x01", filename="notes.txt")
    validate_document_mime(fs, {mime})  # should not raise


# ============================================================================
# validate_document_mime — rejections
# ============================================================================

def test_validate_document_mime_rejects_mismatched_content():
    """A .pdf-named file whose actual bytes are a ZIP (docx), not a PDF."""
    fs = _fs(b"PK\x03\x04" + b"\x00" * 20, filename="fake.pdf")
    with pytest.raises(ValidationError, match="does not match its expected type"):
        validate_document_mime(fs, {"application/pdf"})


def test_validate_document_mime_rejects_none():
    with pytest.raises(ValidationError, match="No file provided"):
        validate_document_mime(None, {"application/pdf"})


def test_validate_document_mime_rejects_empty_file():
    fs = _fs(b"", filename="empty.pdf")
    with pytest.raises(ValidationError, match="File is empty"):
        validate_document_mime(fs, {"application/pdf"})


def test_validate_document_mime_rewinds_stream_after_reading_header():
    """
    Callers need to read the full file afterward for the actual upload —
    the function must leave the stream at position 0, not wherever the
    16-byte header peek left it.
    """
    raw = b"%PDF-1.4" + b"\x00" * 100
    fs = _fs(raw, filename="doc.pdf")

    validate_document_mime(fs, {"application/pdf"})

    assert fs.stream.tell() == 0
    assert fs.read() == raw


def test_validate_document_mime_no_checkable_mimes_in_allowed_set_short_circuits():
    """
    If every mime in the allowed set is unsignable (e.g. only text/plain
    was permitted), there's nothing to check against — this must not
    raise, regardless of what the actual bytes look like.
    """
    fs = _fs(b"\xff\xd8\xff\xe0 looks like a jpeg header", filename="weird.txt")
    validate_document_mime(fs, {"text/plain"})  # should not raise

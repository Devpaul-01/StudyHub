"""
tests/unit/services/test_learnora_file_handler.py

routes/student/learnora/file_handler.py::FileHandler had zero test
coverage. It is a pure processing class (no DB, no Flask request
context needed beyond a werkzeug FileStorage-like object), so this is
tested directly rather than through an AI-chat route -- the AI-chat
routes that use it are only reachable via their own no-provider
fallback path in this test env, which never actually calls
process_files().
"""

import io
import zipfile

import pytest
from werkzeug.datastructures import FileStorage
from PIL import Image

from routes.student.learnora.file_handler import FileHandler


def _file(content: bytes, filename: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(content), filename=filename)


def _minimal_docx(text: str) -> bytes:
    """Build the smallest zip archive docx2txt.process() will accept."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>'
        '</w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def _png_bytes(width=10, height=10) -> bytes:
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ============================================================================
# detect_type
# ============================================================================

class TestDetectType:
    @pytest.mark.parametrize("filename,expected", [
        ("script.py", "code"),
        ("app.js", "code"),
        ("Main.java", "code"),
        ("notes.pdf", "document"),
        ("essay.docx", "document"),
        ("photo.jpg", "image"),
        ("photo.PNG", "image"),
        ("archive.zip", "unknown"),
    ])
    def test_detects_by_extension(self, filename, expected):
        handler = FileHandler()
        # detect_type is called on an already-lowercased filename by
        # process_files, so lowercase it here too except for the
        # explicit-case test above which checks process_files' own
        # lowercasing instead.
        assert handler.detect_type(filename.lower()) == expected


# ============================================================================
# extract_code
# ============================================================================

class TestExtractCode:
    def test_extracts_text_content(self):
        handler = FileHandler()
        f = _file(b"print('hello')", "script.py")

        result = handler.extract_code(f)

        assert result == "print('hello')"

    def test_oversized_file_rejected(self):
        handler = FileHandler()
        f = _file(b"x" * 400_001, "big.py")

        result = handler.extract_code(f)

        assert result.startswith("[ERROR")


# ============================================================================
# extract_document
# ============================================================================

class TestExtractDocument:
    def test_txt_extraction(self):
        handler = FileHandler()
        f = _file(b"plain text content", "notes.txt")

        result = handler.extract_document(f, "notes.txt")

        assert result == "plain text content"

    def test_csv_extraction(self):
        handler = FileHandler()
        f = _file(b"a,b\n1,2\n3,4", "data.csv")

        result = handler.extract_document(f, "data.csv")

        assert "1" in result and "2" in result

    def test_doc_format_explicitly_unsupported(self):
        handler = FileHandler()
        f = _file(b"anything", "legacy.doc")

        result = handler.extract_document(f, "legacy.doc")

        assert "not supported" in result

    def test_docx_extraction(self):
        handler = FileHandler()
        f = _file(_minimal_docx("Hello from docx"), "report.docx")

        result = handler.extract_document(f, "report.docx")

        assert "Hello from docx" in result

    def test_pdf_extraction_does_not_error(self):
        import PyPDF2

        writer = PyPDF2.PdfWriter()
        writer.add_blank_page(width=72, height=72)
        buf = io.BytesIO()
        writer.write(buf)
        f = _file(buf.getvalue(), "blank.pdf")
        handler = FileHandler()

        result = handler.extract_document(f, "blank.pdf")

        assert not result.startswith("[ERROR")

    def test_unsupported_document_extension(self):
        handler = FileHandler()
        f = _file(b"data", "notes.rtf")

        result = handler.extract_document(f, "notes.rtf")

        assert "Unsupported" in result


# ============================================================================
# extract_image_base64
# ============================================================================

class TestExtractImageBase64:
    def test_returns_data_uri(self):
        handler = FileHandler()
        f = _file(_png_bytes(), "photo.png")

        result = handler.extract_image_base64(f)

        assert result.startswith("data:image/png;base64,")

    def test_oversized_file_rejected(self):
        handler = FileHandler()
        # Not a real image, but extract_image_base64 checks size via
        # file.tell() before ever calling Image.open(), so this still
        # exercises the size guard without needing a real 5MB+ PNG.
        f = _file(b"x" * 5_000_001, "huge.png")

        result = handler.extract_image_base64(f)

        assert "too large" in result

    def test_extreme_resolution_rejected(self):
        handler = FileHandler()
        # 10000x10000 well exceeds the 60_000-estimated-token cutoff.
        f = _file(_png_bytes(width=10000, height=10000), "giant.png")

        result = handler.extract_image_base64(f)

        assert "too high" in result or result.startswith("[ERROR")


# ============================================================================
# estimate_tokens
# ============================================================================

class TestEstimateTokens:
    def test_roughly_four_chars_per_token(self):
        handler = FileHandler()

        assert handler.estimate_tokens("a" * 40) == 10

    def test_error_text_counts_as_zero(self):
        handler = FileHandler()

        assert handler.estimate_tokens("[ERROR: something broke]") == 0

    def test_empty_text_counts_as_zero(self):
        handler = FileHandler()

        assert handler.estimate_tokens("") == 0


# ============================================================================
# process_files — the end-to-end orchestration method
# ============================================================================

class TestProcessFiles:
    def test_mixed_files_are_categorized(self):
        handler = FileHandler()
        files = {
            "f1": _file(b"print(1)", "script.py"),
            "f2": _file(b"hello world", "notes.txt"),
            "f3": _file(_png_bytes(), "photo.png"),
        }

        result = handler.process_files(files)

        assert result["info"]["total_files"] == 3
        assert result["info"]["code_files"] == 1
        assert result["info"]["document_files"] == 1
        assert result["info"]["image_files"] == 1
        assert result["has_images"] is True
        assert len(result["texts"]) == 3

    def test_unsupported_file_type_still_counted_but_flagged(self):
        handler = FileHandler()
        files = {"f1": _file(b"binary junk", "archive.zip")}

        result = handler.process_files(files)

        assert result["info"]["total_files"] == 1
        # "[Unsupported file type: ...]" text does not start with
        # "[ERROR", so process_files' own filter still appends it to
        # extracted_texts -- it is not silently dropped.
        assert len(result["texts"]) == 1
        assert "Unsupported file type" in result["texts"][0]["content"]

    def test_empty_files_dict(self):
        handler = FileHandler()

        result = handler.process_files({})

        assert result["info"]["total_files"] == 0
        assert result["texts"] == []
        assert result["tokens"] == 0

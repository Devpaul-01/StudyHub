"""
StudyHub - Learnora: FileHandler

Split from learnora.py per Document 1 (Architecture Refactor) §2.4 as part
of Phase 2 (God-file splitting). This is a pure move — the class body is
unchanged from the original learnora.py.

Genuinely learnora-specific — only the chat upload flow uses this today,
so per Document 1 §2.4 it stays under routes/student/learnora/ rather than
moving to services/ (unlike MultiProviderManager/StudyAssistant, which
already moved to services/ai_provider_service.py in the prior phase, since
those are consumed by other blueprints too).
"""

import os
import base64
import mimetypes
import logging

import pandas as pd
from PIL import Image

logger = logging.getLogger(__name__)

class FileHandler:
    def __init__(self):
        self.total_files = 0
        self.doc_files = 0
        self.code_files = 0
        self.image_files = 0
        self.total_tokens = 0
        self.extracted_texts = []
        self.has_images = False

    def process_files(self, files):
        """Process all uploaded files and extract text/data"""
        logger.info(f"📁 Processing {len(files)} files")

        for file_key in files:
            file = files[file_key]
            filename = file.filename.lower()

            logger.info(f"📄 Processing file: {filename}")

            ftype = self.detect_type(filename)

            try:
                if ftype == "code":
                    text = self.extract_code(file)
                    self.code_files += 1

                elif ftype == "document":
                    text = self.extract_document(file, filename)
                    self.doc_files += 1

                elif ftype == "image":
                    text = self.extract_image_base64(file)
                    self.image_files += 1
                    self.has_images = True

                else:
                    text = f"[Unsupported file type: {filename}]"
                    logger.warning(f"⚠️ Unsupported file type: {filename}")

                token_count = self.estimate_tokens(text)
                self.total_tokens += token_count

                if text and not text.startswith("[ERROR"):
                    self.extracted_texts.append({
                        "type": ftype,
                        "content": text,
                        "filename": file.filename
                    })
                    logger.info(f"✅ Extracted {len(text)} chars from {filename}")
                else:
                    logger.error(f"❌ Failed to extract from {filename}: {text}")

                self.total_files += 1

            except Exception as e:
                logger.error(f"❌ Error processing {filename}: {str(e)}", exc_info=True)
                continue

        logger.info(f"✅ Processed {self.total_files} files: {self.doc_files} docs, {self.code_files} code, {self.image_files} images")

        return {
            "texts": self.extracted_texts,
            "tokens": self.total_tokens,
            "has_images": self.has_images,
            "info": {
                "total_files": self.total_files,
                "document_files": self.doc_files,
                "code_files": self.code_files,
                "image_files": self.image_files,
            }
        }

    def detect_type(self, filename):
        if filename.endswith((".py", ".js", ".java", ".ts", ".cpp", ".html", ".css", ".php", ".rb", ".c", ".h")):
            return "code"
        if filename.endswith((".pdf", ".doc", ".docx", ".txt", ".csv")):
            return "document"
        if filename.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return "image"
        return "unknown"

    def extract_code(self, file):
        """Extract code from file"""
        try:
            file.seek(0)
            content = file.read().decode("utf-8", errors="ignore")
            if len(content) > 400_000:
                return "[ERROR: Code file too large. Max 400KB]"
            return content
        except Exception as e:
            logger.error(f"Code extraction error: {str(e)}")
            return f"[ERROR reading code: {str(e)}]"

    def extract_document(self, file, filename):
        """Extract text from documents"""
        try:
            file.seek(0)

            if filename.endswith(".txt"):
                content = file.read().decode("utf-8", errors="ignore")
                if len(content) > 400_000:
                    return "[ERROR: Text file too large. Max 400KB]"
                return content

            if filename.endswith(".csv"):
                df = pd.read_csv(file)
                content = df.to_string()
                if len(content) > 400_000:
                    return "[ERROR: CSV too large. Max 400KB]"
                return content

            if filename.endswith(".doc"):
                return "[ERROR: .doc files not supported. Please upload .docx]"

            if filename.endswith(".docx"):
                import docx2txt
                import tempfile
                file.seek(0)
                with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp:
                    tmp.write(file.read())
                    tmp_path = tmp.name
                try:
                    content = docx2txt.process(tmp_path)
                finally:
                    # Audit Issue 1: always clean up the temp file, even if
                    # docx2txt.process() itself raises — previously the
                    # unlink line never ran on that path either.
                    os.unlink(tmp_path)
                if len(content) > 400_000:
                    return "[ERROR: Document too large. Max 400KB]"
                return content

            if filename.endswith(".pdf"):
                text = ""
                try:
                    import PyPDF2
                    file.seek(0)
                    pdf_reader = PyPDF2.PdfReader(file)
                    for page in pdf_reader.pages:
                        text += page.extract_text() or ""
                    logger.info("✅ Extracted PDF using PyPDF2")
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning(f"PyPDF2 failed: {str(e)}")

                if len(text) > 400_000:
                    return "[ERROR: PDF too large. Max 400KB]"
                return text

            return "[ERROR: Unsupported document format]"
        except Exception as e:
            logger.error(f"Document extraction error: {str(e)}")
            return f"[ERROR reading document: {str(e)}]"

    def extract_image_base64(self, file):
        """
        Convert image to base64 data URI for vision models.
        Returns a data:mime;base64,... string used later in build_messages()
        only when the active provider supports vision.
        """
        try:
            file.seek(0)

            # Check file size (5MB max)
            file.seek(0, 2)
            size = file.tell()
            file.seek(0)

            if size > 5_000_000:
                return "[ERROR: Image too large. Max 5MB]"

            # Get image dimensions
            img = Image.open(file)
            width, height = img.size

            # Estimate token cost
            baseline_pixels = 512 * 512
            baseline_tokens = 1610
            actual_pixels = width * height
            estimated_tokens = int((actual_pixels / baseline_pixels) * baseline_tokens)

            if estimated_tokens > 60_000:
                return f"[ERROR: Image resolution too high ({width}x{height}). Please resize.]"

            # Reset file pointer and encode to base64
            file.seek(0)
            image_data = base64.b64encode(file.read()).decode('utf-8')

            mime_type = mimetypes.guess_type(file.filename)[0] or 'image/jpeg'

            # Return as data URI — used directly as the image_url value
            return f"data:{mime_type};base64,{image_data}"

        except Exception as e:
            logger.error(f"Image processing error: {str(e)}")
            return f"[ERROR processing image: {str(e)}]"

    def estimate_tokens(self, text):
        """Estimate token count (4 chars ≈ 1 token)"""
        if not text or text.startswith("[ERROR"):
            return 0
        return len(text) // 4


# ===========================================================
# HELPER FUNCTIONS
# ===========================================================


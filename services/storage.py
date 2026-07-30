import requests
import os

import cloudinary
import mimetypes
import cloudinary.uploader

API_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME")
API_KEY = os.environ.get("CLOUDINARY_API_KEY")
API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")

cloudinary.config(
    cloud_name=API_NAME,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# Your Supabase details


# File to upload (local path on your phone)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SERVICE_ROLE_KEY = os.environ.get("SERVICE_ROLE_KEY")
BUCKET = "Study Hub"
headers = {
    "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
    "Content-Type": "application/octet-stream"
}

import secrets
import datetime
import time
from email.utils import parsedate_to_datetime
from werkzeug.utils import secure_filename

# ─────────────────────────────────────────────────────────────────────────
# CLOCK-DRIFT COMPENSATION
#
# Cloudinary signs every upload with a `timestamp` and rejects the request
# as "stale" if that timestamp is more than ~1 hour off from Cloudinary's
# own clock. If the host machine's system clock is wrong (common on some
# containers/VMs without reliable NTP sync), every signed upload fails
# this way even though nothing is actually wrong with the request.
#
# To make uploads resilient to that, we measure the offset between the
# local clock and a trusted external clock (via the Date header on a
# normal HTTPS response) and use that corrected time when signing
# Cloudinary requests. The offset is cached briefly so we don't add a
# network round-trip to every single upload.
# ─────────────────────────────────────────────────────────────────────────
_time_offset_seconds = 0.0
_time_offset_checked_at = 0.0
_TIME_OFFSET_TTL = 300  # re-check every 5 minutes


def _refresh_time_offset():
    """Update the cached offset between local clock and real-world UTC time."""
    global _time_offset_seconds, _time_offset_checked_at

    local_now = time.time()
    try:
        resp = requests.head("https://www.google.com", timeout=3)
        server_dt = parsedate_to_datetime(resp.headers["Date"])
        server_epoch = server_dt.timestamp()
        _time_offset_seconds = server_epoch - local_now
    except Exception:
        # If we can't reach a reference clock, fall back to trusting the
        # local clock (offset 0) rather than blocking the upload entirely.
        _time_offset_seconds = 0.0
    finally:
        _time_offset_checked_at = local_now


def get_corrected_timestamp():
    """Return the current Unix timestamp, corrected for local clock drift."""
    now = time.time()
    if (now - _time_offset_checked_at) > _TIME_OFFSET_TTL:
        _refresh_time_offset()
    return int(time.time() + _time_offset_seconds)

class SupabaseStorage:
    def __init__(self):
        self.bucket = BUCKET
        self.service_role_key = SERVICE_ROLE_KEY
        self.supabase_url = SUPABASE_URL
        

    def upload_file(self, file, folder, filename, content_type="application/octet-stream"):
        """
        Upload file to Supabase storage via HTTP request.

        Args:
            file: Flask FileStorage object
            folder: Subfolder path inside bucket
            filename: Name to save in Supabase
            content_type: MIME type
        """

        # Ensure safe filename
        filename = secure_filename(filename)
        path = f"{folder}/{filename}"

        url = f"{self.supabase_url}/storage/v1/object/{self.bucket}/{path}"

        headers = {
            "Authorization": f"Bearer {self.service_role_key}",
            "apikey": self.service_role_key,
            "Content-Type": content_type
        }

        file.seek(0)
        response = requests.post(url, headers=headers, data=file.read())

        if response.status_code >= 400:
            return {
                "success": False,
                "url": None,
                "error": response.text
            }

        # Construct public URL
        public_url = f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{path}"

        return {
            "success": True,
            "url": public_url,
            "path": path,
            "error": None
        }
        
class FilenameService:
    """Generate secure, unique filenames for uploads"""
    
    @staticmethod
    def _get_extension(original_filename):
        """Extract and validate file extension"""
        
        ext = original_filename.rsplit('.', 1)[1].lower()
        return ext
    import mimetypes
    @staticmethod
    def get_file_category(filename: str) -> str:
        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type is None:
            return "document"  # fallback
        main = mime_type.split("/")[0]
        if main == "image":
            return "image"
        if main == "video":
            return "video"
        return "document"
    
    @staticmethod
    def _generate_token(length=8):
        """Generate random hex token"""
        return secrets.token_hex(length)
    
    @staticmethod
    def _get_timestamp():
        """Get current timestamp in YYYYMMDD format"""
        return datetime.datetime.utcnow().strftime('%Y%m%d')
    
    @staticmethod
    def _get_date_path():
        """Get date-based folder path (YYYY/MM)"""
        now = datetime.datetime.utcnow()
        return f"{now.year}/{now.month:02d}"
    
        
    # ========================================
    # AVATAR FILENAMES
    # ========================================
    
    @staticmethod
    def generate_avatar_filename(user_id, original_filename):
        """
        Generate avatar filename
        Format: user_{id}_{timestamp}_{token}.{ext}
        Example: user_123_20240101_a3f5c9.jpg
        """
        ext = FilenameService._get_extension(original_filename)
        timestamp = FilenameService._get_timestamp()
        token = FilenameService._generate_token(6)
        
        return f"user_{user_id}_{timestamp}_{token}.{ext}"
    
    @staticmethod
    def get_avatar_path(user_id, original_filename):
        """
        Get full Cloudinary path for avatar
        Returns: (folder, filename)
        Example: ("studyhub/avatars", "user_123_20240101_a3f5c9.jpg")
        """
        filename = FilenameService.generate_avatar_filename(user_id, original_filename)
        return "avatars", filename
    
    # ========================================
    # POST FILENAMES
    # ========================================
    
    @staticmethod
    def generate_post_filename(user_id, original_filename, file_type="image"):
        """
        Generate post file filename
        Format: post_{id}_{timestamp}_{token}.{ext}
        Example: post_456_20240101_f3a5c9b2.jpg
        """
        ext = FilenameService._get_extension(original_filename)
        timestamp = FilenameService._get_timestamp()
        token = FilenameService._generate_token(8)
        
        return f"post_{user_id}_{timestamp}_{token}.{ext}"
    
    @staticmethod
    def get_post_file_path(user_id, original_filename, file_type="image"):
        """
        Get full path for post file with date-based folders
        Returns: (folder, filename)
        Example: ("studyhub/posts/images/2024/01", "post_456_20240101_f3a5c9b2.jpg")
        """
        date_path = FilenameService._get_date_path()
        filename = FilenameService.generate_post_filename(user_id, original_filename, file_type)
        folder = f"posts/{file_type}s/{date_path}"
        
        return folder, filename
    
    # ========================================
    # MESSAGE FILENAMES
    # ========================================
    
    @staticmethod
    def generate_message_filename(message_id, original_filename):
        """
        Generate message attachment filename
        Format: msg_{id}_{token}.{ext}
        Example: msg_789_a3f5c9b2.jpg
        """
        ext = FilenameService._get_extension(original_filename)
        token = FilenameService._generate_token(8)
        
        return f"msg_{message_id}_{token}.{ext}"
        
    @staticmethod
    def generate_comment_filename(comment_id,original_filename):
        """
        Generate message attachment filename
        Format: msg_{id}_{token}.{ext}
        Example: msg_789_a3f5c9b2.jpg
        """
        ext = FilenameService._get_extension(original_filename)
        token = FilenameService._generate_token(8)
        
        return f"comment_{comment_id}_{token}.{ext}"
    
    
    @staticmethod
    def get_message_file_path(message_id, original_filename, file_type="image"):
        """
        Get full path for message attachment
        Returns: (folder, filename)
        Example: ("studyhub/messages/images", "msg_789_a3f5c9b2.jpg")
        """
        filename = FilenameService.generate_message_filename(message_id, original_filename)
        folder = f"messages/{file_type}s"
        
        return folder, filename
    @staticmethod
    def get_comment_file_path(comment_id, original_filename, file_type="image"):
        filename = FilenameService.generate_comment_filename(comment_id, original_filename)
        folder = f"posts/comments/{file_type}s"
        return folder, filename
    
    
    # ========================================
    # RESOURCE LIBRARY FILENAMES
    # ========================================
    
    @staticmethod
    def generate_resource_filename(resource_id, original_filename):
        """
        Generate resource library filename
        Format: resource_{id}_{timestamp}_{token}.{ext}
        Example: resource_12_20240101_9b1c4d.pdf
        """
        ext = FilenameService._get_extension(original_filename)
        timestamp = FilenameService._get_timestamp()
        token = FilenameService._generate_token(8)
        
        return f"resource_{resource_id}_{timestamp}_{token}.{ext}"
    
    @staticmethod
    def get_resource_file_path(resource_id, original_filename, file_type="document"):
        """
        Get full path for resource file
        Returns: (folder, filename)
        Example: ("studyhub/resources/documents/2024/01", "resource_12_20240101_9b1c4d.pdf")
        """
        date_path = FilenameService._get_date_path()
        filename = FilenameService.generate_resource_filename(resource_id, original_filename)
        folder = f"resources/{file_type}s/{date_path}"
        
        return folder, filename
    
    # ========================================
    # AI ASSISTANT TEMP FILES
    # ========================================
    
    @staticmethod
    def generate_ai_temp_filename(user_id, original_filename):
        """
        Generate temporary filename for AI uploads
        Format: ai_temp_{user_id}_{token}.{ext}
        """
        ext = FilenameService._get_extension(original_filename)
        token = FilenameService._generate_token(8)
        return f"ai_temp_{user_id}_{token}.{ext}"
    
    @staticmethod
    def get_ai_temp_path(user_id, original_filename):
        """
        Get path for temporary AI uploads
        Returns: (bucket, path, filename)
        """
        filename = FilenameService.generate_ai_temp_filename(user_id, original_filename)
        path = f"ai-uploads/user_{user_id}/{filename}"
        return BUCKET, path, filename

class CloudinaryStorage:
    def __init__(self):
        self.api_name = API_NAME
        self.api_key = API_KEY
        self.api_secret = API_SECRET
    
    def upload_file(self, file, folder, filename, resource_type="auto"):
        """
        Upload file to Cloudinary
        
        Args:
            file: File object or file path
            folder: Cloudinary folder path
            filename: Filename with extension
            resource_type: "image", "video", "raw", or "auto"
        
        Returns:
            dict: {"success": bool, "url": str, "error": str}
        """
        name, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
        
        try:
            result = cloudinary.uploader.upload(
                file,
                folder=folder,
                public_id=name,
                resource_type=resource_type,
                invalidate=True,
                timestamp=get_corrected_timestamp()
            )
            
            if result and "secure_url" in result:
                return {
                    "success": True,
                    "url": result["secure_url"],
                    "public_id": result.get("public_id"),
                    "error": None
                }
            else:
                return {
                    "success": False,
                    "url": None,
                    "error": "Secure URL missing in response"
                }
        except Exception as e:
            return {
                "success": False,
                "url": None,
                "error": str(e)
            }
    
    def delete_file(self, public_id, resource_type="image"):
        """Delete file from Cloudinary"""
        try:
            result = cloudinary.uploader.destroy(
                public_id,
                resource_type=resource_type,
                timestamp=get_corrected_timestamp()
            )
            return {"success": result.get("result") == "ok", "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    
    def upload_ai_file(self, file, user_id):
        """
        Upload file for AI conversation (images and documents) to Cloudinary.

        Args:
            file: Flask FileStorage object
            user_id: User ID

        Returns:
            dict: {"success": bool, "metadata": dict|None, "error": str|None}
        """
        try:
            filename = secure_filename(file.filename)

            # Generate unique filename via FilenameService
            _bucket, _path, generated_filename = FilenameService.get_ai_temp_path(
                user_id, filename
            )

            # Read file data
            file.seek(0)
            file_data = file.read()
            file_size = len(file_data)

            import mimetypes as _mimetypes
            mime_type = _mimetypes.guess_type(filename)[0] or 'application/octet-stream'

            # Map MIME type to Cloudinary resource_type
            if mime_type.startswith('image/'):
                resource_type = 'image'
            elif mime_type.startswith('video/'):
                resource_type = 'video'
            else:
                resource_type = 'raw'

            folder_path = f"ai-uploads/user_{user_id}"

            # upload_file(file, folder, filename, resource_type)
            result = self.upload_file(file_data, folder_path, generated_filename, resource_type)

            if result["success"]:
                return {
                    "success": True,
                    "metadata": {
                        "filename": filename,
                        "url": result["url"],
                        "public_id": result.get("public_id"),
                        "size": file_size,
                        "mime_type": mime_type,
                    },
                    "error": None
                }
            else:
                return {
                    "success": False,
                    "metadata": None,
                    "error": result["error"]
                }

        except Exception as e:
            return {
                "success": False,
                "metadata": None,
                "error": str(e)
            }
    
    def cleanup_temp_files(self, user_id, older_than_hours=24):
        """
        Clean up temporary AI upload files older than specified hours
        (Optional - implement if needed)
        """
        # Implementation for cleaning old temp files
        # You can call this periodically via a cron job
        pass

supabase_storage = SupabaseStorage()
cloudinary_storage = CloudinaryStorage()
filename_service = FilenameService()
    




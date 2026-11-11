from typing import Any, Dict, List

import requests

from config.settings import CLOUDINARY_CLOUD_NAME, CLOUDINARY_UPLOAD_PRESET


class ImageUploadError(RuntimeError):
    pass


def _upload_payload(image_file, rotation: int = 0):
    """(filename, bytes, mime) for an upload. Scanned images correct their rotation first."""
    if hasattr(image_file, "upload_payload"):
        return image_file.upload_payload(rotation)

    return (
        image_file.name,
        image_file.getvalue(),
        image_file.type or "image/jpeg",
    )


def upload_image_to_cloudinary(
    uploaded_file, folder: str = "ebay-card-batch-lister", rotation: int = 0
) -> str:
    """Upload a scan or Streamlit UploadedFile to Cloudinary and return the secure URL."""
    if not CLOUDINARY_CLOUD_NAME or not CLOUDINARY_UPLOAD_PRESET:
        raise ImageUploadError(
            "Missing Cloudinary settings. Add CLOUDINARY_CLOUD_NAME and "
            "CLOUDINARY_UPLOAD_PRESET to your .env file."
        )

    url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"

    filename, file_bytes, mime_type = _upload_payload(uploaded_file, rotation)

    files = {"file": (filename, file_bytes, mime_type)}

    data = {
        "upload_preset": CLOUDINARY_UPLOAD_PRESET,
        "folder": folder,
    }

    response = requests.post(url, files=files, data=data, timeout=60)

    if response.status_code >= 400:
        raise ImageUploadError(
            f"Cloudinary upload failed: {response.status_code} {response.text}"
        )

    result: Dict[str, Any] = response.json()
    secure_url = result.get("secure_url")

    if not secure_url:
        raise ImageUploadError(f"Cloudinary upload succeeded but no secure_url was returned: {result}")

    return secure_url


def upload_card_images(
    front_file, back_file=None, front_rotation: int = 0, back_rotation: int = 0
) -> List[str]:
    """Upload the card's scans. The back is optional for single-sided cards."""
    urls = [upload_image_to_cloudinary(front_file, rotation=front_rotation)]

    if back_file is not None:
        urls.append(upload_image_to_cloudinary(back_file, rotation=back_rotation))

    return urls

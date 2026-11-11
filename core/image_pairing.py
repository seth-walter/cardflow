import base64
from collections import defaultdict
from typing import Any, Dict, List


def uploaded_file_to_data_url(uploaded_file) -> str:
    mime_type = uploaded_file.type or "image/jpeg"
    file_bytes = uploaded_file.getvalue()
    b64 = base64.b64encode(file_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def pair_uploaded_images(files) -> List[Dict[str, Any]]:
    grouped = defaultdict(dict)

    for f in files:
        name_lower = f.name.lower()
        if "_front" in name_lower:
            base = name_lower.split("_front")[0]
            grouped[base]["front"] = f
        elif "_back" in name_lower:
            base = name_lower.split("_back")[0]
            grouped[base]["back"] = f

    pairs = []
    for base, parts in grouped.items():
        if "front" in parts and "back" in parts:
            pairs.append({"base_name": base, "front": parts["front"], "back": parts["back"]})

    return sorted(pairs, key=lambda x: x["base_name"])

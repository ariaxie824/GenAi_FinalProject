"""
LLM structured output logic using Claude Haiku vision.
Extracts location, date, scene description, tags, and transport emoji from photos.
"""

import base64
import json
import re
from pathlib import Path
from datetime import datetime

import anthropic
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    location_name: str = Field(description="Specific place name: city, landmark, or region")
    date: str | None = Field(default=None, description="Date in YYYY-MM-DD format if determinable")
    scene_description: str = Field(description="Vivid 1-2 sentence description of the scene")
    tags: list[str] = Field(description="3-5 descriptive tags about the place or atmosphere")
    transport_emoji: str = Field(description="Single emoji for likely transport mode: ✈️🚂🚗🚢🚌🚲🚶")


EXTRACT_TOOL = {
    "name": "extract_memory",
    "description": "Extract structured travel memory information from photos",
    "input_schema": {
        "type": "object",
        "properties": {
            "location_name": {
                "type": "string",
                "description": "Specific place name: city, landmark, or region visible in the photo"
            },
            "date": {
                "type": ["string", "null"],
                "description": "Date in YYYY-MM-DD format if visible or inferrable, otherwise null"
            },
            "scene_description": {
                "type": "string",
                "description": "Vivid 1-2 sentence description capturing atmosphere and key visual elements"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-5 lowercase tags (e.g. 'beach', 'mountain', 'city', 'night', 'food')"
            },
            "transport_emoji": {
                "type": "string",
                "description": "One emoji representing how the traveler likely ARRIVED at this destination (not objects visible in the scene): ✈️ 🚂 🚗 🚢 🚌 🚲 🚶"
            }
        },
        "required": ["location_name", "scene_description", "tags", "transport_emoji"]
    }
}

SYSTEM_PROMPT = """You are a travel memory analyst. Analyze travel photos and extract structured information.
When identifying locations, be as specific as possible using visual cues: architecture, signage, landscapes, landmarks.
For transport_emoji, infer how the traveler ARRIVED at the destination — not what is visible in the scene.
  - Boats or ships in the background do NOT mean 🚢 unless it is clearly a cruise or ferry trip.
  - A beach or island destination most likely means ✈️ (flew there) unless there is a port or ferry context.
  - Airports, plane windows, or boarding passes suggest ✈️.
  - Train stations or rail scenery suggest 🚂.
  - Scenic mountain roads or parked cars suggest 🚗.
  - If genuinely unclear, default to ✈️ for international destinations, 🚗 for domestic/local ones."""


def extract_exif(image_path: str) -> dict:
    """Extract GPS coordinates and datetime from image EXIF data."""
    result = {"gps": None, "datetime": None}
    try:
        img = Image.open(image_path)
        exif_raw = img._getexif()
        if not exif_raw:
            return result

        exif = {TAGS.get(k, k): v for k, v in exif_raw.items()}

        # Extract datetime
        for dt_tag in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
            if dt_tag in exif:
                try:
                    dt = datetime.strptime(exif[dt_tag], "%Y:%m:%d %H:%M:%S")
                    result["datetime"] = dt.strftime("%Y-%m-%d")
                    break
                except ValueError:
                    pass

        # Extract GPS
        gps_raw = exif.get("GPSInfo")
        if gps_raw:
            gps = {GPSTAGS.get(k, k): v for k, v in gps_raw.items()}
            lat = _dms_to_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef", "N"))
            lon = _dms_to_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef", "E"))
            if lat is not None and lon is not None:
                result["gps"] = {"lat": lat, "lon": lon}
    except Exception:
        pass
    return result


def _dms_to_decimal(dms, ref: str) -> float | None:
    if not dms or len(dms) < 3:
        return None
    try:
        d, m, s = [float(x) for x in dms]
        decimal = d + m / 60 + s / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 7)
    except (TypeError, ValueError):
        return None


def _encode_image(image_path: str) -> tuple[str, str]:
    """Return (base64_data, media_type) for an image file."""
    path = Path(image_path)
    suffix = path.suffix.lower()
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_type_map.get(suffix, "image/jpeg")
    with open(image_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode(), media_type


def analyze_images(image_paths: list[str], exif_data: dict | None = None) -> MemoryEntry:
    """
    Send images to Claude Haiku and return a structured MemoryEntry.
    Uses tool use for reliable JSON extraction and prompt caching for the system prompt.
    """
    client = anthropic.Anthropic()

    content: list[dict] = []
    for path in image_paths:
        b64, media_type = _encode_image(path)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64}
        })

    exif_context = ""
    if exif_data:
        if exif_data.get("datetime"):
            exif_context += f"\nEXIF datetime: {exif_data['datetime']}"
        if exif_data.get("gps"):
            g = exif_data["gps"]
            exif_context += f"\nEXIF GPS: lat={g['lat']}, lon={g['lon']}"

    content.append({
        "type": "text",
        "text": f"Analyze these travel photos and call extract_memory with the information you find.{exif_context}"
    })

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"}
            }
        ],
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_memory"},
        messages=[{"role": "user", "content": content}]
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_memory":
            data = block.input
            if exif_data and exif_data.get("datetime") and not data.get("date"):
                data["date"] = exif_data["datetime"]
            return MemoryEntry(**data)

    raise ValueError("LLM did not return extract_memory tool call")

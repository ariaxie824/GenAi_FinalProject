"""
Folium map generation with rich popups and chronological route visualization.
Routes are drawn as quadratic Bezier arcs rather than straight lines.
"""

import base64
import math
from pathlib import Path

import folium
from folium.plugins import AntPath, MarkerCluster


TRANSPORT_COLORS = {
    "✈️": "blue",
    "🚂": "red",
    "🚗": "green",
    "🚢": "darkblue",
    "🚌": "orange",
    "🚲": "lightgreen",
    "🚶": "gray",
}

DEFAULT_COLOR = "purple"


def _arc_points(lat1: float, lon1: float, lat2: float, lon2: float, n: int = 50) -> list:
    """
    Quadratic Bezier arc between two map coordinates.
    Control point is offset perpendicular (left of travel direction) so the
    arc always curves the same way regardless of travel heading.
    Bulge scales with distance but is capped so transcontinental arcs stay readable.
    """
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    dist = math.hypot(dlat, dlon)
    if dist < 1e-9:
        return [[lat1, lon1], [lat2, lon2]]

    bulge = min(dist * 0.22, 12.0)  # degrees of perpendicular offset, capped at 12

    # CCW perpendicular to travel direction (curves left of direction)
    ctrl_lat = (lat1 + lat2) / 2 - (dlon / dist) * bulge
    ctrl_lon = (lon1 + lon2) / 2 + (dlat / dist) * bulge

    return [
        [
            (1 - t) ** 2 * lat1 + 2 * (1 - t) * t * ctrl_lat + t ** 2 * lat2,
            (1 - t) ** 2 * lon1 + 2 * (1 - t) * t * ctrl_lon + t ** 2 * lon2,
        ]
        for t in (i / n for i in range(n + 1))
    ]


def _thumbnail_b64(image_path: str, max_size: int = 200) -> str | None:
    try:
        from PIL import Image
        img = Image.open(image_path)
        img.thumbnail((max_size, max_size))
        from io import BytesIO
        buf = BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=75)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _build_popup_html(entry: dict, seq: int | None = None) -> str:
    tags_html = " ".join(
        f'<span style="background:#e8f4f8;border-radius:4px;padding:2px 6px;font-size:11px;">{t}</span>'
        for t in entry.get("tags", [])
    )

    img_html = ""
    for img_path in entry.get("image_paths", [])[:1]:
        b64 = _thumbnail_b64(img_path)
        if b64:
            img_html = (
                f'<img src="data:image/jpeg;base64,{b64}" '
                f'style="width:100%;border-radius:6px;margin-bottom:8px;"/>'
            )
            break

    date_str = entry.get("date") or ""
    date_html = (
        f'<div style="color:#888;font-size:12px;margin-bottom:4px;">{date_str}</div>'
        if date_str else ""
    )
    seq_html = (
        f'<div style="color:#aaa;font-size:11px;margin-bottom:2px;">Stop #{seq}</div>'
        if seq is not None else ""
    )

    return f"""
<div style="width:220px;font-family:sans-serif;">
  {img_html}
  {seq_html}
  <div style="font-size:15px;font-weight:bold;margin-bottom:4px;">
    {entry.get('transport_emoji', '')} {entry.get('location_name', '')}
  </div>
  {date_html}
  <div style="font-size:13px;color:#333;margin-bottom:8px;line-height:1.4;">
    {entry.get('scene_description', '')}
  </div>
  <div style="display:flex;flex-wrap:wrap;gap:4px;">{tags_html}</div>
</div>
""".strip()


def create_map(entries: list[dict]) -> folium.Map:
    """
    Build a Folium map with:
    - One marker per entry (color-coded by transport mode)
    - An animated AntPath route connecting dated entries in chronological order
    """
    valid = [e for e in entries if e.get("lat") is not None and e.get("lon") is not None]

    if valid:
        center_lat = sum(e["lat"] for e in valid) / len(valid)
        center_lon = sum(e["lon"] for e in valid) / len(valid)
        zoom = 4 if len(valid) > 1 else 10
    else:
        center_lat, center_lon, zoom = 20, 0, 2

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles="CartoDB positron",
    )

    # ── chronological route (dated entries only) ──────────────────────────────
    dated = sorted(
        [e for e in valid if e.get("date")],
        key=lambda e: e["date"],
    )

    if len(dated) >= 2:
        arc_coords = []
        for i in range(len(dated) - 1):
            e1, e2 = dated[i], dated[i + 1]
            segment = _arc_points(e1["lat"], e1["lon"], e2["lat"], e2["lon"])
            if arc_coords:
                arc_coords.extend(segment[1:])  # skip duplicate junction point
            else:
                arc_coords.extend(segment)

        AntPath(
            locations=arc_coords,
            color="#4a90d9",
            weight=3,
            opacity=0.8,
            delay=600,
            dash_array=[10, 20],
        ).add_to(m)

    # sequence number per dated entry (1-indexed)
    seq_map = {e["id"]: i + 1 for i, e in enumerate(dated)}

    # ── markers ───────────────────────────────────────────────────────────────
    cluster = MarkerCluster(name="Memories").add_to(m)

    for entry in valid:
        seq = seq_map.get(entry["id"])
        color = TRANSPORT_COLORS.get(entry.get("transport_emoji", ""), DEFAULT_COLOR)

        seq_label = f"#{seq} " if seq is not None else ""
        tooltip = f"{seq_label}{entry.get('transport_emoji','')} {entry.get('location_name','')}"
        if entry.get("date"):
            tooltip += f"  ·  {entry['date']}"

        folium.Marker(
            location=[entry["lat"], entry["lon"]],
            popup=folium.Popup(_build_popup_html(entry, seq=seq), max_width=240),
            tooltip=tooltip,
            icon=folium.Icon(color=color, icon="camera", prefix="fa"),
        ).add_to(cluster)

    if valid:
        folium.LayerControl().add_to(m)

    return m

"""
Memoir Map — Streamlit app.
Upload 1-2 travel photos → AI extracts location + scene info → pins to an interactive map.
"""

import json
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

import streamlit as st
from streamlit_folium import st_folium

from llm_processor import analyze_images, extract_exif
from geocoder import coords_from_exif_or_geocode, geocode
from map_renderer import create_map

DATA_DIR = Path(__file__).parent / "data"
ENTRIES_PATH = DATA_DIR / "entries.json"
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ── data helpers ──────────────────────────────────────────────────────────────

def load_entries() -> list[dict]:
    if ENTRIES_PATH.exists():
        with open(ENTRIES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_entries(entries: list[dict]) -> None:
    with open(ENTRIES_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def save_upload(uploaded_file) -> str:
    suffix = Path(uploaded_file.name).suffix
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}{suffix}"
    with open(dest, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return str(dest)


def _append_entry(entry: dict) -> None:
    entries = load_entries()
    entries.append(entry)
    save_entries(entries)


def _update_entry(entry_id: str, location_name: str, date: str | None,
                  description: str, coords) -> None:
    entries = load_entries()
    for e in entries:
        if e["id"] == entry_id:
            e["location_name"] = location_name
            e["date"] = date or None
            e["scene_description"] = description
            if coords is not None:
                e["lat"] = coords[0]
                e["lon"] = coords[1]
            break
    save_entries(entries)


def _delete_entry(entry_id: str) -> None:
    entries = [e for e in load_entries() if e["id"] != entry_id]
    save_entries(entries)


def _save_and_clear_pending(location_name: str, date: str | None,
                             description: str, coords) -> None:
    p = st.session_state["pending_entry"]
    ed = p["entry_data"]
    entry = {
        "id": uuid.uuid4().hex,
        "location_name": location_name,
        "date": date or ed.date,
        "scene_description": description,
        "tags": ed.tags,
        "transport_emoji": ed.transport_emoji,
        "lat": coords[0] if coords else None,
        "lon": coords[1] if coords else None,
        "image_paths": p["saved_paths"],
    }
    _append_entry(entry)
    del st.session_state["pending_entry"]


# ── page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Memoir Map", page_icon="🗺️", layout="wide")
st.title("🗺️ Memoir Map")
st.caption("Upload travel photos → AI reads the scene → pins memories on a map")

# ── sidebar: upload & submit ──────────────────────────────────────────────────

with st.sidebar:
    st.header("Add a Memory")
    uploaded = st.file_uploader(
        "Upload 1–2 photos",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        help="JPEG photos with GPS EXIF data will be placed precisely; others use AI geocoding.",
    )

    if uploaded and len(uploaded) > 2:
        st.warning("Please upload at most 2 photos at a time.")
        uploaded = uploaded[:2]

    if uploaded:
        cols = st.columns(len(uploaded))
        for i, f in enumerate(uploaded):
            cols[i].image(f, use_container_width=True, caption=f.name)

    submit = st.button(
        "✨ Analyze & Pin",
        disabled=not uploaded,
        type="primary",
        use_container_width=True,
    )

    st.divider()
    st.caption("Powered by Claude Haiku · Nominatim · Folium")

# ── process on submit ─────────────────────────────────────────────────────────

if submit and uploaded:
    st.session_state.pop("last_entry", None)
    st.session_state.pop("editing_entry_id", None)
    st.session_state.pop("confirm_delete_id", None)

    with st.spinner("Saving photos…"):
        saved_paths = [save_upload(f) for f in uploaded]

    with st.spinner("Extracting EXIF metadata…"):
        exif = extract_exif(saved_paths[0])

    with st.spinner("Claude Haiku is reading your photos…"):
        try:
            entry_data = analyze_images(saved_paths, exif)
        except Exception as e:
            st.error(f"LLM error: {e}")
            st.stop()

    with st.spinner(f"Locating '{entry_data.location_name}'…"):
        coords = coords_from_exif_or_geocode(exif, entry_data.location_name)

    if coords is not None:
        new_entry = {
            "id": uuid.uuid4().hex,
            "location_name": entry_data.location_name,
            "date": entry_data.date,
            "scene_description": entry_data.scene_description,
            "tags": entry_data.tags,
            "transport_emoji": entry_data.transport_emoji,
            "lat": coords[0],
            "lon": coords[1],
            "image_paths": saved_paths,
        }
        _append_entry(new_entry)
        st.session_state["last_entry"] = new_entry

        st.success(
            f"{entry_data.transport_emoji} **{entry_data.location_name}** pinned!"
            + (f"  ({entry_data.date})" if entry_data.date else "")
        )
        with st.expander("AI extracted this", expanded=True):
            st.write(f"**Scene:** {entry_data.scene_description}")
            st.write(f"**Tags:** {', '.join(entry_data.tags)}")
            src = "EXIF GPS" if (exif and exif.get("gps")) else "Nominatim"
            st.write(f"**Coordinates ({src}):** {coords[0]:.4f}, {coords[1]:.4f}")
    else:
        st.session_state["pending_entry"] = {
            "entry_data": entry_data,
            "saved_paths": saved_paths,
        }

# ── geocoding failure: correction before saving ───────────────────────────────

if "pending_entry" in st.session_state:
    ed = st.session_state["pending_entry"]["entry_data"]

    st.warning(
        f"Could not place **{ed.location_name}** on the map automatically. "
        "Correct the details below and retry, or save without a map pin."
    )

    with st.expander("✏️ Edit before saving", expanded=True):
        p_loc = st.text_input("Location name", value=ed.location_name, key="p_loc",
                               help="Try a city, landmark, or neighborhood")
        p_date = st.text_input("Date (YYYY-MM-DD)", value=ed.date or "", key="p_date",
                                placeholder="e.g. 2024-06-15")
        p_desc = st.text_area("Scene description", value=ed.scene_description,
                               key="p_desc", height=100)

        col1, col2 = st.columns(2)
        retry_btn = col1.button("📍 Retry geocoding", type="primary", use_container_width=True)
        save_btn = col2.button("💾 Save without pin", use_container_width=True)

        if retry_btn:
            with st.spinner(f"Locating '{p_loc}'…"):
                coords = geocode(p_loc)
            if coords:
                st.success(f"Found: {coords[0]:.4f}, {coords[1]:.4f}")
                _save_and_clear_pending(p_loc, p_date, p_desc, coords)
                st.rerun()
            else:
                st.error(f"Still couldn't find **{p_loc}**. Try a shorter or more common name.")

        if save_btn:
            _save_and_clear_pending(p_loc, p_date, p_desc, coords=None)
            st.rerun()

# ── correction form for successfully pinned entries ───────────────────────────

if "last_entry" in st.session_state:
    last = st.session_state["last_entry"]
    with st.expander("✏️ Something wrong? Correct this entry", expanded=False):
        fix_loc = st.text_input("Location name", value=last["location_name"], key="fix_loc")
        fix_date = st.text_input("Date (YYYY-MM-DD)", value=last.get("date") or "",
                                  key="fix_date", placeholder="e.g. 2024-06-15")
        fix_desc = st.text_area("Scene description", value=last["scene_description"],
                                 key="fix_desc", height=100)

        col1, col2 = st.columns(2)
        update_btn = col1.button("📍 Update & re-geocode", use_container_width=True)
        dismiss_btn = col2.button("✓ Looks good", use_container_width=True)

        if update_btn:
            with st.spinner(f"Locating '{fix_loc}'…"):
                new_coords = geocode(fix_loc)
            if new_coords is None:
                st.error(f"Couldn't find **{fix_loc}**. Location name and description updated; map pin unchanged.")
            _update_entry(last["id"], fix_loc, fix_date, fix_desc, new_coords)
            del st.session_state["last_entry"]
            st.rerun()

        if dismiss_btn:
            del st.session_state["last_entry"]
            st.rerun()

# ── edit existing entry ───────────────────────────────────────────────────────

if "editing_entry_id" in st.session_state:
    eid = st.session_state["editing_entry_id"]
    all_entries = load_entries()
    target = next((e for e in all_entries if e["id"] == eid), None)

    if target:
        st.subheader(f"✏️ Editing: {target['location_name']}")

        e_loc = st.text_input("Location name", value=target["location_name"], key="e_loc")
        e_date = st.text_input("Date (YYYY-MM-DD)", value=target.get("date") or "",
                                key="e_date", placeholder="e.g. 2024-06-15")
        e_desc = st.text_area("Scene description", value=target["scene_description"],
                               key="e_desc", height=100)

        col1, col2, col3 = st.columns(3)
        save_edit = col1.button("💾 Save", type="primary", use_container_width=True)
        regeo_edit = col2.button("📍 Save & re-geocode", use_container_width=True)
        cancel_edit = col3.button("✕ Cancel", use_container_width=True)

        if save_edit:
            _update_entry(eid, e_loc, e_date, e_desc, coords=None)
            del st.session_state["editing_entry_id"]
            st.rerun()

        if regeo_edit:
            with st.spinner(f"Locating '{e_loc}'…"):
                new_coords = geocode(e_loc)
            if new_coords is None:
                st.error(f"Couldn't find **{e_loc}**. Other fields saved; map pin unchanged.")
            _update_entry(eid, e_loc, e_date, e_desc, new_coords)
            del st.session_state["editing_entry_id"]
            st.rerun()

        if cancel_edit:
            del st.session_state["editing_entry_id"]
            st.rerun()

    st.divider()

# ── map + memory list ─────────────────────────────────────────────────────────

entries = load_entries()
folium_map = create_map(entries)

map_col, list_col = st.columns([3, 1])

with map_col:
    st_folium(folium_map, width="100%", height=520, returned_objects=[])

with list_col:
    st.subheader(f"Memories ({len(entries)})")
    if not entries:
        st.info("No memories yet — upload a photo to get started!")
    else:
        for e in reversed(entries):
            with st.container():
                st.markdown(
                    f"**{e.get('transport_emoji','')} {e.get('location_name','')}**  \n"
                    f"<small>{e.get('date','') or '—'}</small>",
                    unsafe_allow_html=True,
                )
                if e.get("tags"):
                    st.caption(" · ".join(e["tags"]))

                btn_col1, btn_col2 = st.columns(2)
                if btn_col1.button("✏️", key=f"edit_{e['id']}", help="Edit", use_container_width=True):
                    st.session_state["editing_entry_id"] = e["id"]
                    st.session_state.pop("last_entry", None)
                    st.rerun()

                if btn_col2.button("🗑️", key=f"del_{e['id']}", help="Delete", use_container_width=True):
                    st.session_state["confirm_delete_id"] = e["id"]
                    st.rerun()

                if st.session_state.get("confirm_delete_id") == e["id"]:
                    st.warning("Delete this memory?")
                    c1, c2 = st.columns(2)
                    if c1.button("Yes", key=f"yes_{e['id']}", use_container_width=True):
                        _delete_entry(e["id"])
                        st.session_state.pop("confirm_delete_id", None)
                        st.rerun()
                    if c2.button("No", key=f"no_{e['id']}", use_container_width=True):
                        st.session_state.pop("confirm_delete_id", None)
                        st.rerun()

                st.divider()

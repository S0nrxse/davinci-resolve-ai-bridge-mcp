"""Timeline notes: markers the user left for the AI to act on. Pure logic, standard library only.

A *note* is a marker the user made, with a name or note text. Markers the bridge made itself (pause, dead-air,
shot, beat, QC and review markers) are never notes. New bridge markers carry ``{"aibridge": true}`` in their
customData; older ones are recognized by the bridge's existing customData schemes and by their known names.

Resolve can only edit a marker's customData in place, so resolving a note stores its status in customData and
re-adds the marker with a new color and the AI's reply appended to the note text.
"""
import json
import re

TAG_KEY = "aibridge"            # bridge-made marker: {"aibridge": true, ...}
STATE_KEY = "aibridge_note"     # a user's note the AI resolved: {"aibridge_note": {...}, "user_custom_data": ...}
USER_DATA_KEY = "user_custom_data"
REPLY_SEPARATOR = "\n— AI: "
STATUSES = ("open", "done", "skipped")
STATUS_COLORS = {"done": "Green", "skipped": "Cream"}
FALLBACK_COLORS = {"Cream": "Sand"}
MARKER_COLORS = ("Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose", "Lavender",
                 "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream")

# Untagged markers made by earlier bridge versions: (name pattern, colors or None, note pattern or None).
_LEGACY_MARKERS = (
    (re.compile(r"^Pause \d+(\.\d+)?s$"), ("Purple",), None),
    (re.compile(r"^Dead air \d+(\.\d+)?s$"), ("Purple",), None),
    (re.compile(r"^QC: "), ("Red", "Yellow"), None),
    (re.compile(r"^Shot$"), ("Cyan",), re.compile(r"\(score \d+(\.\d+)?\)$")),
    (re.compile(r"^(Beat|Bar)$"), ("Green", "Red"), re.compile(r"^$")),
    (re.compile(r"^(Review silence$|Review cut: )"), None, None),
)
_LEGACY_NOTES = ("Would be removed by remove_pauses", "Source audio appears silent; review before cutting.",
                 "From the transcript. Audition before cutting.")
_LEGACY_CUSTOM = "resolve-ai-bridge"


def _json_dict(text):
    try:
        value = json.loads(text) if isinstance(text, str) and text.lstrip().startswith("{") else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def bridge_tag(custom_data=""):
    """customData for a marker the bridge makes: keeps the caller's data, adds the bridge tag."""
    text = "" if custom_data is None else str(custom_data)
    data = _json_dict(text)
    if data is not None:
        if data.get(TAG_KEY) is True:
            return text
        data[TAG_KEY] = True
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if text.startswith(_LEGACY_CUSTOM):
        return text
    if not text:
        return json.dumps({TAG_KEY: True}, separators=(",", ":"))
    return json.dumps({TAG_KEY: True, "data": text}, ensure_ascii=False, separators=(",", ":"))


def is_bridge_marker(marker):
    """True for markers the bridge made, tagged or legacy. A resolved user note is never a bridge marker."""
    custom = str(marker.get("customData") or "")
    data = _json_dict(custom)
    if data is not None:
        if STATE_KEY in data:
            return False
        if data.get(TAG_KEY) is True or isinstance(data.get("bridge"), str):
            return True
    if custom.startswith(_LEGACY_CUSTOM):
        return True
    name, note, color = str(marker.get("name") or ""), str(marker.get("note") or ""), str(marker.get("color") or "")
    if any(note.startswith(text) for text in _LEGACY_NOTES):
        return True
    for name_rx, colors, note_rx in _LEGACY_MARKERS:
        if name_rx.search(name) and (colors is None or color in colors) and (note_rx is None or note_rx.search(note)):
            return True
    return False


def note_state(marker):
    """{status, reply, original_note, original_color, user_custom_data} for a marker (open when unresolved)."""
    custom = str(marker.get("customData") or "")
    data = _json_dict(custom)
    state = data.get(STATE_KEY) if data is not None else None
    if isinstance(state, dict):
        status = state.get("status") if state.get("status") in STATUSES else "open"
        note = state.get("original_note")
        if not isinstance(note, str):
            note = str(marker.get("note") or "").split(REPLY_SEPARATOR)[0]
        return {"status": status, "reply": str(state.get("reply") or ""), "original_note": note,
                "original_color": str(state.get("original_color") or marker.get("color") or "Blue"),
                "user_custom_data": str(data.get(USER_DATA_KEY) or "")}
    return {"status": "open", "reply": "", "original_note": str(marker.get("note") or ""),
            "original_color": str(marker.get("color") or "Blue"), "user_custom_data": custom}


def is_note(marker):
    """A user-made marker with a name or note text."""
    if is_bridge_marker(marker):
        return False
    return bool(str(marker.get("name") or "").strip() or note_state(marker)["original_note"].strip())


def _number(value):
    value = float(value)
    return int(value) if value.is_integer() else value


def absolute_frame(offset, timeline_start):
    """GetMarkers keys are frames from the timeline start; JSON transports stringify them."""
    return _number(float(timeline_start) + float(offset))


def timecode(frame, fps, drop_frame=False):
    """Absolute timeline frame -> timecode as Resolve shows it (";" separators on drop-frame timelines)."""
    rate = float(fps or 24)
    nominal = max(1, int(round(rate)))
    frame = max(0, int(round(float(frame))))
    if drop_frame and nominal in (30, 60):
        drop = 2 if nominal == 30 else 4
        per_10min = nominal * 600 - drop * 9
        per_min = nominal * 60 - drop
        tens, rest = divmod(frame, per_10min)
        if rest > drop:
            frame += drop * 9 * tens + drop * ((rest - drop) // per_min)
        else:
            frame += drop * 9 * tens
        ff, seconds = frame % nominal, frame // nominal
        return "%02d;%02d;%02d;%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60, ff)
    ff, seconds = frame % nominal, frame // nominal
    return "%02d:%02d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60, ff)


def is_drop_frame(setting):
    return str(setting).strip() in ("1", "True", "true")


def collect(markers, timeline_start, fps=24, drop_frame=False):
    """Every note on the timeline, sorted by absolute frame (without clips)."""
    notes = []
    for offset, marker in (markers or {}).items():
        if not isinstance(marker, dict) or not is_note(marker):
            continue
        try:
            frame = absolute_frame(offset, timeline_start)
        except (TypeError, ValueError):
            continue
        state = note_state(marker)
        try:
            duration = max(1, int(marker.get("duration") or 1))
        except (TypeError, ValueError):
            duration = 1
        notes.append({"frame": frame, "timecode": timecode(frame, fps, drop_frame), "duration": duration,
                      "end_frame": frame + duration, "name": str(marker.get("name") or ""),
                      "note": state["original_note"], "color": str(marker.get("color") or ""),
                      "status": state["status"], "reply": state["reply"], "clips": []})
    notes.sort(key=lambda n: n["frame"])
    return notes


def count_open(markers):
    """Open notes among GetMarkers() values; cheap enough for a 1 s poll."""
    return sum(1 for marker in (markers or {}).values()
               if isinstance(marker, dict) and is_note(marker) and note_state(marker)["status"] == "open")


def summary(notes, include_done=False):
    counts = {status: sum(1 for n in notes if n["status"] == status) for status in STATUSES}
    shown = notes if include_done else [n for n in notes if n["status"] == "open"]
    return {"notes": shown, "open": counts["open"], "done": counts["done"], "skipped": counts["skipped"]}


def covering(frame, count, bounds):
    """Index of the item whose [start, end) holds ``frame`` on one ordered, non-overlapping track, or None.

    ``bounds(i)`` returns (start, end) or None; callers cache it so each item is read at most once.
    """
    low, high = 0, count - 1
    while low <= high:
        middle = (low + high) // 2
        span = bounds(middle)
        if span is None:
            return None
        start, end = span
        if frame < start:
            high = middle - 1
        elif frame >= end:
            low = middle + 1
        else:
            return middle
    return None


def resolved_marker(marker, status, reply=""):
    """The marker to re-add for ``status``: same name and duration, new color, reply appended, state in customData."""
    if status not in STATUSES:
        raise ValueError("status must be done, skipped or open.")
    state = note_state(marker)
    reply = " ".join(str(reply or "").split())[:400]
    note = state["original_note"]
    if status == "open":
        color, custom = state["original_color"], state["user_custom_data"]
    else:
        color = STATUS_COLORS[status]
        if reply:
            note = note + REPLY_SEPARATOR + reply
        payload = {STATE_KEY: {"status": status, "reply": reply, "original_note": state["original_note"],
                               "original_color": state["original_color"]}}
        if state["user_custom_data"]:
            payload[USER_DATA_KEY] = state["user_custom_data"]
        custom = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        duration = max(1, int(marker.get("duration") or 1))
    except (TypeError, ValueError):
        duration = 1
    return {"color": color, "name": str(marker.get("name") or ""), "note": note, "duration": duration,
            "customData": custom}


def same_marker(expected, actual):
    """Whether the marker Resolve reports matches what was written (color, name, note, duration, customData)."""
    if not isinstance(actual, dict):
        return False
    try:
        same_duration = int(actual.get("duration") or 1) == int(expected.get("duration") or 1)
    except (TypeError, ValueError):
        same_duration = False
    return same_duration and all(str(actual.get(key) or "") == str(expected.get(key) or "")
                                 for key in ("color", "name", "note", "customData"))

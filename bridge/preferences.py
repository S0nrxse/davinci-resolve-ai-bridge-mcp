"""Per-project editor preferences kept in the bridge home (preferences.json), keyed by project name.

Standard library only; never imports Resolve, ``mcp`` or ``fusionscript``, never prints to stdout. The file holds
small, user-chosen defaults that tools read so the agent need not ask again: the pinned working timeline, the bin
for bridge-made timelines, tightening defaults and a loudness target.
"""

import json
import os
import tempfile
from pathlib import Path

FILE_NAME = "preferences.json"

# key -> (type check, description). Unknown keys are refused so a typo never silently does nothing.
SCHEMA = {
    "working_timeline_id": (str, "the timeline the user considers their working edit (unique id)"),
    "agent_bin": (str, 'bin for every bridge-made timeline ("" = leave where Resolve puts it)'),
    "tighten_lead_frames": (int, "air kept before the next word in tighten"),
    "tighten_tail_frames": (int, "air kept after the previous word in tighten"),
    "tighten_max_pause_frames": (int, "pauses longer than this are tightened"),
    "loudness_target_lufs": (float, "measure_loudness target"),
    "notes": (str, "free-form notes about how this user likes to work"),
}


class PreferenceError(ValueError):
    pass


def _path(home):
    return Path(home) / FILE_NAME


def load_all(home):
    try:
        data = json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get(home, project):
    entry = load_all(home).get(str(project or ""))
    return dict(entry) if isinstance(entry, dict) else {}


def validate(updates):
    if not isinstance(updates, dict) or not updates:
        raise PreferenceError("updates must be an object of preference: value (null removes it).")
    clean = {}
    for key, value in updates.items():
        if key not in SCHEMA:
            raise PreferenceError("Unknown preference %r; known: %s." % (key, ", ".join(sorted(SCHEMA))))
        kind = SCHEMA[key][0]
        if value is None:
            clean[key] = None
            continue
        if kind is float and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = float(value)
        if kind is int and isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, kind) or isinstance(value, bool):
            raise PreferenceError("%s must be %s, not %r." % (key, {int: "a whole number of frames", float: "a number",
                                                                      str: "text"}[kind], value))
        if kind is str and len(value) > 2000:
            raise PreferenceError("%s is too long." % key)
        clean[key] = value
    return clean


def set_values(home, project, updates):
    """Merge validated updates (None deletes) into the project's entry; atomic write. Returns the new entry."""
    clean = validate(updates)
    if not project:
        raise PreferenceError("No project is open; preferences are stored per project.")
    data = load_all(home)
    entry = dict(data.get(str(project)) or {})
    for key, value in clean.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    data[str(project)] = entry
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(home), prefix=".preferences-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=1, sort_keys=True)
        os.replace(temp, _path(home))
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return entry

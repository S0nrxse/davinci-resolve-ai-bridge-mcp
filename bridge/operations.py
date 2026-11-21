"""Resolve operation implementations shared by every transport.

This module is imported in two very different places:

* inside DaVinci Resolve, by ``ResolveConsole.py`` running in the Py3 Console
  or from the Workspace > Scripts menu
* outside DaVinci Resolve, by ``bridge/direct.py`` when the MCP server attaches
  to Resolve itself

Because of that it must stay dependent on the standard library only. Never
import ``mcp``, ``fusionscript``, or anything from ``bridge.server`` here.
"""

import json
import math
import os
import re
import time
import uuid
from pathlib import Path


AGENT_VERSION = "1.14.4"
PROTOCOL_VERSION = 2

IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp",
    ".exr", ".dpx", ".tga", ".psd", ".heic", ".heif", ".jp2", ".cin",
}

# Names accepted by set_clip_transform for "composite_mode", mapped to the
# integer constants documented in Resolve's scripting README.
COMPOSITE_MODES = {
    "normal": 0, "add": 1, "subtract": 2, "diff": 3, "difference": 3,
    "multiply": 4, "screen": 5, "overlay": 6, "hardlight": 7, "hard_light": 7,
    "softlight": 8, "soft_light": 8, "darken": 9, "lighten": 10,
    "color_dodge": 11, "colordodge": 11, "color_burn": 12, "colorburn": 12,
    "exclusion": 13, "hue": 14, "saturate": 15, "colorize": 16,
    "luma_mask": 17, "divide": 18, "linear_dodge": 19, "linear_burn": 20,
    "linear_light": 21, "vivid_light": 22, "pin_light": 23, "hard_mix": 24,
    "lighter_color": 25, "darker_color": 26, "foreground": 27, "alpha": 28,
    "inverted_alpha": 29, "lum": 30, "inverted_lum": 31,
}

# Scalar transform keys exposed by set_clip_transform, mapped to the Resolve
# property key. Values are passed through float().
TRANSFORM_FLOATS = {
    "pan": "Pan",
    "tilt": "Tilt",
    "zoom_x": "ZoomX",
    "zoom_y": "ZoomY",
    "rotation": "RotationAngle",
    "anchor_x": "AnchorPointX",
    "anchor_y": "AnchorPointY",
    "pitch": "Pitch",
    "yaw": "Yaw",
    "crop_left": "CropLeft",
    "crop_right": "CropRight",
    "crop_top": "CropTop",
    "crop_bottom": "CropBottom",
    "crop_softness": "CropSoftness",
    "opacity": "Opacity",
    "distortion": "Distortion",
}

TRANSFORM_BOOLS = {
    "flip_x": "FlipX",
    "flip_y": "FlipY",
    "zoom_gang": "ZoomGang",
    "crop_retain": "CropRetain",
}

# Enum transform keys exposed by set_clip_transform. Each maps friendly names to
# the integer constants documented in Resolve's scripting README (section
# "Looking up Timeline item properties"). Names are matched lowercased with
# spaces turned into underscores.
DYNAMIC_ZOOM_EASE = {
    "linear": 0, "ease_in": 1, "in": 1, "ease_out": 2, "out": 2,
    "ease_in_and_out": 3, "ease_in_out": 3, "in_and_out": 3, "in_out": 3,
}
SCALING_MODES = {
    "use_project": 0, "project": 0, "crop": 1, "fit": 2, "fill": 3, "stretch": 4,
}
RESIZE_FILTERS = {
    "use_project": 0, "project": 0, "sharper": 1, "smoother": 2, "bicubic": 3,
    "bilinear": 4, "bessel": 5, "box": 6, "catmull_rom": 7, "cubic": 8,
    "gaussian": 9, "lanczos": 10, "mitchell": 11, "nearest_neighbor": 12,
    "nearest": 12, "quadratic": 13, "sinc": 14, "linear": 15,
}
RETIME_PROCESS = {
    "use_project": 0, "project": 0, "nearest": 1, "frame_blend": 2,
    "optical_flow": 3,
}
MOTION_ESTIMATION = {
    "use_project": 0, "project": 0, "standard_faster": 1, "standard_better": 2,
    "enhanced_faster": 3, "enhanced_better": 4, "speed_warp_better": 5,
    "speed_warp_faster": 6,
}

# param name -> (Resolve property key, name->int table)
TRANSFORM_ENUMS = {
    "dynamic_zoom_ease": ("DynamicZoomEase", DYNAMIC_ZOOM_EASE),
    "scaling": ("Scaling", SCALING_MODES),
    "resize_filter": ("ResizeFilter", RESIZE_FILTERS),
    "retime_process": ("RetimeProcess", RETIME_PROCESS),
    "motion_estimation": ("MotionEstimation", MOTION_ESTIMATION),
}

READABLE_TRANSFORM_KEYS = (
    "Pan", "Tilt", "ZoomX", "ZoomY", "ZoomGang", "RotationAngle",
    "AnchorPointX", "AnchorPointY", "Pitch", "Yaw", "FlipX", "FlipY",
    "CropLeft", "CropRight", "CropTop", "CropBottom", "CropSoftness",
    "CropRetain", "DynamicZoomEase", "CompositeMode", "Opacity", "Distortion",
    "RetimeProcess", "MotionEstimation", "Scaling", "ResizeFilter",
)

PAGES = ("media", "cut", "edit", "fusion", "color", "fairlight", "deliver")

# Delivery ceilings of DaVinci Resolve (Free); Studio has none of these.
FREE_MAX_LONG_EDGE = 3840
FREE_MAX_SHORT_EDGE = 2160
FREE_MAX_FPS = 60


# Default unknown/new operations to mutations for queue context protection.
QUEUE_READ_ONLY = frozenset({
    "status", "project_info", "list_timelines", "timeline_overview", "list_media", "list_ai_timelines",
    "shot_match_sources", "visual_diff",
    "get_clip_transform", "get_clip_grade", "inspect_fusion", "list_render_presets",
    "bridge_capabilities", "project_health", "compare_timelines", "timeline_audio",
    "render_status", "current_context", "fusion_inspect_tool", "project_overview",
    "transcription_source", "media_sources", "timeline_fingerprint", "timeline_notes", "list_checkpoints", "diff_since", "get_preferences",
    "read_clip_volumes",
})

# resolve_api: calls that delete project data with no undo through the API.
RESOLVE_API_DESTRUCTIVE = frozenset({
    "DeleteProject", "DeleteFolder", "DeleteTimelines", "DeleteClips", "DeleteFolders", "DeleteClipMattes",
    "DeleteStills", "DeleteMedia", "DeleteTakeByIndex", "ClearRenderJobs", "DeleteAllRenderJobs",
    "CloseProject", "Quit",
})
RESOLVE_API_ROOTS = ("resolve", "project_manager", "project", "media_pool", "media_storage", "current_timeline",
                     "fusion")
RESOLVE_API_MAX_STEPS = 50
RESOLVE_API_MAX_LIST = 200
RESOLVE_API_LIST_CAP = 20  # Longer list results come back as {count, items: first 20, truncated} by default.
RESOLVE_API_MAX_CALLS = 500  # Every call/getattr/get plus each for_each element, per resolve_api request.
RESOLVE_API_ID_TARGETS = ("item", "media", "timeline")  # "item:<uuid>", "media:<uuid>", "timeline:<uuid>"
_RESOLVE_API_PATH = r"\[\d+\]|\.[A-Za-z_][\w ]*"
_TIMELINE_NAME_CACHE = {}  # (project name, timeline count) -> (time, [timeline summaries])


class OperationError(RuntimeError):
    """A Resolve operation failed for a reason the user or model can act on."""


def call(obj, method, default=None, *args):
    """Call an optional Resolve API method without raising."""
    try:
        function = getattr(obj, method, None)
        return function(*args) if callable(function) else default
    except Exception:
        return default


def _comp_open(comp):
    """Begin a Fusion edit: an undo group, then Lock.

    The undo group is what makes the edit show. Live on Resolve 21.2 Free, nodes, links and values set
    through the API without it were kept in the clip's comp (inspect and ExportFusionComp showed them)
    but the viewer and Deliver renders kept using the old comp. Closing the group with EndUndo(True)
    marks the comp modified, and Resolve re-reads it. It also gives the Fusion page one undo step.
    """
    call(comp, "StartUndo", None, "AI Bridge")
    comp.Lock()


def _comp_close(comp):
    """End a Fusion edit started with _comp_open (also after a rollback, so Resolve re-reads the comp)."""
    try:
        comp.Unlock()
    finally:
        call(comp, "EndUndo", None, True)


def plain(value):
    """Convert Resolve's native objects into JSON-safe values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return str(value)


def _absolute(value):
    return str(Path(str(value)).expanduser().resolve())


def _notes_tag(custom_data):
    """customData for a bridge-made marker, tagged so timeline_notes never mistakes it for a user's note."""
    from bridge import notes as notes_logic
    return notes_logic.bridge_tag(custom_data)


# Reverse of TRANSFORM_FLOATS/BOOLS: Resolve property key -> set_clip_transform
# param name, used to re-apply a read-back transform onto rebuilt clip halves.
# CompositeMode and the enum modes read back as their integer constant, which
# _apply_transform accepts directly, so they are mapped through too.
_PROP_TO_PARAM = {"CompositeMode": "composite_mode"}
for _pname, _pkey in list(TRANSFORM_FLOATS.items()) + list(TRANSFORM_BOOLS.items()):
    _PROP_TO_PARAM[_pkey] = _pname
for _pname, (_pkey, _table) in TRANSFORM_ENUMS.items():
    _PROP_TO_PARAM[_pkey] = _pname


def transform_params_from_props(props):
    """Turn a {"Pan": .., "ZoomX": ..} read-back into set_clip_transform params."""
    params = {}
    if not isinstance(props, dict):
        return params
    for key, value in props.items():
        name = _PROP_TO_PARAM.get(key)
        if name is not None:
            params[name] = value
    return params


# ------------------------------------------------------------------ fusion
#
# Bridge Fusion effects share one segment directly in front of MediaOut:
#
#   <user chain or MediaIn> -> blur -> spotlight -> zoom -> saturation -> color_fx -> MediaOut
#
# Rebuilding the segment keeps whatever already feeds it, so user nodes and the
# other bridge effects stay connected. Only nodes tagged as bridge-owned (or the
# AIBridge* names earlier versions created) are moved or deleted.

FUSION_OWNER = "resolve-ai-bridge.fusion.v1"
FUSION_OWNER_KEY = "ResolveAIBridgeOwner"
FUSION_STAGE_KEY = "ResolveAIBridgeStage"
FUSION_ROLE_KEY = "ResolveAIBridgeRole"
FUSION_LUA_KEY = "ResolveAIBridgeLua"
FUSION_TARGET_KEY = "ResolveAIBridgeTarget"  # One-time tag proving a comp:Execute script runs in the named comp.
# Custom stages (fusion_build / fusion_apply_setting) mark their chain ends:
# CHAIN is "entry", "exit" or "both"; LINK is the entry input fed by the
# segment ("" for the main input); FEED lists extra inputs fed the same source.
FUSION_CHAIN_KEY = "ResolveAIBridgeChain"
FUSION_LINK_KEY = "ResolveAIBridgeLink"
FUSION_FEED_KEY = "ResolveAIBridgeFeed"
FUSION_STAGE_ORDER = ("blur", "spotlight", "zoom", "saturation", "color_fx", "custom", "speed")
# Largest Fusion .setting text or Lua snippet sent through comp:Execute.
FUSION_TEXT_LIMIT = 200 * 1024
# Names created before stages were tagged: prefix -> (stage kind, role).
LEGACY_FUSION_PREFIXES = (
    ("AIBridgeBlur_", "blur", "main"),
    ("AIBridgeMask_", "blur", "mask"),
    ("AIBridgeSpotlightBase_", "spotlight", "base"),
    ("AIBridgeSpotlightGain_", "spotlight", "gain"),
    ("AIBridgeSpotlightMerge_", "spotlight", "merge"),
    ("AIBridgeSpotlightMask_", "spotlight", "mask"),
)
# Lua keeps per-function constant tables small; split long key lists.
LUA_KEY_CHUNK = 2000
LUA_KEYS_HELPER = ("local function aib_keys(set, first, values) "
                   "for i = 1, #values do set(first + i - 1, values[i]) end end")


def _lua_num(value):
    value = float(value)
    if not math.isfinite(value):
        raise OperationError("Fusion values must be finite numbers.")
    return "%.6g" % value


def _lua_string(value):
    """A double-quoted Lua 5.1 string literal (decimal escapes; Lua has no \\uXXXX)."""
    out = []
    for char in str(value):
        code = ord(char)
        if char in ('"', "\\"):
            out.append("\\" + char)
        elif char == "\n":
            out.append("\\n")
        elif code < 32 or code == 127:
            out.append("\\%03d" % code)
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _lua_long_string(text):
    """Wrap ``text`` in a Lua long bracket that nothing inside it can close early."""
    level = 0
    while (text + "]" + "=" * level + "]").find("]" + "=" * level + "]") < len(text):
        level += 1
    # Lua drops one newline right after the opening bracket; add it so the text keeps its own.
    return "[" + "=" * level + "[\n" + text + "]" + "=" * level + "]"


_SETTING_ENTRY = re.compile(r'\s*(?:([A-Za-z_]\w*)|\["([^"]+)"\])\s*=\s*([A-Za-z_]\w*)\s*\{')
_SETTING_TOOLS = re.compile(r'\bTools\s*=\s*(?:ordered\(\)\s*)?\{')


def setting_tools(text):
    """Top-level tools a Fusion .setting or .comp text declares: [{"name", "reg_id", "file", "media_in"}].

    ``file`` is a Loader's first Clip Filename; ``media_in`` is True for a Loader that is really an exported
    MediaIn (ExportFusionComp writes MediaIn1 as ``Loader { CustomData = { MediaProps = ...`` ). Best effort:
    returns [] for text it cannot parse. Never raises.
    """
    try:
        text = str(text)
        found = _SETTING_TOOLS.search(text)
        if not found:
            return []
        tools, depth, index, current = [], 1, found.end(), None
        while index < len(text) and depth > 0:
            if depth == 1 and current is None:
                entry = _SETTING_ENTRY.match(text, index)
                if entry:
                    current = {"name": entry.group(1) or entry.group(2), "reg_id": entry.group(3), "start": entry.end()}
                    depth, index = 2, entry.end()
                    continue
            char = text[index]
            if char == '"':
                index += 1
                while index < len(text) and text[index] != '"':
                    index += 2 if text[index] == "\\" else 1
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 1 and current is not None:
                    body = text[current.pop("start"):index]
                    filename = re.search(r'\bFilename\s*=\s*"((?:[^"\\]|\\.)*)"', body)
                    current["file"] = filename.group(1) if filename else None
                    current["media_in"] = bool(re.search(r"\bMediaProps\s*=", body))
                    tools.append(current)
                    current = None
            index += 1
        return tools
    except Exception:
        return []


def fusion_conversions(declared, comp):
    """Compare declared Loaders with what the comp holds after a paste or ImportFusionComp.

    Resolve converts file Loaders on import (and may on paste). Returns a list of
    {"tool", "declared", "actual", "file"} for real file Loaders (not exported MediaIns) whose tool is
    missing or no longer a Loader.
    """
    loaders = [t for t in declared if t["reg_id"] == "Loader" and not t["media_in"]]
    if not loaders:
        return []
    present = {}
    for tool in _fusion_tools(comp):
        present[_fusion_name(tool)] = call(tool, "GetAttrs", None, "TOOLS_RegID")
    problems = []
    for loader in loaders:
        name = loader["name"]
        match = name if name in present else next(
            (other for other in sorted(present) if re.fullmatch(re.escape(name) + r"_\d+", other)), None)
        actual = present.get(match) if match else None
        if actual != "Loader":
            problems.append({"tool": match or name, "declared": "Loader", "actual": actual, "file": loader["file"]})
    return problems


def _conversion_warning(problems, where):
    return ("%s: Resolve did not keep %s as file Loader(s) (%s). The picture they loaded (%s) is probably missing "
            "or replaced by the clip's own media; rebuild each with fusion_build (type Loader, input Clip) and check "
            "it on the Fusion page." % (
                where, ", ".join(p["tool"] for p in problems),
                ", ".join("%s is now %s" % (p["tool"], p["actual"] or "gone") for p in problems),
                ", ".join(sorted({str(p["file"]) for p in problems if p["file"]})) or "unknown file"))


def _comp_guard(comp, started_key):
    """Lua lines that stop a comp:Execute script unless it runs in ``comp`` itself.

    Resolve can run an Execute'd script in a different composition (the one open on the Fusion page). The
    target is tagged with a one-time nonce first; the script returns before touching anything when the
    ``comp`` it sees lacks the tag, and otherwise marks ``started_key`` "started" so a later "pending" proves
    nothing ran here. Returns [] when this build will not store comp data (no guard is possible).
    """
    nonce = uuid.uuid4().hex
    call(comp, "SetData", None, FUSION_TARGET_KEY, nonce)
    if call(comp, "GetData", None, FUSION_TARGET_KEY) != nonce:
        return []
    return ['if comp == nil or comp:GetData("%s") ~= "%s" then return end' % (FUSION_TARGET_KEY, nonce),
            'comp:SetData("%s", "started")' % started_key]


def _fusion_name(tool):
    try:
        return str(tool.Name)
    except Exception:
        return str(call(tool, "GetAttrs", "", "TOOLS_Name") or "")


def _fusion_tools(comp):
    tools = call(comp, "GetToolList", None, False) or {}
    return list(tools.values()) if isinstance(tools, dict) else list(tools)


def _fusion_role(tool):
    """Return (stage key, role) for a bridge-owned node, otherwise None."""
    if (call(tool, "GetData", None, FUSION_OWNER_KEY) == "resolve-ai-bridge.change_clip_speed.v1"
            and call(tool, "GetAttrs", None, "TOOLS_RegID") == "TimeSpeed"):
        return "speed", "main"
    if call(tool, "GetData", None, FUSION_OWNER_KEY) == FUSION_OWNER:
        stage = call(tool, "GetData", None, FUSION_STAGE_KEY)
        role = call(tool, "GetData", None, FUSION_ROLE_KEY)
        return (str(stage), str(role)) if stage and role and role != "retired" else None
    name = _fusion_name(tool)
    if name == "AIBridgeZoom":
        return "zoom", "main"
    for prefix, kind, role in LEGACY_FUSION_PREFIXES:
        tag = name[len(prefix):]
        if name.startswith(prefix) and tag.isalnum():
            return "%s:%s" % (kind, tag), role
    return None


def _is_retired(tool):
    return (call(tool, "GetData", None, FUSION_OWNER_KEY) == FUSION_OWNER
            and call(tool, "GetData", None, FUSION_ROLE_KEY) == "retired")


def _fusion_stages(comp):
    stages = {}
    for tool in _fusion_tools(comp):
        owned = _fusion_role(tool)
        if owned:
            stages.setdefault(owned[0], {})[owned[1]] = tool
    return stages


def _stage_ports(nodes):
    """Return ([(entry tool, input name or None for main)], output tool), or None if incomplete."""
    if "merge" in nodes:
        if "base" not in nodes:
            return None
        entries = [(nodes["base"], None)]
        # Spotlights from earlier versions fed Foreground directly when gain was 1.
        entries.append((nodes["gain"], None) if "gain" in nodes else (nodes["merge"], "Foreground"))
        return entries, nodes["merge"]
    if "main" in nodes:
        return [(nodes["main"], None)], nodes["main"]
    # Custom stage: any number of nodes, with tagged entry and exit.
    entry = exit_tool = None
    feeds = []
    for role in sorted(nodes):
        tool = nodes[role]
        position = call(tool, "GetData", None, FUSION_CHAIN_KEY)
        if position in ("entry", "both"):
            entry = tool
        if position in ("exit", "both"):
            exit_tool = tool
        feeds += [(tool, name) for name in _feed_inputs(tool)]
    if entry is None or exit_tool is None:
        return None
    return [(entry, call(entry, "GetData", None, FUSION_LINK_KEY) or None)] + feeds, exit_tool


def _feed_inputs(tool):
    raw = call(tool, "GetData", None, FUSION_FEED_KEY)
    return [name for name in str(raw).split(",") if name] if raw else []


def _named_input(tool, name):
    inputs = call(tool, "GetInputList", {}) or {}
    return next((inp for inp in (inputs.values() if isinstance(inputs, dict) else inputs)
                 if call(inp, "GetAttrs", None, "INPS_ID") == name), None)


def _entry_source(tool, input_name=None):
    """Output feeding a stage entry: its main input, or the named link input."""
    if not input_name:
        return _main_source(tool)
    return call(_named_input(tool, input_name), "GetConnectedOutput", None)


def _stage_sort_key(key):
    kind, _, tag = key.partition(":")
    order = FUSION_STAGE_ORDER.index(kind) if kind in FUSION_STAGE_ORDER else len(FUSION_STAGE_ORDER)
    return order, tag


def _fusion_endpoint(comp, kind):
    tool = call(comp, "FindTool", None, kind + "1")
    if tool is not None:
        return tool
    for candidate in _fusion_tools(comp):
        if call(candidate, "GetAttrs", None, "TOOLS_RegID") == kind:
            return candidate
    return None


def _main_source(tool):
    main = call(tool, "FindMainInput", None, 1)
    return call(main, "GetConnectedOutput", None) if main is not None else None


def _retired_entries(comp):
    """Retired custom-stage entries by stage key, so a rebuild can look through a replaced stage."""
    entries = {}
    for tool in _fusion_tools(comp):
        if _is_retired(tool) and call(tool, "GetData", None, FUSION_CHAIN_KEY) in ("entry", "both"):
            entries[call(tool, "GetData", None, FUSION_STAGE_KEY)] = tool
    return entries


def _fusion_walk(media_out, stages, comp=None):
    """Follow MediaOut upstream through bridge stages.

    Returns (first non-bridge output, stage keys in downstream-first order).
    ``comp`` lets the walk look through a custom stage that is being replaced.
    """
    retired = []
    by_output = {}
    for key, nodes in stages.items():
        ports = _stage_ports(nodes)
        if ports:
            by_output[_fusion_name(ports[1])] = (key, ports)
    keys = []
    output = _main_source(media_out)
    for _ in range(1000):
        if output is None:
            break
        tool = call(output, "GetTool", None)
        hit = by_output.get(_fusion_name(tool)) if tool is not None else None
        if hit is None:
            if tool is not None and _is_retired(tool):
                # A node being replaced in this edit: look through it. A replaced
                # custom stage is skipped from its exit straight to its entry's link.
                entry = None
                if comp is not None and call(tool, "GetData", None, FUSION_CHAIN_KEY) in ("exit", "both"):
                    if not retired:
                        retired.append(_retired_entries(comp))
                    entry = retired[0].get(call(tool, "GetData", None, FUSION_STAGE_KEY))
                output = (_entry_source(entry, call(entry, "GetData", None, FUSION_LINK_KEY) or None)
                          if entry is not None else _main_source(tool))
                continue
            break
        if hit[0] in keys:
            raise OperationError("The clip's Fusion chain loops through %s; inspect it on the Fusion page." % hit[0])
        keys.append(hit[0])
        output = _entry_source(*hit[1][0][0])
    return output, keys


def _upstream_names(output, limit=400):
    """Names of every tool feeding ``output``, bounded for very large comps."""
    names, pending = set(), [output]
    while pending and len(names) < limit:
        tool = call(pending.pop(), "GetTool", None)
        if tool is None:
            continue
        name = _fusion_name(tool)
        if name in names:
            continue
        names.add(name)
        inputs = call(tool, "GetInputList", None) or {}
        for inp in (inputs.values() if isinstance(inputs, dict) else inputs):
            connected = call(inp, "GetConnectedOutput", None)
            if connected is not None:
                pending.append(connected)
    return names


def _output_name(output):
    tool = call(output, "GetTool", None) if output is not None else None
    return _fusion_name(tool) if tool is not None else None


def fusion_rebuild(comp, remove=()):
    """Place every bridge stage, in order, directly before MediaOut.

    Stages listed in ``remove`` are disconnected and deleted. MediaOut is
    restored to its previous source if any step fails or does not read back.
    """
    media_out = _fusion_endpoint(comp, "MediaOut")
    if media_out is None:
        raise OperationError("Cannot locate MediaOut in this clip's Fusion composition; nothing was changed.")
    out_input = call(media_out, "FindMainInput", None, 1)
    if out_input is None:
        raise OperationError("MediaOut has no main input this build exposes; nothing was changed.")
    stages = _fusion_stages(comp)
    original = call(out_input, "GetConnectedOutput", None)
    source, _ = _fusion_walk(media_out, stages, comp)
    if source is None:
        media_in = _fusion_endpoint(comp, "MediaIn")
        source = call(media_in, "FindMainOutput", None, 1) if media_in is not None else None
    if source is None:
        raise OperationError("MediaOut has no source and no MediaIn was found; nothing was changed.")
    upstream = _upstream_names(source)
    placed, left_in_place, incomplete = [], [], []
    for key in sorted(stages, key=_stage_sort_key):
        ports = _stage_ports(stages[key])
        output_name = _fusion_name(ports[1]) if ports else None
        if key in remove:
            if output_name in upstream:
                raise OperationError(
                    "%s sits before your own Fusion node; remove it on the Fusion page so your chain "
                    "is not broken. Nothing was changed." % key)
            continue
        if ports is None:
            incomplete.append(key)
        elif output_name in upstream:
            left_in_place.append(key)
        else:
            placed.append(key)
    kept = {key: nodes for key, nodes in stages.items() if key not in remove}
    try:
        previous = source
        for key in placed:
            entries, output_tool = _stage_ports(stages[key])
            for tool, input_name in entries:
                if input_name is None:
                    if not call(call(tool, "FindMainInput", None, 1), "ConnectTo", False, previous):
                        raise OperationError("Fusion refused to connect %s." % _fusion_name(tool))
                else:
                    call(tool, "ConnectInput", None, input_name, previous)
            previous = call(output_tool, "FindMainOutput", None, 1)
            if previous is None:
                raise OperationError("%s has no main output this build exposes." % _fusion_name(output_tool))
        if not call(out_input, "ConnectTo", False, previous):
            raise OperationError("Fusion refused to connect the bridge effects to MediaOut.")
        after_source, after = _fusion_walk(media_out, kept, comp)
        if after != list(reversed(placed)) or _output_name(after_source) != _output_name(source):
            raise OperationError("The rebuilt Fusion chain did not read back as expected; MediaOut was restored.")
    except Exception:
        call(out_input, "ConnectTo", None, original)
        raise
    removed = []
    for key in remove:
        for node in stages.get(key, {}).values():
            call(node, "Delete", None)
        if key in stages:
            removed.append(key)
    return {"source": _output_name(source), "stages": placed, "left_in_place": left_in_place,
            "incomplete": incomplete, "removed": removed}


class FusionEdit:
    """One locked edit of a clip's comp: tag nodes, run Lua, rebuild the bridge chain."""

    def __init__(self, ops, comp, stage):
        self.ops = ops
        self.comp = comp
        self.stage = stage
        self.nodes = _fusion_stages(comp).get(stage, {})
        self.created = []
        self.retired = []
        self.bindings = {}
        self.actions = []
        self.native_fallback = False
        self.allow_native_fallback = False  # Opt-in: the general Fusion tools re-apply via node handles.
        value = call(comp, "GetAttrs", None, "COMPN_RenderStart")
        try:
            value = float(value)
            self.t0 = value if math.isfinite(value) else 0.0
        except (TypeError, ValueError):
            self.t0 = 0.0

    @staticmethod
    def name(tool):
        return _fusion_name(tool)

    def _retire(self, node):
        role = call(node, "GetData", None, FUSION_ROLE_KEY)
        self.retired.append((node, _fusion_name(node), call(node, "GetData", None, FUSION_OWNER_KEY), role))
        call(node, "SetData", None, FUSION_OWNER_KEY, FUSION_OWNER)
        call(node, "SetData", None, FUSION_ROLE_KEY, "retired")
        call(node, "SetAttrs", None, {"TOOLS_Name": "AIBridgeRetired_%s" % uuid.uuid4().hex[:8]})

    def node(self, role, reg_ids, name):
        """Reuse this stage's node for ``role``, or create one of the first available type."""
        existing = self.nodes.get(role)
        if existing is not None:
            reg_id = call(existing, "GetAttrs", None, "TOOLS_RegID")
            if reg_id is None or reg_id in reg_ids:
                self._mark(existing, role, name)
                return existing
            self._retire(existing)
        node = None
        for reg_id in reg_ids:
            node = call(self.comp, "AddTool", None, reg_id, -32768, -32768)
            if node is not None:
                break
        if node is None:
            raise OperationError("Fusion could not add a %s node on this build." % " or ".join(reg_ids))
        self.created.append(node)
        self._mark(node, role, name)
        self.nodes[role] = node
        return node

    def drop(self, role):
        """Delete this stage's ``role`` node once the chain has been rebuilt."""
        node = self.nodes.pop(role, None)
        if node is not None:
            self._retire(node)

    def _mark(self, node, role, name):
        if _fusion_name(node) != name:
            call(node, "SetAttrs", None, {"TOOLS_Name": name})
        call(node, "SetData", None, FUSION_OWNER_KEY, FUSION_OWNER)
        call(node, "SetData", None, FUSION_STAGE_KEY, self.stage)
        call(node, "SetData", None, FUSION_ROLE_KEY, role)
        if call(node, "GetData", None, FUSION_STAGE_KEY) != self.stage:
            raise OperationError("Could not tag the bridge-owned Fusion node %s." % name)

    @staticmethod
    def has_input(node, input_name):
        # GetInput returns nil for an unconnected image input (Merge.Background) and, on some builds, for an
        # animated one, so a None value proves nothing: fall back to the tool's input list.
        if call(node, "GetInput", None, input_name) is not None:
            return True
        return _named_input(node, input_name) is not None

    def bind(self, var, node):
        self.bindings[var] = node
        name = json.dumps(_fusion_name(node))
        return "local %s = comp:FindTool(%s)\nassert(%s, %s)" % (
            var, name, var, json.dumps("Fusion lost " + _fusion_name(node)))

    def static(self, var, input_name, value):
        self.actions.append(("static", var, input_name, value))
        return ['%s:ConnectInput("%s", nil)' % (var, input_name), "%s.%s = %s" % (var, input_name, _lua_num(value))]

    def static_point(self, var, input_name, x, y):
        self.actions.append(("static", var, input_name, (x, y)))
        return ['%s:ConnectInput("%s", nil)' % (var, input_name),
                "%s.%s = {%s, %s}" % (var, input_name, _lua_num(x), _lua_num(y))]

    def static_text(self, var, input_name, value):
        self.actions.append(("text", var, input_name, str(value)))
        return ['%s:ConnectInput("%s", nil)' % (var, input_name), "%s.%s = %s" % (var, input_name, _lua_string(value))]

    def add_keys(self, var, input_name, pairs):
        """Key an input that already carries a BezierSpline, keeping its other keys."""
        self.actions.append(("append", var, input_name, pairs))
        return self._keys("%s.%s" % (var, input_name), pairs)

    def connect(self, var, input_name, target=None):
        self.actions.append(("connect", var, input_name, target))
        return '%s:ConnectInput("%s", %s)' % (var, input_name, target or "nil")

    def _keys(self, target, pairs):
        """Key ``target`` once per listed clip frame, in compact Lua chunks."""
        lines = []
        runs = []
        for frame, value in pairs:
            if runs and frame == runs[-1][0] + len(runs[-1][1]) and len(runs[-1][1]) < LUA_KEY_CHUNK:
                runs[-1][1].append(value)
            else:
                runs.append((int(frame), [value]))
        for first, values in runs:
            # Values sit in their own function so no chunk outgrows Lua's constant table.
            lines.append("aib_keys(function(t, x) %s[t] = x end, %s, (function() return {%s} end)())"
                         % (target, _lua_num(self.t0 + first), ",".join(_lua_num(v) for v in values)))
        return lines

    def animate(self, var, input_name, pairs):
        self.actions.append(("animate", var, input_name, pairs))
        # Disconnect first: an input that already carries a spline or path is replaced, not stacked.
        return ['%s:ConnectInput("%s", nil)' % (var, input_name),
                "%s.%s = BezierSpline()" % (var, input_name)] + self._keys("%s.%s" % (var, input_name), pairs)

    def animate_point(self, var, input_name, xs, ys):
        self.actions.append(("point", var, input_name, (xs, ys)))
        path = "%s_%s" % (var, input_name.lower())
        lines = ['%s:ConnectInput("%s", nil)' % (var, input_name), "%s.%s = XYPath()" % (var, input_name),
                 "local %s = %s.%s:GetConnectedOutput():GetTool()" % (path, var, input_name),
                 "%s.X = BezierSpline()" % path, "%s.Y = BezierSpline()" % path]
        return lines + self._keys(path + ".X", xs) + self._keys(path + ".Y", ys)

    def add_point_keys(self, var, input_name, xs, ys):
        """Key an [x, y] input that already carries an XYPath, keeping the path's other keys."""
        self.actions.append(("append_point", var, input_name, (xs, ys)))
        path = "%s_%s" % (var, input_name.lower())
        return (["local %s = %s.%s:GetConnectedOutput():GetTool()" % (path, var, input_name)]
                + self._keys(path + ".X", xs) + self._keys(path + ".Y", ys))

    @staticmethod
    def _input(node, name):
        inputs = call(node, "GetInputList", {}) or {}
        return next((inp for inp in (inputs.values() if isinstance(inputs, dict) else inputs)
                     if call(inp, "GetAttrs", None, "INPS_ID") == name), None)

    @staticmethod
    def _verify_value(node, name, value, time=None):
        actual = call(node, "GetInput", None, name, time) if time is not None else call(node, "GetInput", None, name)
        try:
            if isinstance(value, tuple):
                actual = [actual.get(i, actual.get(str(i))) for i in (1, 2)] if isinstance(actual, dict) else actual
                valid = all(math.isclose(float(a), b, rel_tol=1e-7, abs_tol=1e-7) for a, b in zip(actual, value)) and len(actual) >= 2
            else:
                valid = math.isclose(float(actual), float(value), rel_tol=1e-7, abs_tol=1e-7)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise OperationError("Fusion did not preserve %s.%s at %s; inspect the clip." % (_fusion_name(node), name, time))

    def _native_curve(self, node, name, pairs):
        call(node, "ConnectInput", None, name, None)
        if not call(node, "AddModifier", False, name, "BezierSpline"):
            raise OperationError("Fusion could not animate %s.%s on this build." % (_fusion_name(node), name))
        for frame, value in pairs:
            node.SetInput(name, value, self.t0 + frame)
        for frame, value in (pairs[0], pairs[-1]):
            self._verify_value(node, name, value, self.t0 + frame)

    def _native(self):
        # A resident Python menu worker can hold Fusion's shared script executor.
        # Use ordinary native input/modifier APIs instead of queuing another script.
        for kind, var, name, value in self.actions:
            node = self.bindings[var]
            if kind == "static":
                call(node, "ConnectInput", None, name, None)
                node.SetInput(name, {1: value[0], 2: value[1]} if isinstance(value, tuple) else value)
                self._verify_value(node, name, value)
            elif kind == "text":
                call(node, "ConnectInput", None, name, None)
                node.SetInput(name, value)
                actual = call(node, "GetInput", None, name)
                if actual is not None and str(actual) != value:
                    raise OperationError("Fusion did not preserve %s.%s; inspect the clip." % (_fusion_name(node), name))
            elif kind == "animate":
                self._native_curve(node, name, value)
            elif kind == "append":
                for frame, number in value:
                    node.SetInput(name, number, self.t0 + frame)
                for frame, number in (value[0], value[-1]):
                    self._verify_value(node, name, number, self.t0 + frame)
            elif kind == "append_point":
                path = call(call(self._input(node, name), "GetConnectedOutput", None), "GetTool", None)
                if path is None:
                    raise OperationError("Fusion did not expose the animated point path.")
                xs, ys = value
                for axis, pairs in (("X", xs), ("Y", ys)):
                    for frame, number in pairs:
                        path.SetInput(axis, number, self.t0 + frame)
                for index in (0, -1):
                    self._verify_value(node, name, (xs[index][1], ys[index][1]), self.t0 + xs[index][0])
            elif kind == "point":
                call(node, "ConnectInput", None, name, None)
                if not call(node, "AddModifier", False, name, "XYPath"):
                    raise OperationError("Fusion could not add the animated point path.")
                port = self._input(node, name)
                output = call(port, "GetConnectedOutput", None)
                path = call(output, "GetTool", None)
                if path is None:
                    raise OperationError("Fusion did not expose the animated point path.")
                xs, ys = value
                self._native_curve(path, "X", xs)
                self._native_curve(path, "Y", ys)
                for index in (0, -1):
                    self._verify_value(node, name, (xs[index][1], ys[index][1]), self.t0 + xs[index][0])
            elif kind == "connect":
                target = self.bindings[value] if value is not None else None
                node.ConnectInput(name, target)
                output = call(self._input(node, name), "GetConnectedOutput", None)
                if _output_name(output) != (_fusion_name(target) if target is not None else None):
                    raise OperationError("Fusion did not connect %s.%s." % (_fusion_name(node), name))
        return True

    def lua(self, lines):
        """Run Lua in the comp: True if it finished, False if it stopped, None if unknowable."""
        if self.ops.transport == "console":
            return self._native()
        nonce = uuid.uuid4().hex
        call(self.comp, "SetData", None, FUSION_LUA_KEY, "pending")
        error_key = FUSION_LUA_KEY + "Error"
        call(self.comp, "SetData", None, error_key, None)
        before = call(self.comp, "GetData", None, FUSION_LUA_KEY)
        guard = _comp_guard(self.comp, FUSION_LUA_KEY)
        script = "\n".join(guard + ["local aib_ok, aib_error = pcall(function()", LUA_KEYS_HELPER] + list(lines) + [
            "end)",
            'if not aib_ok then comp:SetData("%s", tostring(aib_error)); return end' % error_key,
            'comp:SetData("%s", "%s")' % (FUSION_LUA_KEY, nonce)])
        call(self.comp, "Execute", None, script)
        after = call(self.comp, "GetData", None, FUSION_LUA_KEY)
        error = call(self.comp, "GetData", None, error_key)
        if error:
            raise OperationError("Fusion Lua failed: %s. The bridge effect was not connected." % error)
        if after == nonce:
            return True
        if before == "pending" and after == "pending" and guard and self.allow_native_fallback:
            # The script never ran in this clip's comp: Resolve ran it elsewhere (the guard stopped it before
            # any change) or not at all. Node handles are bound to this comp, so apply the same edits natively.
            self.native_fallback = True
            return self._native()
        return False if before == "pending" else None

    def check(self, node, input_name, clip_frame, expected, tolerance=1e-3):
        """Read an animated value back: True/False, or None when the build will not say."""
        value = call(node, "GetInput", None, input_name, self.t0 + clip_frame)
        try:
            return abs(float(value) - float(expected)) <= tolerance
        except (TypeError, ValueError):
            return None

    def finish(self):
        chain = fusion_rebuild(self.comp)
        for node, _, _, _ in self.retired:
            call(node, "Delete", None)
        self.retired = []
        self.created = []
        return chain

    def rollback(self):
        for node in self.created:
            call(node, "Delete", None)
        for node, name, owner, role in self.retired:
            call(node, "SetAttrs", None, {"TOOLS_Name": name})
            call(node, "SetData", None, FUSION_OWNER_KEY, owner)
            call(node, "SetData", None, FUSION_ROLE_KEY, role)
        self.created = []
        self.retired = []


class ResolveOperations:
    """Every bridge operation, implemented against a live Resolve object.

    ``provider`` is a zero-argument callable that returns the Resolve API
    object. It is called on every operation so a transport that can lose its
    connection (direct attach) is able to reconnect transparently. Workers can
    supply ``runtime_home`` when their installed path differs from the process environment.
    """

    def __init__(self, provider, token_id=None, transport="unknown", *, runtime_home=None):
        self._provider = provider
        self.token_id = token_id
        self.transport = transport
        self.runtime_home = runtime_home

    # ------------------------------------------------------------------ core

    @property
    def resolve(self):
        instance = self._provider()
        if instance is None:
            raise OperationError(
                "DaVinci Resolve is not reachable. Open Resolve with a project, then try again."
            )
        return instance

    def _project(self):
        manager = self.resolve.GetProjectManager()
        if manager is None:
            raise OperationError("Resolve did not return a Project Manager.")
        project = manager.GetCurrentProject()
        if project is None:
            raise OperationError("No project is open in Resolve. Open a project and try again.")
        return project

    def _timeline(self):
        timeline = self._project().GetCurrentTimeline()
        if timeline is None:
            raise OperationError("No timeline is open. Open or create a timeline, then try again.")
        return timeline

    def _media_pool(self):
        pool = self._project().GetMediaPool()
        if pool is None:
            raise OperationError("Resolve did not return a Media Pool.")
        return pool

    def _project_rate(self):
        if getattr(self, "_pinned_rate", None):
            return self._pinned_rate  # Fixed for the length of one read-only scan (see transcription_source).
        project = self._project()
        timeline = call(project, "GetCurrentTimeline", None)
        raw = call(timeline, "GetSetting", None, "timelineFrameRate")
        raw = raw or call(project, "GetSetting", "24", "timelineFrameRate") or "24"
        try:
            rate = float(str(raw).replace(" DF", "").strip())
            for nominal in (24, 30, 48, 60, 120):
                if abs(rate - nominal / 1.001) < 0.002:
                    return nominal / 1.001
            return rate if math.isfinite(rate) and rate > 0 else 24.0
        except (ValueError, TypeError):
            return 24.0

    def _project_resolution(self):
        project = self._project()
        timeline = call(project, "GetCurrentTimeline", None)
        try:
            width = int(call(timeline, "GetSetting", None, "timelineResolutionWidth") or call(project, "GetSetting", 1920, "timelineResolutionWidth") or 1920)
            height = int(call(timeline, "GetSetting", None, "timelineResolutionHeight") or call(project, "GetSetting", 1080, "timelineResolutionHeight") or 1080)
        except (TypeError, ValueError):
            width, height = 1920, 1080
        return width, height

    def _tc_frames(self, value, fps):
        drop = ";" in str(value)
        parts = str(value).replace(";", ":").split(":")
        if len(parts) != 4:
            raise OperationError("Invalid timecode: %s. Use HH:MM:SS:FF or HH:MM:SS;FF." % value)
        try:
            hours, minutes, seconds, frames = [int(part) for part in parts]
        except ValueError:
            raise OperationError("Invalid timecode: %s." % value)
        rounded = max(1, int(round(fps)))
        if min(hours, minutes, seconds, frames) < 0 or minutes >= 60 or seconds >= 60 or frames >= rounded:
            raise OperationError("Timecode out of range: %s." % value)
        total = ((hours * 3600 + minutes * 60 + seconds) * rounded) + frames
        if drop:
            if rounded not in (30, 60) or abs(fps - rounded / 1.001) > 0.01:
                raise OperationError("Drop-frame timecode requires 29.97 or 59.94 fps.")
            skipped = 2 if rounded == 30 else 4
            if minutes % 10 and seconds == 0 and frames < skipped:
                raise OperationError("Timecode uses a skipped drop-frame label: %s." % value)
            total_minutes = hours * 60 + minutes
            total -= skipped * (total_minutes - total_minutes // 10)
        return total

    def _frames_tc(self, frame, fps, drop=False):
        """Convert a timecode frame count to a valid SMPTE label."""
        frame = int(frame)
        if frame < 0:
            raise OperationError("Timecode cannot be negative.")
        nominal = max(1, int(round(fps)))
        if drop:
            if nominal not in (30, 60) or abs(fps - nominal / 1.001) > 0.01:
                raise OperationError("Drop-frame timecode requires 29.97 or 59.94 fps.")
            skipped = 2 if nominal == 30 else 4
            ten_minutes = nominal * 600 - skipped * 9
            minute = nominal * 60 - skipped
            blocks, remainder = divmod(frame, ten_minutes)
            frame += skipped * 9 * blocks + skipped * max(0, (remainder - skipped) // minute)
        seconds, frames = divmod(frame, nominal)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return "%02d:%02d:%02d%s%02d" % (hours, minutes, seconds, ";" if drop else ":", frames)

    def _current_marker_frame(self, timeline):
        current = call(timeline, "GetCurrentTimecode", "00:00:00:00")
        start = call(timeline, "GetStartTimecode", "00:00:00:00")
        fps = self._project_rate()
        return max(0, self._tc_frames(current, fps) - self._tc_frames(start, fps))

    # -------------------------------------------------------------- lookups

    def _item_summary(self, item, track_type, track_index, item_index):
        prefix = "V" if track_type == "video" else "A" if track_type == "audio" else "S"
        media_item = call(item, "GetMediaPoolItem", None)
        media_id = call(media_item, "GetUniqueId", None) if media_item is not None else None
        unique_id = call(item, "GetUniqueId", None)
        label = "%s%d.%d" % (prefix, track_index, item_index)
        return {
            "id": str(unique_id) if unique_id else label,
            "label": label,
            "unique_id": unique_id,
            "name": call(item, "GetName", ""),
            "track_type": track_type,
            "track_index": track_index,
            "start": call(item, "GetStart", None),
            "end": call(item, "GetEnd", None),
            "duration": call(item, "GetDuration", None),
            "left_offset": call(item, "GetLeftOffset", 0),
            "right_offset": call(item, "GetRightOffset", 0),
            "enabled": call(item, "GetClipEnabled", None),
            "color": call(item, "GetClipColor", ""),
            "media_pool_id": media_id,
        }

    # Resolve lists transitions ("Cross Dissolve", "Cross Fade 0 dB") as items in GetItemListInTrack, which shifts
    # every positional label after them. Clips on one track never overlap; a transition overlaps the clip(s) it
    # joins. So: an item that overlaps another item on its track and has no media pool item (or a transition-like
    # name, when that cannot be asked) is a transition. Only overlapping items cost the extra media lookup.
    TRANSITION_NAME_RE = re.compile(r"(?i)\b(cross ?(fade|dissolve)|dissolve|dip to|wipe|smooth cut|fade)\b")

    @classmethod
    def _transition_flags(cls, spans, has_media=None, name_of=None):
        """spans: [(start, end, name)] in track order. Returns a set of indices that are transitions.

        With handles (has_media / name_of callbacks): an overlapping item with no media pool item that overlaps both
        neighbours, or carries a transition-like name (a fade at a track end overlaps one clip). From names only
        (Lua snapshot): a transition-like name on an overlapping item.
        """
        flags = set()
        for index, (start, end, name) in enumerate(spans):
            if start is None or end is None:
                continue
            overlapping = [j for j, (s2, e2, _) in enumerate(spans) if j != index and s2 is not None and e2 is not None
                           and s2 < end and start < e2]
            if not overlapping:
                continue
            if has_media is None:
                if cls.TRANSITION_NAME_RE.search(str(name or "")):
                    flags.add(index)
                continue
            if has_media(index):
                continue
            if len(overlapping) >= 2 or cls.TRANSITION_NAME_RE.search(str(name_of(index) if name_of else name or "")):
                flags.add(index)
        return flags

    def _track_split(self, timeline, kind, index):
        """(clips, transitions) on one track; transitions as {track, start_frame, end_frame, frames, name}."""
        items = call(timeline, "GetItemListInTrack", None, kind, index) or []
        items = list(items.values()) if isinstance(items, dict) else list(items)
        if len(items) < 2:
            return items, []
        spans = []
        for item in items:
            start, end = call(item, "GetStart", None), call(item, "GetEnd", None)
            try:
                spans.append((float(start), float(end), None))
            except (TypeError, ValueError):
                spans.append((None, None, None))

        def has_media(i):
            # A clip: it has a media pool item, or (FCPXML-imported clips can lack one) a numeric left offset.
            # Resolve 21.2 returns None for both on a transition item (found live).
            if call(items[i], "GetMediaPoolItem", None) is not None:
                return True
            return isinstance(call(items[i], "GetLeftOffset", None), (int, float))
        flags = self._transition_flags(spans, has_media, lambda i: call(items[i], "GetName", ""))
        prefix = {"video": "V", "audio": "A", "subtitle": "S"}.get(kind, "?")
        transitions = [{"track": "%s%d" % (prefix, index), "start_frame": int(spans[i][0]), "end_frame": int(spans[i][1]),
                        "frames": int(spans[i][1] - spans[i][0]), "name": call(items[i], "GetName", "")}
                       for i in sorted(flags)]
        return [item for i, item in enumerate(items) if i not in flags], transitions

    def _track_clips(self, timeline, kind, index):
        return self._track_split(timeline, kind, index)[0]

    def _timeline_transitions(self, timeline):
        found = []
        for kind in ("video", "audio"):
            for index in range(1, int(call(timeline, "GetTrackCount", 0, kind) or 0) + 1):
                found += self._track_split(timeline, kind, index)[1]
        return found

    def _timeline_items(self, timeline=None, track_types=("video", "audio", "subtitle"), track_index=None):
        """Every clip as (item, summary). track_types / track_index limit the walk: over the Lua transport each
        clip costs ~10 round trips, so reading only the tracks an operation needs is much faster."""
        timeline = self._timeline() if timeline is None else timeline
        results = []
        for track_type in track_types:
            count = int(call(timeline, "GetTrackCount", 0, track_type) or 0)
            indexes = range(1, count + 1) if track_index is None else (
                [int(track_index)] if 1 <= int(track_index) <= count else [])
            for track_index_ in indexes:
                items = self._track_clips(timeline, track_type, track_index_)
                for item_index, item in enumerate(items, 1):
                    results.append(
                        (item, self._item_summary(item, track_type, track_index_, item_index))
                    )
        return results

    def _find_timeline_items(self, identifiers):
        wanted = [str(item) for item in identifiers]
        all_items = self._timeline_items()
        found = []
        missing = []
        for identifier in wanted:
            token = identifier.strip().lower()
            if token in ("playhead", "current", "under_playhead", "at_playhead"):
                try:
                    found.append(self._resolve_one_item(identifier))
                    continue
                except Exception:
                    pass
            matches = [
                (item, info)
                for item, info in all_items
                if identifier in (str(info[key]) for key in ("id", "label", "unique_id", "name") if info.get(key))
            ]
            if not matches:
                missing.append(identifier)
                continue
            exact = next(
                (
                    match
                    for match in matches
                    if identifier in (str(match[1][key]) for key in ("id", "label", "unique_id") if match[1].get(key))
                ),
                None,
            )
            if exact is None and len(matches) > 1:
                raise OperationError(
                    "Clip name '%s' is ambiguous. Use an id such as V1.2 from timeline_overview."
                    % identifier
                )
            found.append(exact or matches[0])
        if missing:
            raise OperationError(
                "Timeline item not found: %s. Call timeline_overview for valid ids."
                % ", ".join(missing)
            )
        return found

    def _delete_timeline_items(self, timeline, targets, ripple=False):
        """Confirm removal on the affected tracks before reporting success or rebuilding."""
        if not targets:
            raise OperationError("Could not identify clips to delete. Nothing was removed.")
        unique = {info["id"]: (item, info) for item, info in targets}
        tracks = {}
        for item, info in unique.values():
            track = (info["track_type"], info["track_index"])
            tracks.setdefault(track, []).append((item, info.get("unique_id")))

        def read_track(track):
            items = call(timeline, "GetItemListInTrack", None, *track)
            if isinstance(items, dict):
                return list(items.values())
            return items if isinstance(items, (list, tuple)) else None

        before = {}
        for track, selected in tracks.items():
            items = read_track(track)
            if items is None or len(items) < len(selected):
                raise OperationError("Could not read the selected tracks before deletion. Nothing was removed.")
            before[track] = len(items)

        if not timeline.DeleteClips([item for item, _ in unique.values()], ripple):
            raise OperationError("Resolve rejected the deletion. It may have applied in part. Inspect the timeline before retrying.")

        unverified = "Could not verify clip removal on %s track %d. Inspect the timeline before retrying."
        for track, selected in tracks.items():
            remaining = read_track(track)
            if remaining is None or len(remaining) > before[track] - len(selected):
                raise OperationError(unverified % track)
            wanted_ids = {str(uid) for _, uid in selected if uid}
            without_ids = [item for item, uid in selected if not uid]
            for candidate in remaining:
                if candidate in without_ids:
                    raise OperationError(unverified % track)
                if wanted_ids:
                    uid = call(candidate, "GetUniqueId", None)
                    if not uid or str(uid) in wanted_ids:
                        raise OperationError(unverified % track)
        return list(unique)

    def _playhead_frame(self, timeline):
        """Absolute timeline frame under the playhead (same space as GetStart)."""
        return int(call(timeline, "GetStartFrame", 0) or 0) + self._current_marker_frame(timeline)

    def _resolve_one_item(self, item_id, track_hint=None):
        """Return a single (item, info) pair.

        ``item_id`` may be an explicit id such as V1.2, a unique id, or a clip
        name. It may also be empty or one of "playhead"/"current"/"under_playhead"
        to select the clip sitting under the playhead, so an AI can act on "the
        clip I am looking at" without first reading every id.
        """
        token = str(item_id or "").strip().lower()
        if token and token not in ("playhead", "current", "under_playhead", "at_playhead"):
            return self._find_timeline_items([item_id])[0]

        timeline = self._timeline()
        frame = self._playhead_frame(timeline)
        want_track = None
        want_type = "video"
        if track_hint is not None:
            hint_str = str(track_hint).strip().lower()
            if hint_str in ("audio", "video"):
                want_type = hint_str
            elif hint_str.isdigit():
                want_track = int(hint_str)
            elif isinstance(track_hint, int):
                want_track = int(track_hint)

        best = None
        for item, info in self._timeline_items():
            if want_type and info.get("track_type") != want_type:
                continue
            start = info.get("start")
            end = info.get("end")
            if start is None or end is None:
                continue
            if info.get("enabled") is False or call(timeline, "GetIsTrackEnabled", True, info.get("track_type"), info.get("track_index")) is False:
                continue
            if float(start) <= frame < float(end):
                if want_track is not None and info.get("track_index") != want_track:
                    continue
                # Prefer the highest track (topmost layer) at the playhead.
                if best is None or info.get("track_index", 0) > best[1].get("track_index", 0):
                    best = (item, info)
        if best is None:
            raise OperationError(
                "No %s clip sits under the playhead (timeline frame %d). Move the playhead "
                "onto a clip, or pass an explicit item_id from timeline_overview." % (want_type or "timeline", frame)
            )
        return best

    def _item_id_after_rebuild(self, timeline, created_item):
        """Unique ID (positional label only on older builds) after rebuilding."""
        unique = call(created_item, "GetUniqueId", None)
        for item, info in self._timeline_items(timeline):
            if (unique is not None and str(info.get("unique_id")) == str(unique)) or item == created_item:
                return info.get("id")
        return unique

    def _walk_media(self, folder, prefix="", limit=2000):
        output = []
        folder_name = call(folder, "GetName", "Media Pool")
        location = (prefix + "/" + folder_name).strip("/")
        for clip in call(folder, "GetClipList", []) or []:
            props = call(clip, "GetClipProperty", {}) or {}
            output.append({
                "id": call(clip, "GetUniqueId", None),
                "name": call(clip, "GetName", props.get("Clip Name", "")),
                "bin": location,
                "file_path": props.get("File Path"),
                "duration": props.get("Duration"),
                "frames": props.get("Frames"),
                "resolution": props.get("Resolution"),
                "type": props.get("Type"),
                "fps": props.get("FPS"),
            })
            if len(output) >= limit:
                return output
        for child in call(folder, "GetSubFolderList", []) or []:
            remaining = max(0, limit - len(output))
            if not remaining:
                break
            output.extend(self._walk_media(child, location, remaining))
        return output

    def _find_media_items(self, identifiers):
        wanted = [str(value) for value in identifiers]
        found = []

        def visit(folder):
            for clip in call(folder, "GetClipList", []) or []:
                if str(call(clip, "GetUniqueId", "")) in wanted or str(call(clip, "GetName", "")) in wanted:
                    found.append(clip)
            for child in call(folder, "GetSubFolderList", []) or []:
                visit(child)

        visit(self._media_pool().GetRootFolder())
        if len(found) < len(wanted):
            raise OperationError(
                "One or more media items were not found. Call list_media and use unique ids."
            )
        return found

    def _ensure_video_track(self, timeline, track_index, allow_create=True):
        """Make sure video track ``track_index`` exists, adding tracks if allowed."""
        if track_index is None:
            return None
        wanted = int(track_index)
        if wanted < 1:
            raise OperationError("track_index must be 1 or greater.")
        count = int(call(timeline, "GetTrackCount", 0, "video") or 0)
        if wanted <= count:
            return wanted
        if not allow_create:
            raise OperationError(
                "Video track V%d does not exist. The timeline has %d video track(s)."
                % (wanted, count)
            )
        for _ in range(wanted - count):
            if not call(timeline, "AddTrack", False, "video"):
                raise OperationError(
                    "Resolve refused to add a video track. The timeline has %d video track(s)."
                    % int(call(timeline, "GetTrackCount", 0, "video") or 0)
                )
        return wanted

    def _apply_transform(self, item, params, verify=False):
        """Apply transforms; reconstruction also verifies the resulting values."""
        applied = {}
        rejected = {}
        width, height = self._project_resolution()

        requests = []
        if params.get("pan_percent") is not None:
            requests.append(("Pan", float(params["pan_percent"]) / 100.0 * width))
        if params.get("tilt_percent") is not None:
            requests.append(("Tilt", float(params["tilt_percent"]) / 100.0 * height))
        zoom = params.get("zoom")
        if zoom is not None:
            requests.append(("ZoomX", float(zoom)))
            requests.append(("ZoomY", float(zoom)))
        for name, key in TRANSFORM_FLOATS.items():
            if params.get(name) is not None:
                requests.append((key, float(params[name])))
        for name, key in TRANSFORM_BOOLS.items():
            if params.get(name) is not None:
                requests.append((key, bool(params[name])))
        mode = params.get("composite_mode")
        if mode is not None:
            if isinstance(mode, str):
                resolved = COMPOSITE_MODES.get(mode.strip().lower().replace(" ", "_"))
                if resolved is None:
                    raise OperationError(
                        "Unknown composite_mode '%s'. Valid names: %s"
                        % (mode, ", ".join(sorted(COMPOSITE_MODES)))
                    )
                requests.append(("CompositeMode", resolved))
            else:
                requests.append(("CompositeMode", int(mode)))

        for name, (key, table) in TRANSFORM_ENUMS.items():
            value = params.get(name)
            if value is None:
                continue
            if isinstance(value, str):
                resolved = table.get(value.strip().lower().replace(" ", "_"))
                if resolved is None:
                    raise OperationError(
                        "Unknown %s '%s'. Valid names: %s"
                        % (name, value, ", ".join(sorted(table)))
                    )
                requests.append((key, resolved))
            else:
                requests.append((key, int(value)))

        for key, value in requests:
            accepted = call(item, "SetProperty", False, key, value)
            if verify:
                # Some builds expose read-only defaults such as AnchorPointX/Y.
                # An already matching value is preserved even if its setter is
                # rejected; a reported success with a different value is not.
                current = call(item, "GetProperty", None, key)
                accepted = current == value
                if isinstance(current, (int, float)) and isinstance(value, float):
                    accepted = math.isclose(current, value, rel_tol=1e-7, abs_tol=1e-7)
            if accepted:
                applied[key] = value
            else:
                rejected[key] = value
        return applied, rejected

    def _read_transform(self, item):
        values = {}
        snapshot = call(item, "GetProperty", None)
        if isinstance(snapshot, dict):
            for key in READABLE_TRANSFORM_KEYS:
                if key in snapshot:
                    values[key] = plain(snapshot[key])
            if values:
                return values
        for key in READABLE_TRANSFORM_KEYS:
            value = call(item, "GetProperty", None, key)
            if value is not None:
                values[key] = plain(value)
        return values

    def _newest_item_on_track(self, timeline, track_index):
        items = self._track_clips(timeline, "video", track_index)
        if not items:
            return None, None
        index = len(items)
        return items[-1], self._item_summary(items[-1], "video", track_index, index)

    # ----------------------------------------------------------- operations

    def _op_status(self, _params):
        instance = self._provider()
        manager = instance.GetProjectManager() if instance is not None else None
        project = manager.GetCurrentProject() if manager else None
        timeline = project.GetCurrentTimeline() if project else None
        return {
            "online": instance is not None,
            "transport": self.transport,
            "agent_version": AGENT_VERSION,
            "protocol": PROTOCOL_VERSION,
            "resolve_version": call(instance, "GetVersionString", None),
            "product": call(instance, "GetProductName", None),
            "page": call(instance, "GetCurrentPage", None),
            "project": call(project, "GetName", None) if project else None,
            "timeline": call(timeline, "GetName", None) if timeline else None,
            "token_id": self.token_id,
        }

    def _op_project_info(self, _params):
        project = self._project()
        timeline = project.GetCurrentTimeline()
        width, height = self._project_resolution()
        return {
            "name": project.GetName(),
            "timeline": call(timeline, "GetName", None) if timeline else None,
            "timeline_count": call(project, "GetTimelineCount", 0),
            "frame_rate": self._project_rate(),
            "resolution_width": width,
            "resolution_height": height,
        }

    def _op_list_timelines(self, _params):
        project = self._project()
        current = project.GetCurrentTimeline()
        timelines = []
        for index in range(1, int(project.GetTimelineCount() or 0) + 1):
            item = project.GetTimelineByIndex(index)
            timelines.append({
                "id": call(item, "GetUniqueId", None),
                "index": index,
                "name": call(item, "GetName", ""),
                "current": self._same_timeline(item, current),
                "start_frame": call(item, "GetStartFrame", None),
                "end_frame": call(item, "GetEndFrame", None),
            })
        return {"timelines": timelines}

    def _op_open_timeline(self, params):
        project = self._project()
        name = params.get("name")
        index = params.get("index")
        timeline_id = params.get("timeline_id")
        if sum((bool(name), index is not None, bool(timeline_id))) != 1:
            raise OperationError("Pass exactly one of name, index, or timeline_id.")
        matches = []
        for item_index in range(1, int(project.GetTimelineCount() or 0) + 1):
            item = project.GetTimelineByIndex(item_index)
            if (index is not None and int(index) == item_index) or (name and item.GetName() == name) or (timeline_id and call(item, "GetUniqueId", None) == timeline_id):
                matches.append(item)
        if not matches:
            raise OperationError("Timeline not found. Call list_timelines for valid names and indexes.")
        if len(matches) != 1:
            raise OperationError("Timeline name is ambiguous. Use a unique ID or index from list_timelines.")
        selected = matches[0]
        if not self._activate_timeline(project, selected):
            raise OperationError("Could not verify that Resolve opened the requested timeline.")
        return {"opened": selected.GetName()}

    COMPACT_KEYS = ("id", "label", "name", "start", "end")

    def _item_detail(self, item, summary, transitions):
        """Extra per-clip facts for detail='full': source range, media rate, volume, Fusion, edge transitions."""
        media = call(item, "GetMediaPoolItem", None)
        source = call(item, "GetSourceStartFrame", None)
        extra = {"src_in": source, "src_out": call(item, "GetSourceEndFrame", None),
                 "media_fps": call(media, "GetClipProperty", None, "FPS") if media is not None else None,
                 "fusion_comps": call(item, "GetFusionCompCount", 0)}
        if summary["track_type"] == "audio":
            extra["audio_volume"] = plain(call(item, "GetProperty", None, "AudioVolume"))
        track = "%s%d" % (summary["label"][0], summary["track_index"])
        start, end = float(summary["start"] or 0), float(summary["end"] or 0)
        extra["transition_in"] = next((t["name"] for t in transitions if t["track"] == track
                                       and t["start_frame"] <= start < t["end_frame"]), None)
        extra["transition_out"] = next((t["name"] for t in transitions if t["track"] == track and t["start_frame"] < end <= t["end_frame"]), None)
        return extra

    def _op_timeline_overview(self, params):
        timeline = self._timeline()
        max_items = max(1, min(int(params.get("max_items", 500) or 500), 2000))
        detail = str(params.get("detail") or "standard").lower()
        if detail not in ("compact", "standard", "full"):
            raise OperationError("detail must be compact, standard or full.")
        tracks, clips, transitions = [], [], []
        for track_type in ("video", "audio", "subtitle"):
            count = int(call(timeline, "GetTrackCount", 0, track_type) or 0)
            for track_index in range(1, count + 1):
                items, found = self._track_split(timeline, track_type, track_index)
                transitions += found
                tracks.append({
                    "type": track_type,
                    "index": track_index,
                    "name": call(timeline, "GetTrackName", "", track_type, track_index),
                    "enabled": call(timeline, "GetIsTrackEnabled", None, track_type, track_index),
                    "items": len(items),
                })
                for item_index, item in enumerate(items, 1):
                    if len(clips) < max_items:
                        summary = self._item_summary(item, track_type, track_index, item_index)
                        if detail == "full":
                            summary.update(self._item_detail(item, summary, transitions))
                        elif detail == "compact":
                            compact = {key: summary[key] for key in self.COMPACT_KEYS}
                            if summary.get("enabled") is False:
                                compact["enabled"] = False
                            if summary.get("color"):
                                compact["color"] = summary["color"]
                            summary = compact
                        clips.append(summary)
        return {
            "detail": detail,
            "transitions": transitions,
            "name": timeline.GetName(),
            "id": call(timeline, "GetUniqueId", None),
            "item_id_note": "id uses Resolve's unique ID when available; label (V1.2) is positional and can change after edits.",
            "start_frame": call(timeline, "GetStartFrame", None),
            "end_frame": call(timeline, "GetEndFrame", None),
            "current_timecode": call(timeline, "GetCurrentTimecode", None),
            "frame_rate": self._project_rate(),
            "tracks": tracks,
            "clips": clips,
            "clips_truncated": sum(track["items"] for track in tracks) > len(clips),
            "markers": plain(call(timeline, "GetMarkers", {}) or {}),
        }

    def _op_list_media(self, params):
        limit = max(1, min(int(params.get("limit", 1000) or 1000), 5000))
        query = str(params.get("query") or "").strip().lower()
        kind = str(params.get("kind") or "all").strip().lower()
        if kind not in ("all", "clips", "timelines"):
            raise OperationError('kind must be "all", "clips" (media, no timelines) or "timelines".')
        if not query and kind == "all":
            items = self._walk_media(self._media_pool().GetRootFolder(), "", limit)
            return {"items": items, "count": len(items), "limited_to": limit}
        items = self._walk_media(self._media_pool().GetRootFolder(), "", 20000)
        if kind != "all":
            items = [i for i in items if (str(i.get("type") or "") == "Timeline") == (kind == "timelines")]
        if query:
            def rank(item):
                name = str(item.get("name") or "").lower()
                path = str(item.get("file_path") or "").lower()
                if name == query or Path(path).name == query:
                    return 0
                if name.startswith(query):
                    return 1
                return 2 if query in name or query in path else None
            items = sorted((i for i in items if rank(i) is not None), key=rank)
        matched = len(items)
        result = {"items": items[:limit], "count": min(matched, limit), "matched": matched, "limited_to": limit,
                  "query": query or None, "kind": kind}
        files = {}
        for item in items:
            if item.get("file_path"):
                files.setdefault(item["file_path"], []).append(item["id"])
        repeated = {path: ids for path, ids in files.items() if len(ids) > 1}
        if repeated:
            result["same_file_entries"] = repeated
            result["note"] = ("Some files are in the pool more than once (same_file_entries); any of those ids places "
                              "the same media.")
        return result

    def _op_import_media(self, params):
        paths = [_absolute(value) for value in params.get("paths", [])]
        if not paths:
            raise OperationError("paths must contain at least one absolute media file path.")
        missing = [path for path in paths if not Path(path).exists()]
        if missing:
            raise OperationError("Media file not found: %s" % ", ".join(missing))
        imported = self._media_pool().ImportMedia(paths) or []
        if not imported:
            raise OperationError(
                "Resolve imported none of the requested files. Confirm the format is supported "
                "and that a project is open."
            )
        return {
            "imported": [
                {
                    "id": call(item, "GetUniqueId", None),
                    "name": call(item, "GetName", ""),
                    "frames": (call(item, "GetClipProperty", {}) or {}).get("Frames"),
                }
                for item in imported
            ],
            "requested": len(paths),
            "imported_count": len(imported),
        }

    def _op_append_media(self, params):
        media_ids = params.get("media_ids") or []
        paths = params.get("paths") or []
        items = None
        if paths:
            absolute = [_absolute(value) for value in paths]
            missing = [path for path in absolute if not Path(path).exists()]
            if missing:
                raise OperationError("Media file not found: %s" % ", ".join(missing))
            items = list(self._media_pool().ImportMedia(absolute) or [])
            if not items:
                raise OperationError("Resolve did not import any of the requested media files.")
        if items is None and not media_ids:
            raise OperationError(
                "Nothing to append. Pass either media_ids (unique ids from list_media, for clips "
                "already in the Media Pool) or paths (absolute file paths to import and append). "
                "For a still image, use add_image instead so its duration is set."
            )
        if items is None:
            items = self._find_media_items(media_ids)
        alpha = {}
        if params.get("alpha_mode"):  # e.g. "Premultiplied" for transparent overlays (captions).
            for item in items:
                alpha[call(item, "GetName", "")] = bool(call(item, "SetClipProperty", False, "Alpha mode", str(params["alpha_mode"])))

        timeline = self._timeline()
        # Resolve interprets mediaType=1 as VIDEO ONLY. Leave it out by default
        # so positioning an AV clip does not silently discard its audio.
        media_type = params.get("media_type")
        requested_type = params.get("track_type")
        if media_type is None and requested_type is not None:
            if str(requested_type).lower() not in ("audio", "video"):
                raise OperationError("track_type must be audio or video.")
            media_type = 2 if str(requested_type).lower() == "audio" else 1
        if media_type is not None:
            if media_type not in (1, 2):
                raise OperationError("media_type must be 1 (video only), 2 (audio only), or omitted for both.")
            media_type = int(media_type)
        track_type = "audio" if media_type == 2 else "video"
        track_index = params.get("track_index")
        if params.get("new_top_track") and track_type == "video":  # Overlays go above everything.
            track_index = int(call(timeline, "GetTrackCount", 0, "video") or 0) + 1
        if track_index is not None:
            track_index = int(track_index)
            if track_index < 1:
                raise OperationError("track_index must be at least 1.")
            if track_type == "audio":
                count = int(call(timeline, "GetTrackCount", 0, "audio") or 0)
                if track_index > count:
                    if not params.get("create_track", True):
                        raise OperationError("Audio track A%d does not exist and create_track is false." % track_index)
                    for _ in range(track_index - count):
                        if not call(timeline, "AddTrack", False, "audio"):
                            raise OperationError("Resolve refused to create the requested audio track.")
            else:
                track_index = self._ensure_video_track(
                    timeline, track_index, bool(params.get("create_track", True))
                )
        record_frame = params.get("record_frame")

        payload = items
        if (track_index is not None or record_frame is not None or media_type is not None
                or params.get("start_frame") is not None or params.get("end_frame") is not None):
            payload = []
            for item in items:
                clip_info = {"mediaPoolItem": item}
                if track_index is not None:
                    clip_info["trackIndex"] = int(track_index)
                if record_frame is not None:
                    clip_info["recordFrame"] = int(record_frame)
                if media_type is not None:
                    clip_info["mediaType"] = media_type
                frames = (call(item, "GetClipProperty", {}) or {}).get("Frames")
                try:
                    total = int(frames)
                except (TypeError, ValueError):
                    total = 0
                sf = int(params.get("start_frame", 0) or 0)
                ef = params.get("end_frame")
                if ef is not None:
                    ef = int(ef)
                    sub = call(item, "CreateSubClip", None, f"sub_{sf}_{ef}", sf, ef)
                    if sub is not None:
                        clip_info["mediaPoolItem"] = sub
                    else:
                        clip_info.update({"startFrame": sf, "endFrame": ef + 1})
                elif total > 1:
                    # AppendToTimeline's source end is exclusive (live checked
                    # in both Python and Lua on macOS Free 21.0.3.7).
                    ef = total
                    clip_info.update({"startFrame": sf, "endFrame": ef})
                payload.append(clip_info)

        created = self._media_pool().AppendToTimeline(payload) or []
        if not created:
            raise OperationError(
                "Resolve appended nothing. A still image needs add_image, and an explicit "
                "record_frame must land on empty space on that track."
            )
        result = {
            "appended_count": len(created),
            "requested": len(items),
            "track_index": track_index,
            "record_frame": record_frame,
        }
        if alpha:
            result["alpha_mode_set"] = alpha
        return result

    def _op_add_image(self, params):
        """Import one still image and place it on the timeline with a real duration."""
        raw_path = str(params.get("path", "")).strip()
        if not raw_path:
            raise OperationError("path is required and must be an absolute image file path.")
        path = _absolute(raw_path)
        if not Path(path).exists():
            raise OperationError("Image file not found: %s" % path)
        suffix = Path(path).suffix.lower()

        timeline = self._timeline()
        fps = self._project_rate()
        duration_frames = params.get("duration_frames")
        if duration_frames is None:
            seconds = params.get("duration_seconds")
            seconds = 5.0 if seconds is None else float(seconds)
            duration_frames = int(round(seconds * fps))
        duration_frames = max(1, int(duration_frames))

        track_index = self._ensure_video_track(
            timeline,
            params.get("track_index") or 1,
            bool(params.get("create_track", True)),
        )
        record_frame = params.get("record_frame")
        if record_frame is None and params.get("at_playhead"):
            record_frame = (
                int(call(timeline, "GetStartFrame", 0) or 0) + self._current_marker_frame(timeline)
            )

        pool = self._media_pool()
        existing = None
        for candidate in self._walk_media(pool.GetRootFolder(), "", 5000):
            if candidate.get("file_path") and _absolute(candidate["file_path"]) == path:
                existing = candidate.get("id")
                break
        if existing:
            media_item = self._find_media_items([existing])[0]
            reused = True
        else:
            imported = pool.ImportMedia([path]) or []
            if not imported:
                raise OperationError(
                    "Resolve could not import %s. Confirm the file is a supported image format." % path
                )
            media_item = imported[0]
            reused = False

        available = (call(media_item, "GetClipProperty", {}) or {}).get("Frames")
        try:
            available_frames = int(available)
        except (TypeError, ValueError):
            available_frames = 0

        # Verified on Resolve 21.2 Free: AppendToTimeline ignores endFrame for a still (it takes the Preferences
        # "standard still duration"), but marks on the media pool item set the length exactly, beyond 5 s too.
        created, fallback_used, marks = [], False, {"method": None}
        previous_marks = plain(call(media_item, "GetMarkInOut", None))
        if call(media_item, "SetMarkInOut", False, 0, duration_frames - 1, "all"):
            marks["method"] = "mark in/out"
            info = {"mediaPoolItem": media_item, "trackIndex": int(track_index)}
            if record_frame is not None:
                info["recordFrame"] = int(record_frame)
            try:
                created = pool.AppendToTimeline([info]) or []
            finally:
                marks.update(self._restore_marks(media_item, previous_marks))
        clip_info = {
            "mediaPoolItem": media_item,
            "startFrame": 0,
            "endFrame": duration_frames,
            "trackIndex": int(track_index),
        }
        if record_frame is not None:
            clip_info["recordFrame"] = int(record_frame)

        if not created:
            created = pool.AppendToTimeline([clip_info]) or []
        if not created and available_frames > 1:
            # Some builds refuse an endFrame beyond the still's reported length.
            clip_info["endFrame"] = min(duration_frames, available_frames)
            created = pool.AppendToTimeline([clip_info]) or []
            fallback_used = bool(created)
        if not created:
            clip_info.pop("startFrame", None)
            clip_info.pop("endFrame", None)
            created = pool.AppendToTimeline([clip_info]) or []
            fallback_used = bool(created)
        if not created:
            raise OperationError(
                "Resolve placed nothing on V%d. If record_frame was given, that space may already "
                "be occupied. Try another record_frame, another track_index, or omit both to append "
                "at the end of the track." % track_index
            )

        item = created[0]
        actual = call(item, "GetDuration", None)
        applied, rejected = self._apply_transform(item, params)
        result = {
            "item": self._item_id_after_rebuild(timeline, item),
            "name": call(item, "GetName", ""),
            "media_pool_id": call(media_item, "GetUniqueId", None),
            "reused_existing_media": reused,
            "is_recognised_image_format": suffix in IMAGE_SUFFIXES,
            "track_index": track_index,
            "record_frame": record_frame,
            "start": call(item, "GetStart", None),
            "requested_duration_frames": duration_frames,
            "actual_duration_frames": actual,
            "frame_rate": fps,
            "transform_applied": applied,
            "length_method": marks["method"] if marks["method"] else "AppendToTimeline endFrame",
        }
        if marks.get("marks_restored") is not None:
            result["media_marks_restored"] = marks["marks_restored"]
            if marks.get("marks_note"):
                result["media_marks_note"] = marks["marks_note"]
        if rejected:
            result["transform_rejected"] = rejected
        notes = []
        if fallback_used:
            notes.append(
                "Resolve would not honour the exact requested length, so a shorter or default "
                "still duration was used."
            )
        try:
            if actual is not None and int(actual) != duration_frames:
                notes.append(
                    "Requested %d frames, Resolve created %d. set_clip_length(item_id, frames=%d) can fix "
                    "it in place." % (duration_frames, int(actual), duration_frames)
                )
        except (TypeError, ValueError):
            pass
        if notes:
            result["notes"] = notes
        return result

    @staticmethod
    def _restore_marks(media_item, previous):
        """Put a media pool item's In/Out marks back after a temporary SetMarkInOut(..., "all").

        Only "all" marks work on Resolve 21.2 Free ("video"/"audio" are broken), so differing video and audio marks
        cannot be restored exactly; that is reported, never hidden.
        """
        marks = []
        if isinstance(previous, dict):
            for kind in ("video", "audio"):
                entry = previous.get(kind)
                if isinstance(entry, dict) and entry.get("in") is not None and entry.get("out") is not None:
                    marks.append((kind, entry["in"], entry["out"]))
        if not marks:
            return {"marks_restored": bool(call(media_item, "ClearMarkInOut", False, "all"))}
        spans = {(m[1], m[2]) for m in marks}
        if len(spans) == 1:
            first, last = spans.pop()
            return {"marks_restored": bool(call(media_item, "SetMarkInOut", False, first, last, "all"))}
        call(media_item, "ClearMarkInOut", False, "all")
        return {"marks_restored": False,
                "marks_note": "This still had different video and audio In/Out marks (%s); Resolve 21.2 Free can only set "
                              "both at once, so they were cleared. Re-mark it in the media pool if they mattered."
                              % ", ".join("%s %s-%s" % m for m in marks)}

    def _op_set_clip_transform(self, params):
        item, info = self._resolve_one_item(params.get("item_id"), params.get("track_index"))
        applied, rejected = self._apply_transform(item, params)
        if not applied and not rejected:
            raise OperationError(
                "No transform values were supplied. Pass at least one of pan, tilt, pan_percent, "
                "tilt_percent, zoom, zoom_x, zoom_y, rotation, opacity, composite_mode, flip_x, "
                "flip_y, a crop value, or a mode such as scaling, resize_filter, dynamic_zoom_ease, "
                "retime_process, or motion_estimation."
            )
        result = {"item": info["id"], "applied": applied, "current": self._read_transform(item)}
        if rejected:
            result["rejected"] = rejected
            result["note"] = (
                "Resolve rejected the listed keys. Confirm they are supported on this clip type "
                "and Resolve version."
            )
        return result

    def _op_get_clip_transform(self, params):
        item, info = self._resolve_one_item(params.get("item_id"), params.get("track_index"))
        width, height = self._project_resolution()
        return {
            "item": info["id"],
            "timeline_resolution": {"width": width, "height": height},
            "transform": self._read_transform(item),
        }

    def _op_split_clip(self, params):
        """Rebuild a normal-speed clip, keeping a verified timeline checkpoint."""
        timeline = self._timeline()
        item, info = self._resolve_one_item(params.get("item_id"), params.get("track_index"))
        if info.get("track_type") not in ("video", "audio"):
            raise OperationError("Only normal-speed media clips can be split.")
        if call(item, "GetFusionCompCount", 0):
            raise OperationError("This clip has Fusion effects. Rebuilding would lose them; split it in Resolve instead.")
        if call(timeline, "GetIsTrackLocked", False, info["track_type"], info["track_index"]):
            raise OperationError("The clip's track is locked.")
        start, end = float(info["start"]), float(info["end"])
        if not start.is_integer() or not end.is_integer():
            raise OperationError("Subframe audio boundaries cannot be rebuilt safely by this tool.")
        start, end = int(start), int(end)
        frame = params.get("frame")
        if frame is not None and params.get("timecode"):
            raise OperationError("Pass frame or timecode, not both.")
        if frame is None and params.get("timecode"):
            fps = self._project_rate()
            frame = int(timeline.GetStartFrame()) + self._tc_frames(params["timecode"], fps) - self._tc_frames(timeline.GetStartTimecode(), fps)
        frame = self._playhead_frame(timeline) if frame is None else int(frame)
        if not start < frame < end:
            raise OperationError("Cut frame must be strictly inside the clip's [%d, %d) range." % (start, end))
        media = call(item, "GetMediaPoolItem", None)
        source_start = call(item, "GetSourceStartFrame", None)
        if media is None or source_start is None:
            raise OperationError("Resolve did not provide a media source range; nothing was changed.")
        # Source extraction's range validation also catches detectable retiming.
        self._source_window(item, start)
        source_fps = call(media, "GetClipProperty", None, "FPS")
        try:
            if source_fps and abs(float(source_fps) - self._project_rate()) > 0.01:
                raise OperationError("Mixed source/timeline frame rates cannot be split safely by reconstruction. Use Resolve's razor.")
        except (TypeError, ValueError):
            raise OperationError("Source frame rate could not be verified.")
        checkpoint, checkpoint_info = self._duplicate_current("Before split", open_duplicate=False)
        checkpoint_items = self._track_clips(checkpoint, info["track_type"], info["track_index"])
        # The checkpoint is verified in track order; retain the copied item for grade copying.
        original_items = self._track_clips(timeline, info["track_type"], info["track_index"])
        source_index = next((index for index, candidate in enumerate(original_items)
                             if (info.get("unique_id") is not None and call(candidate, "GetUniqueId", None) == info.get("unique_id")) or candidate == item), None)
        if source_index is None or source_index >= len(checkpoint_items):
            raise OperationError("Could not identify the checkpoint clip; original was not removed.")
        saved_item = checkpoint_items[source_index]
        transform = self._read_transform(item)
        color = call(item, "GetClipColor", "")
        enabled = call(item, "GetClipEnabled", None)
        source_start = int(source_start)
        segments = []
        for begin, finish in ((start, frame), (frame, end)):
            segments.append({"mediaPoolItem": media, "startFrame": source_start + begin - start,
                             "endFrame": source_start + finish - start, "recordFrame": begin,
                             "trackIndex": info["track_index"], "mediaType": 1 if info["track_type"] == "video" else 2})
        if not self._is_current_timeline(self._project(), timeline):
            raise OperationError("The active timeline changed before the split. Nothing was removed; checkpoint: %s." % checkpoint_info["name"])
        try:
            self._delete_timeline_items(timeline, [(item, info)])
            pool = self._media_pool()
            if not self._is_current_timeline(self._project(), timeline):
                raise OperationError("The active timeline changed before the halves could be placed.")
            created = pool.AppendToTimeline(segments) or []
            if len(created) != 2:
                raise OperationError("Resolve created %d of 2 halves." % len(created))
            for piece, segment in zip(created, segments):
                expected_end = segment["recordFrame"] + segment["endFrame"] - segment["startFrame"]
                if call(piece, "GetStart", None) != segment["recordFrame"] or call(piece, "GetEnd", None) != expected_end:
                    raise OperationError("Resolve rebuilt a half at an unexpected position or duration.")
                _, rejected = self._apply_transform(piece, transform_params_from_props(transform), verify=True)
                if rejected:
                    raise OperationError("Resolve rejected restored transforms: %s." % sorted(rejected))
                if enabled is not None and not call(piece, "SetClipEnabled", False, enabled):
                    raise OperationError("Could not restore clip enabled state.")
                if color and not call(piece, "SetClipColor", False, color):
                    raise OperationError("Could not restore clip color.")
            # CopyGrades is part of the regular Resolve API, not a Studio AI feature.
            grade_copied = bool(call(saved_item, "CopyGrades", False, created)) if info["track_type"] == "video" else None
            if info["track_type"] == "video" and not grade_copied:
                raise OperationError("Could not preserve the current color-grade layer.")
        except Exception as exc:
            reopened = self._activate_timeline(self._project(), checkpoint)
            raise OperationError("Split failed: %s. Checkpoint '%s' contains the pre-edit timeline; %s. The attempted timeline may contain partial edits and has been retained."
                                 % (exc, checkpoint_info["name"], "opened the checkpoint for recovery" if reopened else "open that checkpoint manually"))
        return {"original": info["id"], "cut_frame": frame, "checkpoint": checkpoint_info,
                "halves": [{"side": side, "id": self._item_id_after_rebuild(timeline, piece),
                            "start": call(piece, "GetStart", None), "end": call(piece, "GetEnd", None), "duration": call(piece, "GetDuration", None)}
                           for side, piece in zip(("left", "right"), created)],
                "transform_preserved": True, "current_grade_layer_copied": grade_copied,
                "note": "Checkpoint retained. Rebuilt halves preserve static transforms, enabled state, clip color and current grade layer. Other grade layers, audio links, clip markers, fades and keyframes are not guaranteed; compare with the checkpoint before continuing."}

    def _build_ease_func(self, easing_name):
        import math
        easing = str(easing_name).lower().strip()

        def ease_fn(t):
            t_clamp = max(0.0, min(1.0, t))
            if easing in ("linear", "none_linear"):
                return t_clamp
            elif easing in ("ease_in", "quad_in"):
                return t_clamp * t_clamp
            elif easing in ("cubic_in",):
                return t_clamp * t_clamp * t_clamp
            elif easing in ("ease_out", "quad_out"):
                return 1.0 - (1.0 - t_clamp) * (1.0 - t_clamp)
            elif easing in ("cubic_out", "snap_in", "punch_in"):
                return 1.0 - math.pow(1.0 - t_clamp, 3)
            elif easing in ("ease", "quad_ease", "ease_in_out", "smoothstep"):
                return 2.0 * t_clamp * t_clamp if t_clamp < 0.5 else 1.0 - math.pow(-2.0 * t_clamp + 2.0, 2) / 2.0
            elif easing in ("cubic_ease", "smootherstep", "cinematic"):
                return t_clamp * t_clamp * t_clamp * (t_clamp * (t_clamp * 6.0 - 15.0) + 10.0)
            elif easing in ("circular_ease", "circ_ease", "circular"):
                return (1.0 - math.sqrt(1.0 - math.pow(2.0 * t_clamp, 2))) / 2.0 if t_clamp < 0.5 else (math.sqrt(1.0 - math.pow(-2.0 * t_clamp + 2.0, 2)) + 1.0) / 2.0
            elif easing in ("rebound_in", "back_in", "anticipation"):
                c1 = 1.70158
                c3 = c1 + 1.0
                return c3 * t_clamp * t_clamp * t_clamp - c1 * t_clamp * t_clamp
            elif easing in ("rebound_out", "back_out", "overshoot", "pop"):
                c1 = 1.70158
                c3 = c1 + 1.0
                return 1.0 + c3 * math.pow(t_clamp - 1.0, 3) + c1 * math.pow(t_clamp - 1.0, 2)
            elif easing in ("rebound_ease", "back_ease", "back_in_out"):
                c1 = 1.70158 * 1.525
                return (math.pow(2.0 * t_clamp, 2) * ((c1 + 1.0) * 2.0 * t_clamp - c1)) / 2.0 if t_clamp < 0.5 else (math.pow(2.0 * t_clamp - 2.0, 2) * ((c1 + 1.0) * (t_clamp * 2.0 - 2.0) + c1) + 2.0) / 2.0
            elif easing in ("elastic_out", "elastic", "spring"):
                c4 = (2.0 * math.pi) / 3.0
                return 0.0 if t_clamp == 0 else (1.0 if t_clamp == 1 else math.pow(2.0, -10.0 * t_clamp) * math.sin((t_clamp * 10.0 - 0.75) * c4) + 1.0)
            elif easing in ("none", "step"):
                return 1.0 if t_clamp >= 1.0 else 0.0
            return t_clamp * t_clamp * t_clamp * (t_clamp * (t_clamp * 6.0 - 15.0) + 10.0)

        return ease_fn

    def _op_animate_zoom(self, params):
        """Animate a clip's scale and framing with a bridge-owned Fusion Transform.

        Supports standard easing curves, Zoom Out direction, multi-keyframe
        arrays and presets. The Transform joins the bridge's Fusion segment, so
        blur, spotlight and user nodes stay connected.
        """
        item, info = self._resolve_one_item(params.get("item_id"), params.get("track_index"))
        if params.get("reset"):
            return self._fusion_reset(item, info, "zoom")

        preset = str(params.get("preset", params.get("scenario", ""))).lower().strip()
        direction = str(params.get("direction", params.get("zoom_type", "in"))).lower().strip()
        raw_start_zoom = params.get("start_zoom")
        raw_end_zoom = params.get("end_zoom")
        easing = str(params.get("easing", "")).lower().strip()

        # preset -> (default start, default end, default easing)
        presets = {
            "punch_in": (1.0, 1.35, "cubic_out"), "snap_in": (1.0, 1.35, "cubic_out"),
            "pop_in": (1.0, 1.38, "rebound_out"), "rebound": (1.0, 1.38, "rebound_out"),
            "overshoot": (1.0, 1.38, "rebound_out"),
            "slow_push": (1.0, 1.15, "linear"), "ken_burns": (1.0, 1.15, "linear"),
            "dramatic": (1.0, 1.60, "cubic_in"), "build_up": (1.0, 1.60, "cubic_in"),
            "reveal": (1.40, 1.0, "cubic_out"), "zoom_out": (1.40, 1.0, "cubic_out"),
            "cinematic": (1.0, 1.25, "cubic_ease"), "glide": (1.0, 1.25, "cubic_ease"),
        }
        if preset in presets:
            default_start, default_end, default_easing = presets[preset]
        elif direction in ("out", "zoom_out"):
            default_start, default_end, default_easing = 1.40, 1.0, "cubic_out"
        else:
            default_start, default_end, default_easing = 1.0, 1.5, "smootherstep"
        start_zoom = float(raw_start_zoom if raw_start_zoom is not None else default_start)
        end_zoom = float(raw_end_zoom if raw_end_zoom is not None else default_end)
        easing = easing or default_easing
        if direction in ("out", "zoom_out") and start_zoom < end_zoom:
            start_zoom, end_zoom = end_zoom, start_zoom

        start_cx = float(params.get("start_center_x", params.get("center_x", 0.5)))
        start_cy = float(params.get("start_center_y", params.get("center_y", 0.5)))
        end_cx = float(params.get("target_center_x", params.get("target_x", 0.5)))
        end_cy = float(params.get("target_center_y", params.get("target_y", 0.5)))

        raw_keyframes = params.get("keyframes")
        if isinstance(raw_keyframes, list) and len(raw_keyframes) >= 2:
            pairs = self._keyframe_offsets(item, raw_keyframes)
            series = self._sample_keyframes(pairs, {
                "size": lambda k: float(k.get("zoom", k.get("size", 1.0))),
                "x": lambda k: float(k.get("center_x", k.get("cx", 0.5))),
                "y": lambda k: float(k.get("center_y", k.get("cy", 0.5))),
            })
            start_frame, end_frame = pairs[0][0], pairs[-1][0]
            start_zoom, end_zoom = series["size"][0][1], series["size"][-1][1]
            easing = "multi_segment_keyframes"
            animate_center = True
        else:
            start_frame, end_frame = self._effect_range(item, params)
            series = {"size": self._sample_range(start_frame, end_frame, start_zoom, end_zoom, easing)}
            animate_center = any(abs(v - 0.5) > 1e-9 for v in (start_cx, start_cy, end_cx, end_cy))
            if animate_center:
                series["x"] = self._sample_range(start_frame, end_frame, start_cx, end_cx, easing)
                series["y"] = self._sample_range(start_frame, end_frame, start_cy, end_cy, easing)
        if min(value for _, value in series["size"]) <= 0:
            raise OperationError("Zoom values must be greater than 0 (1.0 is original size).")

        comp = self._fusion_comp(item, info)
        _comp_open(comp)
        edit = FusionEdit(self, comp, "zoom")
        try:
            node = edit.node("main", ("Transform",), "AIBridgeZoom")
            lines = [edit.bind("zoom", node)]
            lines += edit.animate("zoom", "Size", series["size"])
            if animate_center:
                lines += edit.animate_point("zoom", "Center", series["x"], series["y"])
            else:
                lines += edit.static_point("zoom", "Center", 0.5, 0.5)
            lua_ok = edit.lua(lines)
            fallback = False
            if lua_ok is False:
                # Keep the honest static fallback: a scale that holds, not a claimed animation.
                call(node, "ConnectInput", None, "Size", None)
                call(node, "SetInput", None, "Size", end_zoom)
                fallback = edit.check(node, "Size", end_frame, end_zoom) is True
            chain = edit.finish()
        except Exception:
            edit.rollback()
            raise
        finally:
            _comp_close(comp)

        verified = edit.check(node, "Size", end_frame, end_zoom) if lua_ok is not False else False
        result = {
            "item": info["id"],
            "node": edit.name(node),
            # None: this build reported neither the script's end nor the value.
            "keyframes_created": lua_ok if lua_ok is not None else verified,
            "keyframes_verified": verified,
            "start_zoom": start_zoom,
            "end_zoom": end_zoom,
            "easing": easing,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "frames": "clip offsets (0 = first frame of the clip)",
            "fusion_start_frame": edit.t0,
            "fusion_chain": chain,
        }
        if lua_ok is False:
            result["note"] = (
                "Fusion did not finish the keyframe script on this build. %s Open the clip on the "
                "Fusion page to animate it, or use set_clip_transform for a static scale."
                % ("A static Size of %.3f was applied instead." % end_zoom if fallback else "No zoom was applied.")
            )
        elif verified is None:
            result["note"] = ("Keyframes were sent, but this build did not let the bridge read the Size back. "
                              "Check the clip on the Fusion page or with inspect_fusion.")
        elif not verified:
            result["note"] = ("Size reads back differently from the requested end zoom. "
                              "Inspect the clip with inspect_fusion before continuing.")
        else:
            result["note"] = "%s zoom on %s, clip frames %d to %d." % (easing, info["id"], start_frame, end_frame)
        return result

    def _op_add_track(self, params):
        timeline = self._timeline()
        track_type = str(params.get("track_type", "video")).strip().lower()
        if track_type not in ("video", "audio", "subtitle"):
            raise OperationError("track_type must be video, audio, or subtitle.")
        count = max(1, min(int(params.get("count", 1) or 1), 8))
        added = 0
        for _ in range(count):
            if call(timeline, "AddTrack", False, track_type):
                added += 1
            else:
                break
        if not added:
            raise OperationError("Resolve refused to add a %s track." % track_type)
        return {
            "track_type": track_type,
            "added": added,
            "total": call(timeline, "GetTrackCount", 0, track_type),
        }

    def _op_set_track_name(self, params):
        timeline = self._timeline()
        track_type = str(params.get("track_type", "video")).strip().lower()
        index = int(params.get("track_index", 1))
        name = str(params.get("name", "")).strip()
        if not name:
            raise OperationError("name is required.")
        if not call(timeline, "SetTrackName", False, track_type, index, name):
            raise OperationError("Resolve refused to rename %s track %d." % (track_type, index))
        return {"track_type": track_type, "track_index": index, "name": name}

    def _op_insert_title(self, params):
        """Insert a title at the playhead. Text setting is best effort."""
        timeline = self._timeline()
        title = str(params.get("title_name", "") or "Text+").strip()
        item = None
        used = None
        for method in ("InsertFusionTitleIntoTimeline", "InsertTitleIntoTimeline"):
            function = getattr(timeline, method, None)
            if not callable(function):
                continue
            try:
                candidate = function(title)
            except Exception:
                candidate = None
            if candidate:
                item, used = candidate, method
                break
        if item is None:
            raise OperationError(
                "Resolve did not insert a title named '%s'. Use the exact name shown in the Edit "
                "page Titles list, for example 'Text+' or 'Text'. This depends on the Resolve "
                "version and installed title templates." % title
            )

        text = params.get("text")
        text_set = None
        if text is not None:
            text_set = self._set_title_text(item, str(text))
        applied, rejected = self._apply_transform(item, params)
        result = {
            "inserted": call(item, "GetName", title),
            "method": used,
            "start": call(item, "GetStart", None),
            "duration": call(item, "GetDuration", None),
            "text_set": text_set,
            "transform_applied": applied,
        }
        if rejected:
            result["transform_rejected"] = rejected
        if text is not None and not text_set:
            result["note"] = (
                "The title was inserted but its text could not be set through the scripting API. "
                "Type the text in the Inspector, or render the text with Remotion instead."
            )
        return result

    def _set_title_text(self, item, text):
        count = call(item, "GetFusionCompCount", 0) or 0
        for index in range(1, int(count) + 1):
            comp = call(item, "GetFusionCompByIndex", None, index)
            if comp is None:
                continue
            tools = call(comp, "GetToolList", None, False) or {}
            candidates = tools.values() if isinstance(tools, dict) else list(tools)
            for tool in candidates:
                for attribute in ("StyledText", "Text"):
                    current = call(tool, "GetInput", None, attribute)
                    if isinstance(current, str):
                        if current != text:
                            # Explicit setters work through the Lua proxy, where
                            # assigning a Python attribute is not supported.
                            call(tool, "SetInput", None, attribute, text)
                        if call(tool, "GetInput", None, attribute) == text:
                            return True
                    else:
                        # Some native Python wrappers only expose a string property.
                        try:
                            if isinstance(getattr(tool, attribute, None), str):
                                setattr(tool, attribute, text)
                                if getattr(tool, attribute, None) == text:
                                    return True
                        except Exception:
                            continue
        return False

    # timelineFrameRate strings Resolve offers. Live on 21.2 Free only "30" was accepted (and only on an empty
    # timeline with useCustomSettings=1); "29.97", "29.97 DF", 29.97 and "29.970" were rejected. Every value is
    # therefore set and READ BACK, never assumed.
    TIMELINE_FPS = ("23.976", "24", "25", "29.97", "30", "47.952", "48", "50", "59.94", "60")

    def _dominant_media_fps(self, limit=2000):
        rates = {}
        for entry in self._walk_media(self._media_pool().GetRootFolder(), "", limit):
            if "video" not in str(entry.get("type") or "").lower():
                continue
            try:
                rate = round(float(entry.get("fps")), 3)
            except (TypeError, ValueError):
                continue
            rates[rate] = rates.get(rate, 0) + 1
        return max(rates, key=rates.get) if rates else None, rates

    def _op_create_timeline(self, params):
        name = str(params.get("name", "")).strip()
        if not name:
            raise OperationError("name is required.")
        fps = params.get("fps")
        if fps is not None:
            fps = str(fps).strip()
            if fps not in self.TIMELINE_FPS:
                raise OperationError("fps must be one of %s (as a string). Nothing was created." % ", ".join(self.TIMELINE_FPS))
        size = [params.get("width"), params.get("height")]
        if any(v is not None for v in size) and not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size):
            raise OperationError("width and height must both be positive whole numbers. Nothing was created.")
        timeline = self._media_pool().CreateEmptyTimeline(name)
        if not timeline:
            raise OperationError(
                "Resolve could not create the timeline. The name may already be in use."
            )
        if isinstance(timeline, bool):
            return {"created": None, "active": None, "note": "Resolve did not identify the new timeline. Inspect list_timelines before editing or retrying."}
        result = {"created": timeline.GetName(), "id": call(timeline, "GetUniqueId", None),
                  "active": self._activate_timeline(self._project(), timeline),
                  "bin": self._file_into_bin(timeline, getattr(self, "_bin", self.AGENT_BIN))}
        notes = []
        if fps is not None or size[0] is not None:
            # The new timeline is empty here, which is the only state in which Free accepts a rate change.
            settings = {}
            custom = call(timeline, "SetSetting", False, "useCustomSettings", "1")
            if fps is not None:
                settings["timelineFrameRate"] = fps
            if size[0] is not None:
                settings.update(timelineResolutionWidth=str(size[0]), timelineResolutionHeight=str(size[1]))
            applied = {}
            for key, value in settings.items():
                returned = call(timeline, "SetSetting", False, key, value)
                back = call(timeline, "GetSetting", None, key)
                same = back is not None and (str(back) == value or self._same_value(back, value))
                applied[key] = {"requested": value, "returned": returned, "read_back": back, "verified": bool(same)}
                if not same:
                    notes.append("Resolve did not keep %s=%s (reads back %r)." % (key, value, back) + (
                        " On Resolve 21.2 Free only \"30\" was accepted in testing; set the rate in Project Settings "
                        "before adding media instead." if key == "timelineFrameRate" else ""))
            result["settings"] = dict(applied, useCustomSettings=custom)
        try:
            project_fps = round(float(self._project_rate()), 3)
            dominant, rates = self._dominant_media_fps()
            timeline_fps = round(float(call(timeline, "GetSetting", project_fps, "timelineFrameRate") or project_fps), 3)
            if dominant and abs(dominant - timeline_fps) > 0.01:
                notes.append("This timeline runs at %g fps but most video in the media pool is %g fps (%s); Resolve "
                             "conforms mismatched clips, which can stutter. Free locks the project rate once media "
                             "is used." % (timeline_fps, dominant, ", ".join("%g: %d" % kv for kv in sorted(rates.items()))))
        except (TypeError, ValueError, OperationError):
            pass
        if not result["active"]:
            notes.append("The timeline was created, but Resolve did not confirm opening it. Inspect or open it before editing.")
        if notes:
            result["note"] = " ".join(notes)
        return result

    def _op_set_playhead(self, params):
        timecode = str(params.get("timecode", "")).strip()
        if not timecode:
            raise OperationError("timecode is required in HH:MM:SS:FF format.")
        if not self._timeline().SetCurrentTimecode(timecode):
            raise OperationError(
                "Resolve rejected the timecode. Use the timeline's frame rate and HH:MM:SS:FF."
            )
        return {"timecode": timecode}

    def _op_open_page(self, params):
        page = str(params.get("page", "")).strip().lower()
        if page not in PAGES:
            raise OperationError("page must be one of: %s" % ", ".join(PAGES))
        self.resolve.OpenPage(page)
        return {"page": call(self.resolve, "GetCurrentPage", page)}

    def _op_add_marker(self, params):
        timeline = self._timeline()
        frame = params.get("frame")
        if frame is None:
            frame = self._current_marker_frame(timeline)
        color = str(params.get("color", "Blue"))
        name = str(params.get("name", "AI marker"))
        note = str(params.get("note", ""))
        duration = max(1, int(params.get("duration", 1) or 1))
        custom_data = _notes_tag(params.get("custom_data"))
        if not timeline.AddMarker(int(frame), color, name, note, duration, custom_data):
            raise OperationError(
                "Resolve could not add the marker. A marker may already exist at frame %d, or the "
                "colour name may be invalid." % int(frame)
            )
        return {"frame": int(frame), "color": color, "name": name}

    def _op_delete_marker(self, params):
        frame = int(params.get("frame"))
        if not self._timeline().DeleteMarkerAtFrame(frame):
            raise OperationError("No marker was deleted at frame %d." % frame)
        return {"deleted_frame": frame}

    def _op_set_clip_property(self, params):
        item, info = self._find_timeline_items([params.get("item_id")])[0]
        name = str(params.get("property_name", "")).strip()
        if not name:
            raise OperationError("property_name is required.")
        value = params.get("value")
        if not item.SetProperty(name, value):
            available = plain(call(item, "GetProperty", {}) or {})
            raise OperationError(
                "Resolve rejected property '%s'. Current values: %s" % (name, json.dumps(available))
            )
        actual = plain(call(item, "GetProperty", None, name))
        result = {"item": info["id"], "property": name, "value": value, "read_back": actual}
        if actual is not None and str(actual) != str(value):
            try:
                same = abs(float(actual) - float(value)) < 1e-6
            except (TypeError, ValueError):
                same = False
            if not same:
                result["note"] = "Resolve accepted the call but reports %r, not %r." % (actual, value)
        return result

    def _op_set_clip_enabled(self, params):
        item, info = self._find_timeline_items([params.get("item_id")])[0]
        enabled = bool(params.get("enabled", True))
        if not item.SetClipEnabled(enabled):
            raise OperationError("Resolve could not change the clip enabled state.")
        actual = call(item, "GetClipEnabled", None)
        if actual is not None and bool(actual) != enabled:
            raise OperationError("Resolve accepted the change but the clip still reads enabled=%s." % bool(actual))
        return {"item": info["id"], "enabled": enabled}

    def _op_set_clip_color(self, params):
        item, info = self._find_timeline_items([params.get("item_id")])[0]
        color = str(params.get("color", "")).strip()
        ok = item.SetClipColor(color) if color else item.ClearClipColor()
        if not ok:
            raise OperationError(
                "Resolve rejected the clip color. Use a standard Resolve color name, or an empty "
                "value to clear it."
            )
        actual = call(item, "GetClipColor", None)
        if actual is not None and str(actual).lower() != color.lower():
            raise OperationError("Resolve accepted the color but the clip reads %r." % actual)
        return {"item": info["id"], "color": color or None}

    # Shot matching (bridge/colormatch.py measures source media in the MCP server; this only writes the CDL).
    SHOT_MATCH_LIMITS = {"slope": (0.4, 2.5), "offset": (-0.3, 0.3), "power": (0.5, 2.0), "saturation": (0.5, 1.6)}

    @staticmethod
    def _read_cdl(item, node_index=1):
        """Node CDL via TimelineItem.GetCDL (Resolve 21.2+, Free). None when this build cannot report it."""
        value = plain(call(item, "GetCDL", None, int(node_index)))
        if not isinstance(value, dict):
            return None
        try:
            out = {key.lower(): [float(v) for v in str(value[key]).split()] for key in ("Slope", "Offset", "Power")}
            out["saturation"] = float(value.get("Saturation", 1.0))
        except (KeyError, TypeError, ValueError):
            return None
        if any(len(out[key]) != 3 for key in ("slope", "offset", "power")):
            return None
        out["node_index"] = int(node_index)
        return out

    def _op_shot_match_sources(self, params):
        """Read-only: source windows and grade summaries for a reference and target clip, in one timeline scan."""
        ids = [str(params.get("reference_item_id") or "").strip(), str(params.get("target_item_id") or "").strip()]
        if not all(ids) or any(i.lower() in ("playhead", "current", "under_playhead", "at_playhead") for i in ids):
            raise OperationError("Pass unique clip ids for both the reference and the target.")
        timeline = self._timeline()
        fps = self._project_rate()
        result = {"timeline": self._timeline_identity(timeline), "fps": fps}
        for role, (item, info) in zip(("reference", "target"), self._find_timeline_items(ids)):
            if info.get("track_type") != "video":
                raise OperationError("The %s %s is not a video clip." % (role, info["id"]))
            entry = self._source_entry(timeline, item, info, fps)
            graph = call(item, "GetNodeGraph")
            entry["grade"] = {"item": info["id"], "name": info["name"],
                              "num_color_nodes": (call(graph, "GetNumNodes", 0) if graph else 0) or 0,
                              "fusion_comp_count": call(item, "GetFusionCompCount", 0) or 0,
                              "node_1_cdl": self._read_cdl(item, 1)}
            result[role] = entry
        return result

    def _op_apply_shot_match(self, params):
        """Set a solved shot-match CDL on the target's node, then read it back. The only write in match_shot."""
        target_id = str(params.get("target_item_id") or "").strip()
        if not target_id or target_id.lower() in ("playhead", "current", "under_playhead", "at_playhead"):
            raise OperationError("apply_shot_match needs the target clip's unique id. Nothing was changed.")
        item, info = self._find_timeline_items([target_id])[0]
        if info.get("track_type") != "video":
            raise OperationError("The target %s is not a video clip. Nothing was changed." % info["id"])
        media = call(item, "GetMediaPoolItem", None)
        file_path = str(call(media, "GetClipProperty", "", "File Path") or "")
        expected = params.get("expected_file_path")
        if expected and file_path != str(expected):
            raise OperationError("The target clip's source changed since it was measured (%r, expected %r). Run "
                                 "match_shot again. Nothing was changed." % (file_path, expected))
        node_index = int(params.get("node_index", 1))
        graph = call(item, "GetNodeGraph")
        num_nodes = int((call(graph, "GetNumNodes", 0) if graph else 0) or call(item, "GetNumNodes", 0) or 0)
        if node_index < 1 or (num_nodes and node_index > num_nodes):
            raise OperationError("node_index %d is outside the clip's %d node(s). Nothing was changed."
                                 % (node_index, num_nodes))
        cdl = {}
        for key in ("slope", "offset", "power"):
            values = params.get(key)
            if isinstance(values, str):
                values = values.split()
            try:
                values = [float(v) for v in values]
            except (TypeError, ValueError):
                values = []
            low, high = self.SHOT_MATCH_LIMITS[key]
            if len(values) != 3 or not all(math.isfinite(v) and low <= v <= high for v in values):
                raise OperationError("%s must be three numbers between %s and %s. Nothing was changed." % (key, low, high))
            cdl[key] = values
        try:
            cdl["saturation"] = float(params.get("saturation", 1.0))
        except (TypeError, ValueError):
            cdl["saturation"] = float("nan")
        low, high = self.SHOT_MATCH_LIMITS["saturation"]
        if not (math.isfinite(cdl["saturation"]) and low <= cdl["saturation"] <= high):
            raise OperationError("saturation must be between %s and %s. Nothing was changed." % (low, high))
        previous = self._read_cdl(item, node_index)
        cdl_map = {"NodeIndex": str(node_index), "Saturation": "%.6f" % cdl["saturation"]}
        for key in ("slope", "offset", "power"):
            cdl_map[key.capitalize()] = " ".join("%.6f" % v for v in cdl[key])
        returned = bool(item.SetCDL(cdl_map))
        read_back = self._read_cdl(item, node_index)
        verified = None
        if read_back is not None:
            verified = all(abs(a - b) <= 5e-4 for key in ("slope", "offset", "power")
                           for a, b in zip(read_back[key], cdl[key])) and abs(read_back["saturation"] - cdl["saturation"]) <= 5e-4
        applied = returned and verified is not False
        if verified is True:
            note = "Resolve reports the new CDL on node %d." % node_index
        elif verified is False:
            note = ("SetCDL returned %s but node %d reads back different values; treat the match as NOT applied and "
                    "check the Color page." % (returned, node_index))
        else:
            note = ("SetCDL returned %s. This Resolve build cannot read a CDL back (TimelineItem.GetCDL), so the "
                    "change is unverified; check the Color page." % returned)
        if previous is not None:
            note += " To undo, call set_clip_grade with the 'previous' values."
        return {"item": info["id"], "name": info["name"], "reference_item": params.get("reference_item_id"),
                "node_index": node_index, "requested": cdl, "set_cdl_returned": returned, "previous": previous,
                "read_back": read_back, "verified": verified, "applied": applied, "note": note}

    def _op_get_clip_grade(self, params):
        item, info = self._resolve_one_item(params.get("item_id"), track_hint="video")
        graph = call(item, "GetNodeGraph")
        num_nodes = call(graph, "GetNumNodes", 0) if graph else 0
        comp_count = call(item, "GetFusionCompCount", 0) or 0
        comp_names = call(item, "GetFusionCompNameList", []) or []
        versions = call(item, "GetVersionNameList", [], 0) or []
        current_version = call(item, "GetCurrentVersion", {}) or {}
        return {
            "item": info["id"],
            "name": info["name"],
            "num_color_nodes": num_nodes,
            "fusion_comp_count": comp_count,
            "fusion_comp_names": comp_names,
            "versions": versions,
            "current_version": current_version,
            "node_1_cdl": self._read_cdl(item, 1),
        }

    def _select_items(self, params, default_kind=None):
        """Items from item_ids (ids or labels) or range {start_frame, end_frame (exclusive), track: "V1"|"A2"|None}."""
        ids = params.get("item_ids")
        if ids:
            if not isinstance(ids, list):
                raise OperationError("item_ids must be a list of clip ids.")
            return self._find_timeline_items(ids[:2000])
        span = params.get("range")
        if isinstance(span, dict):
            try:
                start, end = int(span["start_frame"]), int(span["end_frame"])
            except (KeyError, TypeError, ValueError):
                raise OperationError("range needs integer start_frame and end_frame (exclusive).")
            track = str(span.get("track") or "").upper()
            picked = []
            for item, info in self._timeline_items():
                label = "%s%d" % (info["label"][0], info["track_index"])
                if (track and label != track) or (not track and default_kind and info["track_type"] != default_kind):
                    continue
                if float(info["start"]) < end and start < float(info["end"]):
                    picked.append((item, info))
            if not picked:
                raise OperationError("No clips overlap %d-%d%s. Nothing was changed." % (start, end, " on " + track if track else ""))
            return picked
        return None

    def _op_set_clip_grade(self, params):
        many = self._select_items(params, default_kind="video")
        if many is not None:
            results = [self._op_set_clip_grade(dict(params, item_ids=None, range=None, item_id=info["id"]))
                       for _, info in many]
            return {"clips": results, "count": len(results), "applied": sum(1 for r in results if r["applied"]),
                    "verified": all(r.get("verified") is not False for r in results)}
        item, info = self._resolve_one_item(params.get("item_id"), track_hint="video")
        saturation = params.get("saturation")
        slope = params.get("slope", "1.0 1.0 1.0")
        offset = params.get("offset", "0.0 0.0 0.0")
        power = params.get("power", "1.0 1.0 1.0")
        node_index = str(params.get("node_index", 1))
        reset = bool(params.get("reset", False))

        graph = call(item, "GetNodeGraph")
        if reset and graph:
            call(graph, "ResetAllGrades")

        cdl_map = {"NodeIndex": str(node_index)}
        if saturation is not None:
            cdl_map["Saturation"] = str(float(saturation))
        if slope:
            cdl_map["Slope"] = " ".join(str(v) for v in slope) if isinstance(slope, (list, tuple)) else str(slope)
        if offset:
            cdl_map["Offset"] = " ".join(str(v) for v in offset) if isinstance(offset, (list, tuple)) else str(offset)
        if power:
            cdl_map["Power"] = " ".join(str(v) for v in power) if isinstance(power, (list, tuple)) else str(power)

        # Resolve 21.2 Free: SetCDL returns False (and changes nothing) unless the map carries every CDL key, so
        # anything not asked for keeps the node's current value (or the identity when it can't be read).
        current = self._read_cdl(item, int(node_index)) or {}
        defaults = {"Slope": "1.0 1.0 1.0", "Offset": "0.0 0.0 0.0", "Power": "1.0 1.0 1.0", "Saturation": "1.0"}
        for key, fallback in defaults.items():
            if key not in cdl_map:
                have = current.get(key.lower())
                cdl_map[key] = (" ".join("%.6f" % v for v in have) if isinstance(have, list)
                                else "%.6f" % have if isinstance(have, (int, float)) else fallback)
        ok = item.SetCDL(cdl_map)
        back = self._read_cdl(item, int(node_index))
        return {
            "item": info["id"],
            "name": info["name"],
            "cdl": cdl_map,
            "applied": bool(ok),
            "verified": None if back is None else self._cdl_matches(back, cdl_map),
        }

    @staticmethod
    def _cdl_matches(back, wanted):
        def numbers(value):
            try:
                return [float(v) for v in (value if isinstance(value, (list, tuple)) else str(value).split())]
            except (TypeError, ValueError):
                return None
        for key in ("Slope", "Offset", "Power", "Saturation"):
            if key not in wanted:
                continue
            got = back.get(key.lower(), back.get(key)) if isinstance(back, dict) else None
            a, b = numbers(got), numbers(wanted[key])
            if a is None or b is None or len(a) != len(b) or any(abs(x - y) > 1e-3 for x, y in zip(a, b)):
                return False
        return True

    def _op_copy_grade(self, params):
        """CopyGrades from one clip to many (Resolve's own grade copy, current version), counted per target."""
        source, info = self._resolve_one_item(params.get("from_item_id"), track_hint="video")
        targets = self._select_items(params, default_kind="video") or []
        targets = [(item, t) for item, t in targets if t["id"] != info["id"]]
        if not targets:
            raise OperationError("Pass item_ids or range for the clips to receive the grade. Nothing was changed.")
        ok = bool(call(source, "CopyGrades", False, [item for item, _ in targets]))
        before = self._read_cdl(source, 1)
        wanted = None if before is None else {k.capitalize(): before[k] for k in ("slope", "offset", "power", "saturation")}
        checks = []
        for item, t in targets:
            back = self._read_cdl(item, 1) if wanted is not None else None
            checks.append({"item": t["id"], "matches_node1_cdl": None if back is None else self._cdl_matches(back, wanted)})
        return {"from": info["id"], "copied_to": len(targets), "returned": ok, "checks": checks,
                "note": "CopyGrades copies the current grade version; node 1's CDL was read back where Resolve reports it."
                        if ok else "Resolve's CopyGrades returned False; check the clips on the Color page."}

    # Properties set_clip_properties writes with SetProperty and reads back. Others are refused up front so a typo
    # never silently fails; transform names are Resolve's own keys (see READABLE_TRANSFORM_KEYS).
    BATCH_PROPERTIES = set(READABLE_TRANSFORM_KEYS) | {"AudioVolume", "AudioPan", "AudioVolumeEnabled"}

    def _op_set_clip_properties(self, params):
        """SetProperty on many clips at once, each value read back."""
        props = params.get("properties")
        if not isinstance(props, dict) or not props:
            raise OperationError("properties must be an object like {\"AudioVolume\": -3, \"Opacity\": 80}.")
        unknown = sorted(set(props) - self.BATCH_PROPERTIES)
        if unknown:
            raise OperationError("Unknown properties %s; allowed: %s. Nothing was changed."
                                 % (", ".join(unknown), ", ".join(sorted(self.BATCH_PROPERTIES))))
        items = self._select_items(params)
        if not items:
            raise OperationError("Pass item_ids or range. Nothing was changed.")
        results, failures = [], 0
        for item, info in items:
            entry = {"item": info["id"], "label": info["label"], "set": {}}
            for key, value in props.items():
                if key.startswith("Audio") and info["track_type"] != "audio":
                    entry["set"][key] = {"skipped": "not an audio clip"}
                    continue
                returned = call(item, "SetProperty", None, key, value)
                back = plain(call(item, "GetProperty", None, key))
                good = back is not None and self._same_value(back, value)
                failures += 0 if good else 1
                entry["set"][key] = {"value": value, "read_back": back, "verified": good}
                if returned is False:
                    entry["set"][key]["returned"] = False
            results.append(entry)
        return {"clips": results, "count": len(results), "verified": failures == 0,
                "note": "Every value was read back." if not failures else "%d value(s) did not read back; see clips." % failures}

    # ------------------------------------------------------------- fusion ops

    def _fusion_comp(self, item, info, create=True):
        comp = call(item, "GetFusionCompByIndex", None, 1)
        if comp is None and create:
            comp = call(item, "AddFusionComp", None)
            if comp is None:
                raise OperationError(
                    "Resolve did not return a Fusion composition for %s. This clip type may not "
                    "accept Fusion effects." % info["id"])
        return comp

    def _fusion_reset(self, item, info, kind):
        """Remove every bridge stage of ``kind``; user nodes and other effects stay connected."""
        comp = self._fusion_comp(item, info, create=False)
        if comp is None:
            return {"item": info["id"], "reset": True, "removed": [], "note": "No Fusion composition; nothing to reset."}
        _comp_open(comp)
        try:
            keys = [key for key in _fusion_stages(comp) if key.partition(":")[0] == kind]
            chain = fusion_rebuild(comp, remove=keys) if keys else None
        finally:
            _comp_close(comp)
        return {"item": info["id"], "reset": True, "removed": keys, "fusion_chain": chain,
                "note": ("Removed the bridge %s nodes; your own Fusion nodes and other bridge effects stay connected." % kind)
                if keys else "No bridge %s nodes were found; nothing was changed." % kind}

    def _effect_range(self, item, params):
        """Return clip-relative (start, end) offsets for an effect.

        ``start_frame``/``end_frame`` count from the clip's first frame.
        ``timeline_start_frame``/``timeline_end_frame`` take absolute timeline frames.
        """
        duration = int(call(item, "GetDuration", 0) or 0)
        clip_start = int(call(item, "GetStart", 0) or 0)

        def offset(key, default):
            absolute = params.get("timeline_" + key)
            if absolute is not None:
                return int(absolute) - clip_start
            value = params.get(key)
            return default if value is None else int(value)

        start = offset("start_frame", 0)
        end = offset("end_frame", duration - 1 if duration > 1 else start + 1)
        last = duration - 1 if duration > 0 else None
        if start < 0 or end <= start or (last is not None and end > last):
            raise OperationError(
                "Effect frames %d..%d (clip offsets) are outside this clip, which spans offsets 0..%s "
                "(timeline frames %d..%s). end must be after start."
                % (start, end, last if last is not None else "?", clip_start,
                   clip_start + last if last is not None else "?"))
        return start, end

    def _keyframe_offsets(self, item, keyframes):
        """Sort keyframes into (clip offset, dict); ``timeline_frame`` takes an absolute frame."""
        duration = int(call(item, "GetDuration", 0) or 0)
        clip_start = int(call(item, "GetStart", 0) or 0)
        pairs = []
        for key in keyframes:
            if not isinstance(key, dict):
                raise OperationError("Each keyframe must be an object with a frame or timeline_frame.")
            frame = int(key["timeline_frame"]) - clip_start if key.get("timeline_frame") is not None else int(key.get("frame", 0))
            if frame < 0 or (duration > 0 and frame > duration - 1):
                raise OperationError("Keyframe at clip offset %d is outside this clip (0..%d)." % (frame, duration - 1))
            pairs.append((frame, key))
        pairs.sort(key=lambda pair: pair[0])
        if any(a[0] == b[0] for a, b in zip(pairs, pairs[1:])):
            raise OperationError("Two keyframes share the same frame; give each keyframe its own frame.")
        return pairs

    def _sample_keyframes(self, pairs, fields, default_easing="smootherstep"):
        """Per-frame values between keyframes; ``fields`` maps a series name to a getter."""
        series = {name: [] for name in fields}
        for index in range(len(pairs) - 1):
            (f0, k0), (f1, k1) = pairs[index], pairs[index + 1]
            ease = self._build_ease_func(str(k1.get("easing", k0.get("easing", default_easing))).lower().strip())
            stop = f1 + 1 if index == len(pairs) - 2 else f1
            for frame in range(f0, stop):
                eased = ease((frame - f0) / float(f1 - f0))
                for name, get in fields.items():
                    a, b = get(k0), get(k1)
                    series[name].append((frame, a + (b - a) * eased))
        return series

    def _sample_range(self, start, end, a, b, easing):
        ease = self._build_ease_func(easing)
        span = float(end - start)
        return [(frame, a + (b - a) * ease((frame - start) / span)) for frame in range(start, end + 1)]

    @staticmethod
    def _fusion_summary(verified, lua_ok):
        if lua_ok is False:
            return "Fusion stopped before the bridge script finished; the Resolve Console shows the Lua error. Nothing was connected."
        if verified is None:
            return "Sent to Fusion. This build did not let the bridge read the result back; check it with inspect_fusion or the Fusion page."
        if not verified:
            return "The value Fusion reads back differs from the request. Inspect the clip with inspect_fusion."
        return None

    def _op_keyframe_clip_saturation(self, params):
        item, info = self._resolve_one_item(params.get("item_id"), track_hint="video")
        if params.get("reset"):
            return self._fusion_reset(item, info, "saturation")
        start_frame, end_frame = self._effect_range(item, params)
        start_sat = float(params.get("start_saturation", 1.0))
        end_sat = float(params.get("end_saturation", 0.0))
        easing = str(params.get("easing", "linear")).lower().strip()
        comp = self._fusion_comp(item, info)
        _comp_open(comp)
        edit = FusionEdit(self, comp, "saturation")
        try:
            node = edit.node("main", ("BrightnessContrast",), "AIBridgeSaturation")
            if not edit.has_input(node, "Saturation"):
                raise OperationError("Fusion's BrightnessContrast exposes no Saturation input on this build.")
            lines = [edit.bind("sat", node)]
            lines += edit.animate("sat", "Saturation", self._sample_range(start_frame, end_frame, start_sat, end_sat, easing))
            lua_ok = edit.lua(lines)
            if lua_ok is False:
                raise OperationError(self._fusion_summary(None, False))
            chain = edit.finish()
        except Exception:
            edit.rollback()
            raise
        finally:
            _comp_close(comp)
        verified = edit.check(node, "Saturation", end_frame, end_sat)
        result = {"item": info["id"], "name": info["name"], "node": edit.name(node),
                  "start_frame": start_frame, "end_frame": end_frame, "frames": "clip offsets",
                  "start_saturation": start_sat, "end_saturation": end_sat, "easing": easing,
                  "fusion_start_frame": edit.t0, "applied": True, "verified": verified, "fusion_chain": chain}
        note = self._fusion_summary(verified, lua_ok)
        if note:
            result["note"] = note
        return result

    def _op_animate_color_fx(self, params):
        import random

        item, info = self._resolve_one_item(params.get("item_id"), track_hint="video")
        if params.get("reset"):
            return self._fusion_reset(item, info, "color_fx")
        start_frame, end_frame = self._effect_range(item, params)
        rainbow = params.get("rainbow", True)
        rainbow_cycles = float(rainbow) if isinstance(rainbow, (int, float)) and not isinstance(rainbow, bool) else (1.5 if rainbow else 0.0)
        tint_strength = float(params.get("tint_strength", 0.70))
        flicker = params.get("flicker", True)
        flicker_amp = float(flicker) if isinstance(flicker, (int, float)) and not isinstance(flicker, bool) else (0.05 if flicker else 0.0)
        flicker_freq = float(params.get("flicker_frequency", 2.2))
        saturation = float(params.get("saturation", 1.3))

        comp = self._fusion_comp(item, info)
        _comp_open(comp)
        edit = FusionEdit(self, comp, "color_fx")
        try:
            node = edit.node("main", ("ColorCorrector",), "AIBridgeColorFX")

            def first_input(*names):
                return next((name for name in names if edit.has_input(node, name)), None)

            sat_input = first_input("MasterSaturation", "Saturation1", "WheelSaturation1")
            angle_input = first_input("WheelTintAngle1")
            length_input = first_input("WheelTintLength1")
            gain_input = first_input("MasterRGBGain")
            missing = []
            lines = [edit.bind("cc", node)]
            if sat_input:
                lines += edit.static("cc", sat_input, saturation)
            else:
                missing.append("saturation")
            if rainbow_cycles > 0:
                if angle_input and length_input:
                    lines += edit.static("cc", length_input, tint_strength)
                    steps = max(16, (end_frame - start_frame) // 2)
                    keys = {}
                    for step in range(steps + 1):
                        frame = int(round(start_frame + (end_frame - start_frame) * step / float(steps)))
                        keys[frame] = (step / float(steps)) * rainbow_cycles
                    lines += edit.animate("cc", angle_input, sorted(keys.items()))
                else:
                    missing.append("rainbow tint")
            if flicker_amp > 0:
                if gain_input:
                    rng = random.Random(42)
                    pairs = []
                    for frame in range(start_frame, end_frame + 1):
                        wave = math.sin(frame * flicker_freq * 0.8) * 0.55 + math.cos(frame * flicker_freq * 1.6) * 0.30
                        pairs.append((frame, 1.0 + (wave + (rng.random() - 0.5) * 0.30) * flicker_amp))
                    lines += edit.animate("cc", gain_input, pairs)
                else:
                    missing.append("flicker")
            requested = 1 + (rainbow_cycles > 0) + (flicker_amp > 0)
            if len(missing) == requested:
                raise OperationError("Fusion's ColorCorrector exposes none of the inputs this effect needs on this build.")
            lua_ok = edit.lua(lines)
            if lua_ok is False:
                raise OperationError(self._fusion_summary(None, False))
            chain = edit.finish()
        except Exception:
            edit.rollback()
            raise
        finally:
            _comp_close(comp)
        result = {"item": info["id"], "name": info["name"], "node": edit.name(node),
                  "start_frame": start_frame, "end_frame": end_frame, "frames": "clip offsets",
                  "rainbow_cycles": rainbow_cycles, "tint_strength": tint_strength,
                  "flicker_amplitude": flicker_amp, "saturation": saturation,
                  "fusion_start_frame": edit.t0, "applied": True, "fusion_chain": chain}
        if missing:
            result["not_applied"] = missing
            result["note"] = "This build's ColorCorrector lacks the inputs for: %s. Those parts were skipped." % ", ".join(missing)
        elif lua_ok is None:
            result["note"] = self._fusion_summary(None, None)
        return result

    # blur_type keyword -> (Fusion tool IDs to try, size inputs to try)
    BLUR_TYPES = (
        ("defocus", ("Defocus",), ("XDefocusSize", "YDefocusSize", "DefocusSize")),
        ("mosaic", ("Mosaic", "MosaicBlur"), ("BlockSize", "Size")),
        ("pixel", ("Mosaic", "MosaicBlur"), ("BlockSize", "Size")),
        ("directional", ("DirectionalBlur",), ("Length",)),
        ("motion", ("DirectionalBlur",), ("Length",)),
        ("", ("Blur",), ("XBlurSize", "YBlurSize")),
    )

    def _op_apply_blur_effect(self, params):
        """Apply a static or gradual blur to the full frame or a masked region."""
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index"))
        if params.get("reset"):
            return self._fusion_reset(item, info, "blur")
        blur_type = str(params.get("blur_type", "gaussian")).lower().strip()
        reg_ids, size_inputs = next((ids, inputs) for word, ids, inputs in self.BLUR_TYPES if word in blur_type)
        blur_size = float(params.get("blur_size", params.get("size", 20.0)))
        cx = float(params.get("center_x", params.get("x", 0.50)))
        cy = float(params.get("center_y", params.get("y", 0.50)))
        w = float(params.get("width", params.get("w", 1.0)))
        h = float(params.get("height", params.get("h", 1.0)))
        corner_radius = float(params.get("corner_radius", 0.0))
        soft_edge = float(params.get("soft_edge", 0.015))
        mask_shape = str(params.get("mask_shape", "rectangle")).lower().strip()
        tag = "".join(c for c in str(params.get("mask_name", params.get("name", "1"))) if c.isalnum()) or "1"
        animate = bool(params.get("animate", False))
        start_blur = float(params.get("start_blur", 0.0))
        end_blur = float(params.get("end_blur", blur_size))
        easing = str(params.get("easing", "smootherstep")).lower().strip()
        raw_keyframes = params.get("keyframes")
        use_keys = isinstance(raw_keyframes, list) and len(raw_keyframes) >= 2
        has_mask = w < 0.999 or h < 0.999 or cx != 0.50 or cy != 0.50 or use_keys
        start_frame, end_frame = self._effect_range(item, params)
        series = {}
        if use_keys:
            pairs = self._keyframe_offsets(item, raw_keyframes)
            series = self._sample_keyframes(pairs, {
                "size": lambda k: float(k.get("blur_size", k.get("size", blur_size))),
                "x": lambda k: float(k.get("center_x", k.get("cx", cx))),
                "y": lambda k: float(k.get("center_y", k.get("cy", cy))),
                "w": lambda k: float(k.get("width", k.get("w", w))),
                "h": lambda k: float(k.get("height", k.get("h", h))),
            })
            start_frame, end_frame = pairs[0][0], pairs[-1][0]
            final_size = series["size"][-1][1]
        elif animate:
            series["size"] = self._sample_range(start_frame, end_frame, start_blur, end_blur, easing)
            final_size = end_blur
        else:
            final_size = blur_size

        comp = self._fusion_comp(item, info)
        _comp_open(comp)
        edit = FusionEdit(self, comp, "blur:" + tag)
        try:
            node = edit.node("main", reg_ids, "AIBridgeBlur_" + tag)
            inputs = [name for name in size_inputs if edit.has_input(node, name)]
            if not inputs:
                raise OperationError("Fusion's %s node exposes none of %s on this build; choose blur_type='gaussian'."
                                     % (" / ".join(reg_ids), ", ".join(size_inputs)))
            lines = [edit.bind("blur", node)]
            for name in inputs:
                lines += edit.animate("blur", name, series["size"]) if "size" in series else edit.static("blur", name, blur_size)
            mask = None
            if has_mask:
                shape = "EllipseMask" if "ellipse" in mask_shape or "circle" in mask_shape else "RectangleMask"
                mask = edit.node("mask", (shape,), "AIBridgeMask_" + tag)
                lines.append(edit.bind("mask", mask))
                if use_keys:
                    lines += edit.animate("mask", "Width", series["w"]) + edit.animate("mask", "Height", series["h"])
                    lines += edit.animate_point("mask", "Center", series["x"], series["y"])
                else:
                    lines += edit.static("mask", "Width", w) + edit.static("mask", "Height", h)
                    lines += edit.static_point("mask", "Center", cx, cy)
                if shape == "RectangleMask":
                    lines += edit.static("mask", "CornerRadius", corner_radius)
                lines += edit.static("mask", "SoftEdge", soft_edge)
                lines.append(edit.connect("blur", "EffectMask", "mask"))
            else:
                lines.append(edit.connect("blur", "EffectMask"))
                edit.drop("mask")
            lua_ok = edit.lua(lines)
            if lua_ok is False:
                raise OperationError(self._fusion_summary(None, False))
            chain = edit.finish()
        except Exception:
            edit.rollback()
            raise
        finally:
            _comp_close(comp)
        verified = edit.check(node, inputs[0], end_frame, final_size) if "size" in series else edit.check(node, inputs[0], 0, blur_size)
        result = {
            "item": info["id"],
            "blur_node": edit.name(node),
            "blur_type": call(node, "GetAttrs", None, "TOOLS_RegID") or reg_ids[0],
            "size_inputs": inputs,
            "mask_node": edit.name(mask) if mask is not None else None,
            "blur_size": blur_size,
            "animated": "size" in series,
            "start_blur": start_blur if animate and not use_keys else None,
            "end_blur": final_size if "size" in series else None,
            "start_frame": start_frame if series else None,
            "end_frame": end_frame if series else None,
            "frames": "clip offsets",
            "center": [cx, cy] if has_mask else [0.5, 0.5],
            "dimensions": [w, h] if has_mask else [1.0, 1.0],
            "verified": verified,
            "fusion_start_frame": edit.t0,
            "fusion_chain": chain,
        }
        result["note"] = self._fusion_summary(verified, lua_ok) or "Applied %s %s blur on %s." % (
            "animated" if series else "static", blur_type, info["id"])
        return result

    def _op_apply_spotlight_mask(self, params):
        """Apply a static or animated spotlight: dim the frame outside a soft mask."""
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index"))
        if params.get("reset"):
            return self._fusion_reset(item, info, "spotlight")
        cx = float(params.get("center_x", params.get("x", 0.50)))
        cy = float(params.get("center_y", params.get("y", 0.50)))
        radius = float(params.get("radius", params.get("size", 0.20)))
        sized = "radius" in params or "size" in params
        w = float(params.get("width", params.get("w", radius * 2 if sized else 0.40)))
        h = float(params.get("height", params.get("h", radius * 2 if sized else 0.40)))
        soft_edge = float(params.get("soft_edge", params.get("feather", 0.08)))
        ambient = max(0.0, min(1.0, float(params.get("ambient_brightness", params.get("base_gain", 0.08)))))
        spotlight_gain = float(params.get("spotlight_gain", params.get("gain", 1.0)))
        mask_shape = str(params.get("shape", params.get("mask_shape", "ellipse"))).lower().strip()
        tag = "".join(c for c in str(params.get("mask_name", params.get("name", "1"))) if c.isalnum()) or "1"
        raw_keyframes = params.get("keyframes")
        start_frame, end_frame = self._effect_range(item, params)
        series = {}
        if isinstance(raw_keyframes, list) and len(raw_keyframes) >= 2:
            pairs = self._keyframe_offsets(item, raw_keyframes)

            def size(axis):
                def get(k):
                    r = float(k.get("radius", k.get("size", radius)))
                    return float(k.get(axis, k.get(axis[0], r * 2)))
                return get
            series = self._sample_keyframes(pairs, {
                "x": lambda k: float(k.get("x", k.get("center_x", cx))),
                "y": lambda k: float(k.get("y", k.get("center_y", cy))),
                "w": size("width"), "h": size("height"),
            })
            start_frame, end_frame = pairs[0][0], pairs[-1][0]
        elif params.get("animate"):
            start_rad = float(params.get("start_radius", radius))
            end_rad = float(params.get("end_radius", 2.0 if params.get("expand") else radius))
            easing = str(params.get("easing", "cubic_out")).lower().strip()
            radii = self._sample_range(start_frame, end_frame, start_rad, end_rad, easing)
            series = {"w": [(f, r * 2) for f, r in radii], "h": [(f, r * 2) for f, r in radii]}

        comp = self._fusion_comp(item, info)
        _comp_open(comp)
        edit = FusionEdit(self, comp, "spotlight:" + tag)
        try:
            base = edit.node("base", ("BrightnessContrast",), "AIBridgeSpotlightBase_" + tag)
            gain = edit.node("gain", ("BrightnessContrast",), "AIBridgeSpotlightGain_" + tag)
            shape = "RectangleMask" if "rect" in mask_shape else "EllipseMask"
            mask = edit.node("mask", (shape,), "AIBridgeSpotlightMask_" + tag)
            merge = edit.node("merge", ("Merge",), "AIBridgeSpotlightMerge_" + tag)
            lines = [edit.bind("base", base), edit.bind("gain", gain), edit.bind("mask", mask), edit.bind("merge", merge)]
            lines += edit.static("base", "Gain", ambient) + edit.static("gain", "Gain", spotlight_gain)
            if "w" in series:
                lines += edit.animate("mask", "Width", series["w"]) + edit.animate("mask", "Height", series["h"])
            else:
                lines += edit.static("mask", "Width", w) + edit.static("mask", "Height", h)
            if "x" in series:
                lines += edit.animate_point("mask", "Center", series["x"], series["y"])
            else:
                lines += edit.static_point("mask", "Center", cx, cy)
            lines += edit.static("mask", "SoftEdge", soft_edge)
            lines += [edit.connect("merge", "Background", "base"), edit.connect("merge", "Foreground", "gain"),
                      edit.connect("merge", "EffectMask", "mask")]
            lua_ok = edit.lua(lines)
            if lua_ok is False:
                raise OperationError(self._fusion_summary(None, False))
            chain = edit.finish()
        except Exception:
            edit.rollback()
            raise
        finally:
            _comp_close(comp)
        final_w = series["w"][-1][1] if "w" in series else w
        verified = edit.check(mask, "Width", end_frame if series else 0, final_w)
        result = {
            "item": info["id"],
            "base_node": edit.name(base),
            "gain_node": edit.name(gain),
            "mask_node": edit.name(mask),
            "merge_node": edit.name(merge),
            "ambient_brightness": ambient,
            "spotlight_gain": spotlight_gain,
            "center": [cx, cy],
            "dimensions": [w, h],
            "soft_edge": soft_edge,
            "animated": bool(series),
            "start_frame": start_frame if series else None,
            "end_frame": end_frame if series else None,
            "frames": "clip offsets",
            "verified": verified,
            "fusion_start_frame": edit.t0,
            "fusion_chain": chain,
        }
        result["note"] = self._fusion_summary(verified, lua_ok) or "Applied spotlight mask to %s." % info["id"]
        return result

    def _op_inspect_fusion(self, params):
        """Read-only view of a clip's Fusion nodes and the bridge segment. Never creates a comp."""
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index") or "video")
        comp = self._fusion_comp(item, info, create=False)
        if comp is None:
            return {"item": info["id"], "has_fusion": False, "tools": {}, "bridge_stages": {}}
        attrs = {key: call(comp, "GetAttrs", None, "COMPN_" + key) for key in ("GlobalStart", "GlobalEnd", "RenderStart", "RenderEnd")}
        tools = {}
        for tool in _fusion_tools(comp):
            inputs = []
            listed = call(tool, "GetInputList", None) or {}
            for inp in (listed.values() if isinstance(listed, dict) else listed):
                connected = call(inp, "GetConnectedOutput", None)
                source = call(connected, "GetTool", None) if connected is not None else None
                if source is not None:
                    inputs.append({"id": str(call(inp, "GetAttrs", "", "INPS_ID")), "connected_to": _fusion_name(source)})
            owned = _fusion_role(tool)
            tools[_fusion_name(tool)] = {"reg_id": call(tool, "GetAttrs", None, "TOOLS_RegID"),
                                         "connected_inputs": inputs,
                                         "bridge_stage": owned[0] if owned else None}
        stages = _fusion_stages(comp)
        media_out = _fusion_endpoint(comp, "MediaOut")
        chain = None
        if media_out is not None:
            try:
                source, keys = _fusion_walk(media_out, stages)
                chain = {"source": _output_name(source), "stages_before_media_out": list(reversed(keys))}
            except OperationError as exc:
                chain = {"error": str(exc)}
        return {"item": info["id"], "has_fusion": True, "comp": attrs, "tools": tools,
                "bridge_stages": {key: {role: _fusion_name(node) for role, node in nodes.items()} for key, nodes in stages.items()},
                "bridge_chain": chain, "frame_format": self._comp_frame_format(item, comp)}

    def _comp_frame_format(self, item, comp):
        """Comp size and range: masks and Merge centres are relative to THIS image, which is the source media's
        size, not the timeline's. Values Resolve won't report come back null."""
        def number(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        media = call(item, "GetMediaPoolItem", None)
        return {"width": number(call(comp, "GetPrefs", None, "Comp.FrameFormat.Width")),
                "height": number(call(comp, "GetPrefs", None, "Comp.FrameFormat.Height")),
                "render_start": number(call(comp, "GetAttrs", None, "COMPN_RenderStart")),
                "render_end": number(call(comp, "GetAttrs", None, "COMPN_RenderEnd")),
                "media_in_source": call(media, "GetClipProperty", None, "File Path") if media is not None else None,
                "media_resolution": call(media, "GetClipProperty", None, "Resolution") if media is not None else None,
                "note": "media_in_source is the clip's media pool file, which MediaIn1 reads."}

    # --------------------------------------------------------- general Fusion

    FUSION_READ_TYPES = ("Number", "Point", "Text", "FuID", "Clip")

    @staticmethod
    def _fusion_identifier(value, what, fallback=None):
        text = "".join(c for c in str(value or "") if c.isalnum() or c == "_")
        if not text:
            if fallback is None:
                raise OperationError("%s is required and may use only letters, digits and underscores." % what)
            text = fallback
        if text[0].isdigit():
            text = "N" + text
        return text

    @staticmethod
    def _fusion_tool(comp, name):
        tool = call(comp, "FindTool", None, str(name))
        if tool is None:
            names = sorted(_fusion_name(t) for t in _fusion_tools(comp))
            raise OperationError("No Fusion tool named '%s' in this clip. Tools: %s. Call inspect_fusion."
                                 % (name, ", ".join(names[:60]) or "none"))
        return tool

    @staticmethod
    def _value_kind(value, where):
        if isinstance(value, bool) or isinstance(value, (int, float)):
            _lua_num(float(value))
            return "number"
        if isinstance(value, str):
            return "text"
        if isinstance(value, (list, tuple)) and len(value) == 2 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            _lua_num(value[0]), _lua_num(value[1])
            return "point"
        raise OperationError("%s must be a number, a string or an [x, y] pair; got %r." % (where, value))

    def _fusion_key_series(self, item, keyframes, interpolation, where):
        """Validate keyframes into ('number', pairs, None) or ('point', xs, ys), clip-offset frames."""
        if not isinstance(keyframes, list) or not keyframes:
            raise OperationError("%s keyframes must be a non-empty list of {frame|timeline_frame, value}." % where)
        for key in keyframes:
            if isinstance(key, dict) and "value" not in key:
                raise OperationError("Each %s keyframe needs a value." % where)
        pairs = self._keyframe_offsets(item, keyframes)
        kinds = {self._value_kind(key["value"], where + " keyframe value") for _, key in pairs}
        if kinds - {"number", "point"} or len(kinds) != 1:
            raise OperationError("%s keyframes must all be numbers or all be [x, y] points." % where)
        kind = kinds.pop()
        mode = str(interpolation or "fusion").lower().strip()
        if mode not in ("fusion", "baked"):
            raise OperationError("interpolation must be 'fusion' (keys only at your frames) or 'baked' (per-frame easing).")
        baked = len(pairs) > 1 and (mode == "baked" or any("easing" in key for _, key in pairs))

        def series(get):
            if baked:
                return self._sample_keyframes(pairs, {"v": get})["v"]
            return [(frame, get(key)) for frame, key in pairs]

        if kind == "number":
            return "number", series(lambda k: float(k["value"])), None, baked
        return ("point", series(lambda k: float(k["value"][0])), series(lambda k: float(k["value"][1])), baked)

    def _fusion_assign(self, edit, var, node, input_id, spec, item, where, replace=True):
        """Lua lines plus read-back checks for one input value, keyframe set or connection.

        Returns (lines, checks, connection); connection is ("from", name) for the caller to wire.
        """
        if not edit.has_input(node, input_id):
            raise OperationError("%s has no input '%s' on this build. Call fusion_inspect_tool to list its inputs."
                                 % (where, input_id))
        if isinstance(spec, dict) and "from" in spec:
            return [], [], str(spec["from"])
        if isinstance(spec, dict) and "keyframes" in spec:
            kind, first, second, baked = self._fusion_key_series(
                item, spec["keyframes"], spec.get("interpolation"), "%s.%s" % (where, input_id))
            modifier = None if replace else self._input_modifier(node, input_id)
            merge_into = "BezierSpline" if kind == "number" else "XYPath"
            if modifier is not None and modifier != merge_into:
                raise OperationError(
                    "%s.%s is animated by a %s modifier, which replace=false cannot add keys to (only a %s can). "
                    "Use replace=true to replace that animation, or clear=true to remove it. Nothing was changed."
                    % (where, input_id, modifier, merge_into))
            if kind == "number":
                lines = edit.add_keys(var, input_id, first) if modifier else edit.animate(var, input_id, first)
                checks = [(node, input_id, first[0][0], first[0][1]), (node, input_id, first[-1][0], first[-1][1])]
            else:
                lines = (edit.add_point_keys(var, input_id, first, second) if modifier
                         else edit.animate_point(var, input_id, first, second))
                checks = [(node, input_id, first[i][0], (first[i][1], second[i][1])) for i in (0, -1)]
            return lines, checks, None
        value = spec.get("value") if isinstance(spec, dict) and "value" in spec else spec
        kind = self._value_kind(value, "%s.%s" % (where, input_id))
        if kind == "number":
            return edit.static(var, input_id, float(value)), [(node, input_id, 0, float(value))], None
        if kind == "point":
            return (edit.static_point(var, input_id, float(value[0]), float(value[1])),
                    [(node, input_id, 0, (float(value[0]), float(value[1])))], None)
        return edit.static_text(var, input_id, value), [(node, input_id, 0, str(value))], None

    @staticmethod
    def _input_modifier(node, input_id):
        output = call(_named_input(node, input_id), "GetConnectedOutput", None)
        tool = call(output, "GetTool", None) if output is not None else None
        return call(tool, "GetAttrs", None, "TOOLS_RegID") if tool is not None else None

    @staticmethod
    def _fusion_checks(edit, checks):
        """True if every value reads back, False on any mismatch, None if the build will not say."""
        outcome = True
        for node, input_id, frame, expected in checks:
            actual = call(node, "GetInput", None, input_id, edit.t0 + frame)
            try:
                if isinstance(expected, str):
                    good = None if actual is None else str(actual) == expected
                elif isinstance(expected, tuple):
                    if isinstance(actual, dict):
                        actual = [actual.get(i, actual.get(str(i))) for i in (1, 2)]
                    good = all(abs(float(a) - b) <= 1e-3 for a, b in zip(actual, expected)) and len(actual) >= 2
                else:
                    good = abs(float(actual) - float(expected)) <= 1e-3
            except (TypeError, ValueError):
                good = None
            if good is False:
                return False
            if good is None:
                outcome = None
        return outcome

    def _custom_reset(self, item, info, key):
        comp = self._fusion_comp(item, info, create=False)
        if comp is None:
            return {"item": info["id"], "reset": True, "removed": [], "note": "No Fusion composition; nothing to reset."}
        _comp_open(comp)
        try:
            present = key in _fusion_stages(comp)
            chain = fusion_rebuild(comp, remove=[key]) if present else None
        finally:
            _comp_close(comp)
        return {"item": info["id"], "reset": True, "removed": [key] if present else [], "fusion_chain": chain,
                "note": "Removed %s; your own Fusion nodes and other bridge effects stay connected." % key
                if present else "No %s stage was found; nothing was changed." % key}

    @staticmethod
    def _tag_chain(nodes, entry, exit_tool, link, feeds):
        """Tag a custom stage's ends. ``nodes`` are this stage's tools, ``feeds`` maps tool -> input names."""
        for tool in nodes:
            position = "both" if tool is entry and tool is exit_tool else (
                "entry" if tool is entry else "exit" if tool is exit_tool else None)
            call(tool, "SetData", None, FUSION_CHAIN_KEY, position)
            call(tool, "SetData", None, FUSION_LINK_KEY, (link or "") if tool is entry else None)
            names = feeds.get(_fusion_name(tool)) or []
            call(tool, "SetData", None, FUSION_FEED_KEY, ",".join(names) if names else None)
        for tool, value in ((entry, ("entry", "both")), (exit_tool, ("exit", "both"))):
            if call(tool, "GetData", None, FUSION_CHAIN_KEY) not in value:
                raise OperationError("Could not tag %s as the custom stage's chain end." % _fusion_name(tool))

    def _main_input_id(self, node):
        inp = call(node, "FindMainInput", None, 1)
        name = call(inp, "GetAttrs", None, "INPS_ID") if inp is not None else None
        if not name:
            raise OperationError("%s has no main image input; name the chain input explicitly." % _fusion_name(node))
        return str(name)

    def _op_fusion_build(self, params):
        """Insert a described node graph into the clip's bridge chain as one custom stage."""
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index") or "video")
        tag = self._fusion_identifier(params.get("name"), "name", fallback="Custom")
        key = "custom:" + tag
        if params.get("reset"):
            return self._custom_reset(item, info, key)
        specs = params.get("nodes")
        if not isinstance(specs, list) or not specs:
            raise OperationError("nodes must be a non-empty list of {name, type, inputs}.")
        names = []
        for spec in specs:
            if not isinstance(spec, dict) or not spec.get("type"):
                raise OperationError("Each node needs a 'type' (a Fusion RegID such as Transform) and a 'name'.")
            names.append(self._fusion_identifier(spec.get("name") or spec["type"], "node name"))
            if not isinstance(spec.get("inputs") or {}, dict):
                raise OperationError("Node %s inputs must be an object of InputID: value." % names[-1])
        if len(set(names)) != len(names):
            raise OperationError("Node names must be unique within one fusion_build call.")
        raw_chain = params.get("chain") or ([names[0]] if len(names) == 1 else None)
        chain_inferred = False
        if not raw_chain:
            # With {"from": ...} links the graph has one end node that nothing else reads: that is the output,
            # and the stage's input goes wherever a node asks for {"from": "chain"}.
            sources = {str(value.get("from")) for spec in specs for value in (spec.get("inputs") or {}).values()
                       if isinstance(value, dict) and value.get("from")}
            ends = [name for name in names if name not in sources]
            if len(ends) != 1:
                raise OperationError("chain is required here: list the node names from input to output (or link "
                                     "every node with {\"from\": ...} so exactly one node is the output; now %s)."
                                     % ("none is" if not ends else "%s all are" % ", ".join(ends)))
            end_spec = specs[names.index(ends[0])]
            entry = next((key for key, value in (end_spec.get("inputs") or {}).items()
                          if isinstance(value, dict) and value.get("from") == "chain"), None)
            raw_chain = [{"node": ends[0], "input": entry} if entry else ends[0]]
            chain_inferred = True
        chain = []
        for link in raw_chain:
            node_name, input_id = (link.get("node"), link.get("input")) if isinstance(link, dict) else (link, None)
            node_name = self._fusion_identifier(node_name, "chain node")
            if node_name not in names:
                raise OperationError("chain names %s, which is not one of nodes: %s." % (node_name, ", ".join(names)))
            chain.append((node_name, str(input_id) if input_id else None))
        if len({name for name, _ in chain}) != len(chain):
            raise OperationError("A node may appear only once in chain.")
        for spec in specs:  # Validate keyframes before touching the comp.
            for input_id, value in (spec.get("inputs") or {}).items():
                if isinstance(value, dict) and "keyframes" in value:
                    self._fusion_key_series(item, value["keyframes"], value.get("interpolation"), str(input_id))

        comp = self._fusion_comp(item, info)
        _comp_open(comp)
        edit = FusionEdit(self, comp, key)
        edit.allow_native_fallback = True
        try:
            for old in list(edit.nodes.values()):
                edit._retire(old)
            edit.nodes = {}
            tools, variables = {}, {}
            lines = []
            for index, (spec, name) in enumerate(zip(specs, names)):
                tools[name] = edit.node("n_" + name, (str(spec["type"]),), "%s_%s" % (tag, name))
                variables[name] = "aib_n%d" % index
                lines.append(edit.bind(variables[name], tools[name]))
            checks, feeds, links, media_in_bound = [], {}, [], False
            for spec, name in zip(specs, names):
                node, var = tools[name], variables[name]
                for input_id, value in (spec.get("inputs") or {}).items():
                    input_id = str(input_id)
                    more, more_checks, source = self._fusion_assign(edit, var, node, input_id, value, item,
                                                                    "%s (%s)" % (name, spec["type"]))
                    lines += more
                    checks += more_checks
                    if source is None:
                        continue
                    if source.lower() == "chain":
                        feeds.setdefault(_fusion_name(node), []).append(input_id)
                        continue
                    if source.lower() in ("mediain", "media_in"):
                        target = _fusion_endpoint(comp, "MediaIn")
                        if target is None:
                            raise OperationError("This comp has no MediaIn to connect from.")
                        if not media_in_bound:
                            lines.insert(0, edit.bind("aib_mediain", target))
                            media_in_bound = True
                        lines.append(edit.connect(var, input_id, "aib_mediain"))
                        continue
                    local = self._fusion_identifier(source, "from")
                    if local in variables:
                        lines.append(edit.connect(var, input_id, variables[local]))
                    else:
                        other = self._fusion_tool(comp, source)
                        other_var = "aib_x%d" % len(links)
                        links.append(other_var)
                        lines.insert(0, edit.bind(other_var, other))
                        lines.append(edit.connect(var, input_id, other_var))
            for (previous, _), (name, input_id) in zip(chain, chain[1:]):
                target_input = input_id or self._main_input_id(tools[name])
                if not edit.has_input(tools[name], target_input):
                    raise OperationError("%s has no input '%s' for the chain." % (name, target_input))
                lines.append(edit.connect(variables[name], target_input, variables[previous]))
            entry_name, entry_input = chain[0]
            if entry_input and not edit.has_input(tools[entry_name], entry_input):
                raise OperationError("%s has no input '%s' for the chain." % (entry_name, entry_input))
            if not entry_input and call(tools[entry_name], "FindMainInput", None, 1) is None:
                raise OperationError(
                    "%s has no image input, so the clip cannot flow through it. Generators such as TextPlus "
                    "or Background go over the clip with a Merge: chain the Merge (input Background) and "
                    "connect its Foreground with {\"from\": \"%s\"}." % (entry_name, entry_name))
            self._tag_chain(list(tools.values()), tools[entry_name], tools[chain[-1][0]], entry_input, feeds)
            lua_ok = edit.lua(lines)
            if lua_ok is False:
                raise OperationError(self._fusion_summary(None, False))
            fusion_chain = edit.finish()
        except Exception:
            edit.rollback()
            raise
        finally:
            _comp_close(comp)
        verified = self._fusion_checks(edit, checks) if checks else None
        result = {"item": info["id"], "stage": key, "nodes": {name: edit.name(tool) for name, tool in tools.items()},
                  "chain": [edit.name(tools[name]) for name, _ in chain], "frames": "clip offsets",
                  "fusion_start_frame": edit.t0, "verified": verified, "fusion_chain": fusion_chain}
        if chain_inferred:
            result["chain_inferred"] = "No chain given: %s is the only node nothing else reads, so it is the output." % (
                chain[0][0])
        note = self._fusion_summary(verified, lua_ok)
        if key in (fusion_chain or {}).get("left_in_place", []):
            note = "%s sits before one of your own Fusion nodes, so it was left in place." % key
        result["note"] = note or "Built %s with %d node(s); it sits in the bridge chain before MediaOut." % (key, len(tools))
        if edit.native_fallback:
            result["route"] = ("direct node calls: Resolve did not run the Lua script in this clip's comp, so the "
                               "bridge set the inputs through the node handles instead")
        return result

    def _op_fusion_keyframe_input(self, params):
        """Set or keyframe one input of any tool in a clip's comp."""
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index") or "video")
        tool_name = params.get("tool")
        input_id = str(params.get("input") or "")
        if not tool_name or not input_id:
            raise OperationError("tool and input are required. Call inspect_fusion and fusion_inspect_tool for names.")
        if params.get("clear"):
            return self._fusion_clear_keys(item, info, tool_name, input_id, params)
        if params.get("keyframes") is not None:
            spec = {"keyframes": params["keyframes"], "interpolation": params.get("interpolation")}
        elif "value" in params:
            spec = {"value": params["value"]}
        else:
            raise OperationError("Pass keyframes (a list of {frame|timeline_frame, value}) or a static value.")
        comp = self._fusion_comp(item, info, create=False)
        if comp is None:
            raise OperationError("%s has no Fusion composition. Build one with fusion_build first." % info["id"])
        node = self._fusion_tool(comp, tool_name)
        replace = params.get("replace", True) is not False
        previous = self._input_modifier(node, input_id)
        _comp_open(comp)
        edit = FusionEdit(self, comp, "keyframe")
        edit.allow_native_fallback = True
        try:
            lines, checks, source = self._fusion_assign(edit, "aib_tool", node, input_id, spec, item,
                                                        _fusion_name(node), replace=replace)
            if source is not None:
                raise OperationError("Use fusion_build to change connections; this tool sets values and keyframes.")
            lua_ok = edit.lua([edit.bind("aib_tool", node)] + lines)
            if lua_ok is False:
                raise OperationError("Fusion stopped before the keyframe script finished; nothing was confirmed.")
        finally:
            _comp_close(comp)
        verified = self._fusion_checks(edit, checks)
        if "keyframes" not in spec:
            mode = "static" + (" (removed the %s animation)" % previous if previous else "")
        elif previous and not replace:
            mode = "merged into the existing %s" % previous
        else:
            mode = "replaced the existing %s" % previous if previous else "created"
        result = {"item": info["id"], "tool": _fusion_name(node), "input": input_id, "frames": "clip offsets",
                  "fusion_start_frame": edit.t0, "verified": verified, "previous_modifier": previous, "mode": mode,
                  "keyframe_frames": [check[2] for check in checks] if "keyframes" in spec else None}
        if edit.native_fallback:
            result["route"] = ("direct node calls: Resolve did not run the Lua script in this clip's comp, so the "
                               "bridge set the values through the node handles instead")
        note = self._fusion_summary(verified, lua_ok)
        if note:
            result["note"] = note
        return result

    def _fusion_clear_keys(self, item, info, tool_name, input_id, params):
        """Remove an input's animation (spline, path or other modifier) and leave it at a static value."""
        comp = self._fusion_comp(item, info, create=False)
        if comp is None:
            raise OperationError("%s has no Fusion composition; there are no keyframes to clear." % info["id"])
        node = self._fusion_tool(comp, tool_name)
        where = _fusion_name(node)
        port = _named_input(node, input_id)
        if port is None and call(node, "GetInput", None, input_id) is None:
            raise OperationError("%s has no input '%s' on this build. Call fusion_inspect_tool to list its inputs."
                                 % (where, input_id))
        if call(port, "GetAttrs", None, "INPS_DataType") in ("Image", "Mask"):
            raise OperationError("%s.%s is an image connection, not keyframes; use fusion_build to rewire it."
                                 % (where, input_id))
        previous = self._input_modifier(node, input_id)
        if previous is None:
            return {"item": info["id"], "tool": where, "input": input_id, "cleared": False, "previous_modifier": None,
                    "note": "%s.%s is not animated; nothing was changed." % (where, input_id)}
        t0 = float(call(comp, "GetAttrs", 0.0, "COMPN_RenderStart") or 0.0)
        if "value" in params and params["value"] is not None:
            value, source = params["value"], "given value"
        else:
            frame = int(params.get("frame") or 0)
            value, source = call(node, "GetInput", None, input_id, t0 + frame), "its value at clip frame %d" % frame
            if isinstance(value, dict):
                value = [value.get(i, value.get(str(i))) for i in (1, 2)]
        _comp_open(comp)
        edit = FusionEdit(self, comp, "keyframe")
        edit.allow_native_fallback = True
        try:
            lines = [edit.bind("aib_tool", node)]
            kind = None
            try:
                kind = self._value_kind(value, "%s.%s" % (where, input_id)) if value is not None else None
            except OperationError:
                kind = None
            if kind == "number":
                lines += edit.static("aib_tool", input_id, float(value))
            elif kind == "point":
                lines += edit.static_point("aib_tool", input_id, float(value[0]), float(value[1]))
            elif kind is not None:
                lines += edit.static_text("aib_tool", input_id, value)
            else:
                lines.append(edit.connect("aib_tool", input_id, None))
                source = "Fusion's default (the animated value could not be read)"
            lua_ok = edit.lua(lines)
            if lua_ok is False:
                raise OperationError("Fusion stopped before clearing %s.%s; nothing was confirmed." % (where, input_id))
        finally:
            _comp_close(comp)
        remaining = self._input_modifier(node, input_id)
        verified = None if lua_ok is None else remaining is None
        result = {"item": info["id"], "tool": where, "input": input_id, "cleared": remaining is None,
                  "previous_modifier": previous, "static_value": value if kind else None, "static_from": source,
                  "verified": verified}
        if remaining is not None:
            result["note"] = "%s.%s is still driven by %s; inspect it with fusion_inspect_tool." % (where, input_id, remaining)
        else:
            result["note"] = "Removed the %s animation from %s.%s; it now holds %s." % (previous, where, input_id, source)
        return result

    def _op_fusion_inspect_tool(self, params):
        """Read-only: inputs, values and animation of one Fusion tool."""
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index") or "video")
        comp = self._fusion_comp(item, info, create=False)
        if comp is None:
            raise OperationError("%s has no Fusion composition." % info["id"])
        node = self._fusion_tool(comp, params.get("tool") or "")
        t0 = call(comp, "GetAttrs", 0.0, "COMPN_RenderStart") or 0.0
        frame = int(params.get("frame") or 0)
        wanted = {str(name) for name in params.get("inputs") or []}
        search = str(params.get("search") or "").lower()
        limit = max(1, min(int(params.get("max_inputs") or 120), 400))
        listed = call(node, "GetInputList", None) or {}
        inputs, skipped = [], 0
        for inp in (listed.values() if isinstance(listed, dict) else listed):
            attrs = call(inp, "GetAttrs", None)
            attrs = attrs if isinstance(attrs, dict) else {"INPS_ID": call(inp, "GetAttrs", None, "INPS_ID")}
            input_id = str(attrs.get("INPS_ID") or "")
            name = attrs.get("INPS_Name")
            if wanted and input_id not in wanted:
                continue
            if search and search not in input_id.lower() and search not in str(name or "").lower():
                continue
            if len(inputs) >= limit:
                skipped += 1
                continue
            entry = {"id": input_id, "name": name, "type": attrs.get("INPS_DataType")}
            connected = call(inp, "GetConnectedOutput", None)
            source = call(connected, "GetTool", None) if connected is not None else None
            if source is not None:
                reg_id = call(source, "GetAttrs", None, "TOOLS_RegID")
                entry["connected_to"] = _fusion_name(source)
                if entry["type"] not in ("Image", "Mask") and reg_id and reg_id != "MediaIn":
                    # A non-image input fed by a tool is driven by a modifier (spline, path, expression, shake...).
                    entry["animated"] = True
                    entry["modifier"] = reg_id
                    keys = call(source, "GetKeyFrames", None)
                    if isinstance(keys, dict):
                        entry["keyframes"] = sorted(float(v) - float(t0) for v in keys.values() if isinstance(v, (int, float)))
            if entry["type"] in self.FUSION_READ_TYPES or entry["type"] is None:
                value = call(node, "GetInput", None, input_id, float(t0) + frame)
                if value is None or isinstance(value, (bool, int, float, str, dict, list, tuple)):
                    entry["value"] = value
            inputs.append(entry)
        outputs = call(node, "GetOutputList", None) or {}
        return {"item": info["id"], "tool": _fusion_name(node), "reg_id": call(node, "GetAttrs", None, "TOOLS_RegID"),
                "frame": frame, "frames": "clip offsets", "fusion_start_frame": t0,
                "bridge_stage": (_fusion_role(node) or (None,))[0],
                "inputs": inputs, "inputs_not_listed": skipped,
                "outputs": [str(call(out, "GetAttrs", "", "OUTS_ID"))
                            for out in (outputs.values() if isinstance(outputs, dict) else outputs)]}

    def _fusion_execute(self, comp, script_lines, what):
        """Run Lua through comp:Execute and return (finished, error). Never through the Py3 worker."""
        if self.transport == "console":
            raise OperationError(
                "%s needs comp:Execute, which the Python Console worker cannot run safely. Use Start AI Bridge (Lua) "
                "on Free 21.1+ or Studio direct attach, or use fusion_build / fusion_keyframe_input." % what)
        nonce = uuid.uuid4().hex
        done_key, error_key = FUSION_LUA_KEY + "Done", FUSION_LUA_KEY + "Error"
        call(comp, "SetData", None, done_key, "pending")
        call(comp, "SetData", None, error_key, None)
        guarded = call(comp, "GetData", None, done_key) == "pending"
        guard = _comp_guard(comp, done_key) if guarded else []
        script = "\n".join(guard + ["local aib_ok, aib_error = pcall(function()"] + list(script_lines) + [
            "end)",
            'if not aib_ok then comp:SetData("%s", tostring(aib_error)) end' % error_key,
            'comp:SetData("%s", "%s")' % (done_key, nonce)])
        call(comp, "Execute", None, script)
        done = call(comp, "GetData", None, done_key)
        if guard and done == "pending":
            raise OperationError(
                "%s did not run in this clip's Fusion composition. Resolve ran comp:Execute somewhere else (usually "
                "the comp open on the Fusion page) or not at all; the bridge's guard stopped it before it changed "
                "anything. Nothing was changed. Open this clip on the Fusion page and retry, or use fusion_build / "
                "fusion_keyframe_input, which fall back to direct node calls." % what)
        return done == nonce, call(comp, "GetData", None, error_key)

    def _op_fusion_apply_setting(self, params):
        """Paste Fusion .setting / copied-node text into a clip's comp."""
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index") or "video")
        text = params.get("setting")
        if not isinstance(text, str) or not text.strip():
            raise OperationError("setting must be the text of a Fusion .setting file or copied nodes ({ Tools = ordered() {...} }).")
        if len(text.encode("utf-8")) > FUSION_TEXT_LIMIT:
            raise OperationError("setting is %d bytes; the limit is %d. Paste a smaller macro or load it on the Fusion page."
                                 % (len(text.encode("utf-8")), FUSION_TEXT_LIMIT))
        insert = params.get("insert_into_chain")
        if insert is not None and not (isinstance(insert, dict) and insert.get("entry") and insert.get("exit")):
            raise OperationError("insert_into_chain needs {entry, exit}: names of pasted tools to wire before MediaOut.")
        tag = self._fusion_identifier(params.get("name"), "name", fallback="Setting")
        key = "custom:" + tag
        comp = self._fusion_comp(item, info)
        before = {_fusion_name(tool) for tool in _fusion_tools(comp)}
        _comp_open(comp)
        edit = FusionEdit(self, comp, key)
        pasted = []
        try:
            if insert is not None:
                for old in list(edit.nodes.values()):
                    edit._retire(old)
                edit.nodes = {}
            finished, error = self._fusion_execute(comp, [
                "local aib_settings = bmd.readstring(%s)" % _lua_long_string(text),
                'if type(aib_settings) ~= "table" then error("Fusion could not parse the setting text") end',
                "comp:Paste(aib_settings)"], "fusion_apply_setting")
            if error:
                raise OperationError("Fusion refused the setting: %s" % error)
            pasted = [tool for tool in _fusion_tools(comp) if _fusion_name(tool) not in before and not _is_retired(tool)]
            edit.created += pasted
            if not pasted:
                raise OperationError("Fusion pasted no tools%s. Check the setting text."
                                     % ("" if finished else " and did not report finishing"))
            by_name = {_fusion_name(tool): tool for tool in pasted}
            fusion_chain = None
            if insert is not None:
                missing = [str(insert[k]) for k in ("entry", "exit") if str(insert[k]) not in by_name]
                if missing:
                    raise OperationError("insert_into_chain names %s, but the pasted tools are: %s."
                                         % (", ".join(missing), ", ".join(sorted(by_name))))
                for name, tool in by_name.items():
                    edit._mark(tool, "n_" + name, name)
                    edit.nodes["n_" + name] = tool
                entry_input = insert.get("entry_input")
                if entry_input and not edit.has_input(by_name[str(insert["entry"])], str(entry_input)):
                    raise OperationError("%s has no input '%s'." % (insert["entry"], entry_input))
                self._tag_chain(pasted, by_name[str(insert["entry"])], by_name[str(insert["exit"])],
                                str(entry_input) if entry_input else None, {})
                fusion_chain = edit.finish()
            else:
                edit.created = []
        except Exception:
            edit.rollback()
            raise
        finally:
            _comp_close(comp)
        result = {"item": info["id"], "pasted_tools": sorted(by_name), "finished": finished,
                  "stage": key if insert is not None else None, "fusion_chain": fusion_chain}
        result["note"] = ("Pasted %d tool(s) and wired %s before MediaOut." % (len(pasted), key) if insert is not None
                          else "Pasted %d tool(s). They are not connected to the clip; pass insert_into_chain to wire them."
                          % len(pasted))
        declared = setting_tools(text)
        result["declared_tools"] = [{"name": t["name"], "type": t["reg_id"]} for t in declared][:200]
        converted = fusion_conversions(declared, comp)
        if converted:
            result["converted_tools"] = converted
            result["warning"] = _conversion_warning(converted, "Paste")
        return result

    LUA_JSON = r'''local function aib_json(v, depth)
  local t = type(v)
  if v == nil then return "null" end
  if t == "boolean" then return tostring(v) end
  if t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return string.format("%.14g", v)
  end
  if t == "table" and depth < 6 then
    local parts, n = {}, #v
    if n > 0 then
      for i = 1, math.min(n, 2000) do parts[#parts + 1] = aib_json(v[i], depth + 1) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local count = 0
    for k, item in pairs(v) do
      count = count + 1
      if count > 2000 then break end
      parts[#parts + 1] = aib_json(tostring(k), depth + 1) .. ":" .. aib_json(item, depth + 1)
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  local s = (string.gsub(tostring(v), '[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end))
  return '"' .. s .. '"'
end'''

    def _op_fusion_run_lua(self, params):
        """Advanced: run a Lua snippet in a clip's comp and return its JSON-able result."""
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index") or "video")
        code = params.get("code")
        if not isinstance(code, str) or not code.strip():
            raise OperationError("code is required: Lua that runs with `comp` set to the clip's composition.")
        if len(code.encode("utf-8")) > FUSION_TEXT_LIMIT:
            raise OperationError("code exceeds %d bytes." % FUSION_TEXT_LIMIT)
        if "GetCurrentComp" in code:
            raise OperationError("fusion_run_lua refuses code that calls GetCurrentComp: that is whichever comp the "
                                 "Fusion page shows, not %s. Use `comp`, which is bound to this clip's composition. "
                                 "Nothing was run." % info["id"])
        comp = self._fusion_comp(item, info, create=params.get("create_comp", True) is not False)
        if comp is None:
            raise OperationError("%s has no Fusion composition and create_comp is false." % info["id"])
        result_key = FUSION_LUA_KEY + "Result"
        call(comp, "SetData", None, result_key, None)
        _comp_open(comp)
        try:
            finished, error = self._fusion_execute(comp, [
                self.LUA_JSON,
                "local aib_load = loadstring or load",
                # A loaded chunk sees the global environment, where `comp` is nil or the Fusion page's comp.
                # Bind this clip's comp as a chunk-local (same line, so error line numbers stay the user's).
                "local aib_chunk, aib_syntax = aib_load(\"local comp = ...; \" .. %s, \"fusion_run_lua\")"
                % _lua_long_string(code),
                'if not aib_chunk then error("syntax: " .. tostring(aib_syntax)) end',
                'comp:SetData("%s", aib_json(aib_chunk(comp), 0))' % result_key], "fusion_run_lua")
        finally:
            _comp_close(comp)
        if error:
            raise OperationError("Lua failed in %s: %s" % (info["id"], error))
        raw = call(comp, "GetData", None, result_key)
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            value = raw
        result = {"item": info["id"], "finished": bool(finished), "result": value}
        if not finished:
            result["note"] = ("Fusion did not confirm the script finished. It may have run in part; "
                              "check the Resolve Console and inspect_fusion before retrying.")
        return result

    # ------------------------------------------------- fusion graph and copy

    # Animation modifiers hang off an input rather than the flow, but GetToolList can list them beside nodes.
    FUSION_MODIFIER_IDS = ("BezierSpline", "XYPath", "PolyPath", "Path", "Expression")
    # A link reads a tool's main output: "Tool", "Tool.Output" or (mask tools) "Tool.Mask".
    GRAPH_MAIN_OUTPUTS = ("", "output", "mask")

    @staticmethod
    def _graph_port(text, what):
        """Split "Tool.Port" into (tool, port); port is "" when left out."""
        if not isinstance(text, str) or not text.strip():
            raise OperationError('%s must be a string such as "Blur.Output" or "Merge.Background". Nothing was changed.'
                                 % what)
        tool, _, port = text.strip().partition(".")
        if not tool.strip():
            raise OperationError("%s %r names no tool. Nothing was changed." % (what, text))
        return tool.strip(), port.strip()

    def _graph_plan(self, params, names, specs):
        """Validate links and output without touching the comp: (links, output, warnings)."""
        raw_links = params.get("links")
        if not isinstance(raw_links, list) or not raw_links:
            raise OperationError('links must be a non-empty list of ["Source.Output", "Node.Input"] pairs; one of them '
                                 'must come from "chain" (the clip as it reaches this stage). Nothing was changed.')
        links, targets, edges = [], set(), {name: set() for name in names}
        for index, link in enumerate(raw_links):
            if isinstance(link, dict):
                pair = (link.get("from"), link.get("to"))
            elif isinstance(link, (list, tuple)) and len(link) == 2:
                pair = tuple(link)
            else:
                raise OperationError('links[%d] must be ["Source.Output", "Node.Input"] or {"from", "to"}. '
                                     "Nothing was changed." % index)
            source, out_port = self._graph_port(pair[0], "links[%d] source" % index)
            target, in_port = self._graph_port(pair[1], "links[%d] target" % index)
            if out_port.lower() not in self.GRAPH_MAIN_OUTPUTS:
                raise OperationError(
                    'links[%d] reads %s.%s: fusion_graph links a tool\'s main output only (write "%s" or "%s.Output"). '
                    "Nothing was changed." % (index, source, out_port, source, source))
            if source.lower() == "chain":
                kind = "chain"
            elif source.lower() in ("mediain", "media_in"):
                kind = "mediain"
            else:
                local = self._fusion_identifier(source, "links[%d] source" % index)
                kind, source = ("node", local) if local in names else ("external", source)
            node = self._fusion_identifier(target, "links[%d] target" % index)
            if node not in names:
                raise OperationError(
                    "links[%d] feeds %s, which is not one of this graph's nodes (%s). fusion_graph wires only its own "
                    "nodes and never rewires yours. Nothing was changed." % (index, target, ", ".join(names)))
            if in_port and not re.fullmatch(r"[A-Za-z_]\w*", in_port):
                raise OperationError("links[%d] input %r is not a Fusion input id. Nothing was changed." % (index, in_port))
            slot = (node, in_port or None)
            if slot in targets:
                raise OperationError("Two links feed %s.%s; an input takes one source. Nothing was changed."
                                     % (node, in_port or "<main input>"))
            targets.add(slot)
            inputs = specs[names.index(node)].get("inputs") or {}
            if in_port and in_port in inputs:
                raise OperationError("%s.%s has both a value in inputs and a link; give it one. Nothing was changed."
                                     % (node, in_port))
            if kind == "node":
                if source == node:
                    raise OperationError("links[%d] feeds %s into itself. Nothing was changed." % (index, node))
                edges[node].add(source)
            links.append((kind, source, node, in_port or None))
        if not any(kind == "chain" for kind, _, _, _ in links):
            raise OperationError(
                'No link comes from "chain". Link "chain" into the input that should receive the clip as it reaches '
                "this stage (after your own nodes and earlier bridge effects); \"MediaIn\" is the untouched source and "
                "would skip them. Nothing was changed.")
        visiting, done = set(), set()

        def visit(name, path):
            if name in done:
                return
            if name in visiting:
                raise OperationError("The links loop: %s. Nothing was changed." % " <- ".join(path + [name]))
            visiting.add(name)
            for upstream in sorted(edges[name]):
                visit(upstream, path + [name])
            visiting.discard(name)
            done.add(name)

        for name in names:
            visit(name, [])
        raw_output = params.get("output") or (names[0] if len(names) == 1 else None)
        if not raw_output:
            raise OperationError("output is required with several nodes: name the node that feeds MediaOut. "
                                 "Nothing was changed.")
        output = self._fusion_identifier(raw_output, "output")
        if output not in names:
            raise OperationError("output names %s, which is not one of nodes: %s. Nothing was changed."
                                 % (raw_output, ", ".join(names)))
        feeding, pending = {output}, [output]
        while pending:
            for upstream in edges[pending.pop()]:
                if upstream not in feeding:
                    feeding.add(upstream)
                    pending.append(upstream)
        warnings = []
        unused = [name for name in names if name not in feeding]
        if unused:
            warnings.append("%s do(es) not feed %s, so they have no effect on the picture." % (", ".join(unused), output))
        if not any(kind == "chain" and node in feeding for kind, _, node, _ in links):
            warnings.append('The "chain" link does not reach %s, so the clip as it arrives at this stage is not part '
                            "of the picture this graph sends to MediaOut." % output)
        return links, output, warnings

    def _comp_names(self, item):
        names = call(item, "GetFusionCompNameList", None) or []
        if isinstance(names, dict):
            names = [names[key] for key in sorted(names, key=lambda k: float(k) if str(k).lstrip("-").isdigit() else 0)]
        return [str(name) for name in names]

    def _remove_created_comp(self, item, name, count_before):
        """Take off a comp this call added; returns a sentence saying what happened."""
        if name and call(item, "DeleteFusionCompByName", False, name) and int(
                call(item, "GetFusionCompCount", 0) or 0) == count_before:
            return "The Fusion composition this call created (%s) was removed again." % name
        return ("Resolve did not remove the Fusion composition this call created (%s); it holds only MediaIn and "
                "MediaOut, so the clip passes through unchanged, but the clip now has a Fusion comp. Resolve will not "
                "delete a comp while it is loaded (open on the Fusion page). Delete it from the clip on the Fusion page "
                "if you do not want it; a later fusion_graph or fusion_build reuses it instead of adding another."
                % (name or "unnamed"))

    def _op_fusion_graph(self, params):
        """A declarative node graph, wired by explicit links, as one custom stage before MediaOut."""
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index") or "video")
        tag = self._fusion_identifier(params.get("name"), "name", fallback="Graph")
        key = "custom:" + tag
        if params.get("reset"):
            return self._custom_reset(item, info, key)
        specs = params.get("nodes")
        if not isinstance(specs, list) or not specs:
            raise OperationError("nodes must be a non-empty list of {name, type, inputs}. Nothing was changed.")
        names = []
        for spec in specs:
            if not isinstance(spec, dict) or not spec.get("type"):
                raise OperationError("Each node needs a 'type' (a Fusion RegID such as Merge) and a 'name'. "
                                     "Nothing was changed.")
            names.append(self._fusion_identifier(spec.get("name") or spec["type"], "node name"))
            inputs = spec.get("inputs") or {}
            if not isinstance(inputs, dict):
                raise OperationError("Node %s inputs must be an object of InputID: value. Nothing was changed." % names[-1])
            for input_id, value in inputs.items():
                if isinstance(value, dict) and "from" in value:
                    raise OperationError('%s.%s uses {"from": ...}; fusion_graph wires inputs only through links, '
                                         'e.g. ["%s.Output", "%s.%s"]. Nothing was changed.'
                                         % (names[-1], input_id, value["from"], names[-1], input_id))
                try:
                    if isinstance(value, dict) and "keyframes" in value:
                        self._fusion_key_series(item, value["keyframes"], value.get("interpolation"),
                                                "%s.%s" % (names[-1], input_id))
                    elif not isinstance(value, dict):
                        self._value_kind(value, "%s.%s" % (names[-1], input_id))
                except OperationError as exc:
                    raise OperationError("%s Nothing was changed." % str(exc).rstrip()) from exc
        if len(set(names)) != len(names):
            raise OperationError("Node names must be unique within one fusion_graph call. Nothing was changed.")
        links, output, warnings = self._graph_plan(params, names, specs)

        count_before = int(call(item, "GetFusionCompCount", 0) or 0)
        names_before = self._comp_names(item)
        comp = self._fusion_comp(item, info, create=False)
        externals = {}
        for kind, source, _, _ in links:
            if kind != "external" or source in externals:
                continue
            if comp is None:
                raise OperationError("%s has no Fusion composition yet, so there is no tool %s to link from. "
                                     "Nothing was changed." % (info["id"], source))
            tool = self._fusion_tool(comp, source)
            owned = _fusion_role(tool)
            if owned and owned[0] == key:
                raise OperationError("%s belongs to the %s stage this call replaces; link it inside the graph instead. "
                                     "Nothing was changed." % (source, key))
            externals[source] = tool
        if comp is not None and any(kind == "mediain" for kind, _, _, _ in links) and _fusion_endpoint(comp, "MediaIn") is None:
            raise OperationError("This comp has no MediaIn to link from. Nothing was changed.")
        created, created_name = comp is None, None
        if created:
            comp = self._fusion_comp(item, info)
            added = [name for name in self._comp_names(item) if name not in names_before]
            created_name = added[0] if len(added) == 1 else None
        try:
            _comp_open(comp)
            edit = FusionEdit(self, comp, key)
            edit.allow_native_fallback = True
            try:
                for old in list(edit.nodes.values()):
                    edit._retire(old)
                edit.nodes = {}
                tools, variables, lines, checks = {}, {}, [], []
                for index, (spec, name) in enumerate(zip(specs, names)):
                    tools[name] = edit.node("n_" + name, (str(spec["type"]),), "%s_%s" % (tag, name))
                    variables[name] = "aib_n%d" % index
                    lines.append(edit.bind(variables[name], tools[name]))
                for spec, name in zip(specs, names):
                    for input_id, value in (spec.get("inputs") or {}).items():
                        more, more_checks, _ = self._fusion_assign(edit, variables[name], tools[name], str(input_id),
                                                                   value, item, "%s (%s)" % (name, spec["type"]))
                        lines += more
                        checks += more_checks
                bound, made, slots, feeds = {}, [], set(), {}
                entry = entry_input = None
                for kind, source, name, in_port in links:
                    node = tools[name]
                    input_id = in_port or self._main_input_id(node)
                    if not edit.has_input(node, input_id):
                        raise OperationError("%s (%s) has no input '%s' on this build. Call fusion_inspect_tool to list "
                                             "its inputs." % (name, call(node, "GetAttrs", None, "TOOLS_RegID"), input_id))
                    if (name, input_id) in slots:
                        raise OperationError("Two links feed %s.%s; an input takes one source." % (name, input_id))
                    slots.add((name, input_id))
                    if kind == "chain":
                        if entry is None:
                            entry, entry_input = node, input_id
                        else:
                            feeds.setdefault(_fusion_name(node), []).append(input_id)
                        made.append((kind, None, node, input_id))
                        continue
                    if kind == "node":
                        source_tool, source_var = tools[source], variables[source]
                    else:
                        source_tool = (_fusion_endpoint(comp, "MediaIn") if kind == "mediain" else externals[source])
                        if source_tool is None:
                            raise OperationError("This comp has no MediaIn to link from.")
                        source_var = bound.get(_fusion_name(source_tool))
                        if source_var is None:
                            source_var = bound[_fusion_name(source_tool)] = "aib_x%d" % len(bound)
                            lines.insert(0, edit.bind(source_var, source_tool))
                    lines.append(edit.connect(variables[name], input_id, source_var))
                    made.append((kind, source_tool, node, input_id))
                self._tag_chain(list(tools.values()), entry, tools[output], entry_input, feeds)
                lua_ok = edit.lua(lines)
                if lua_ok is False:
                    raise OperationError(self._fusion_summary(None, False))
                fusion_chain = edit.finish()
            except Exception:
                edit.rollback()
                raise
            finally:
                _comp_close(comp)
        except Exception as exc:
            if not created:
                raise
            note = self._remove_created_comp(item, created_name, count_before)
            raise OperationError("%s %s" % (str(exc).rstrip(), note)) from exc

        verified_values = self._fusion_checks(edit, checks) if checks else None
        links_made = []
        for kind, source_tool, node, input_id in made:
            port = _named_input(node, input_id)
            actual = _output_name(call(port, "GetConnectedOutput", None)) if port is not None else None
            if kind == "chain":
                good = None if port is None else actual is not None
                label = "chain (%s)" % actual if actual else "chain"
            else:
                label = "%s.Output" % _fusion_name(source_tool)
                good = None if port is None else actual == _fusion_name(source_tool)
            links_made.append({"from": label, "to": "%s.%s" % (_fusion_name(node), input_id), "verified": good})
        placed = (fusion_chain or {}).get("stages") or []
        feeds_media_out = None
        if key in placed and placed[-1] == key:
            media_out = _fusion_endpoint(comp, "MediaOut")
            feeds_media_out = _output_name(_main_source(media_out)) == _fusion_name(tools[output]) if media_out else None
        elif key in placed:
            warnings.append("%s feeds %s, which then feeds MediaOut; bridge stages after custom ones keep their place."
                            % (_fusion_name(tools[output]), ", ".join(placed[placed.index(key) + 1:])))
        if key in (fusion_chain or {}).get("left_in_place", []):
            warnings.append("%s sits before one of your own Fusion nodes, so it was left in place." % key)
        outcomes = ([verified_values] if checks else []) + [link["verified"] for link in links_made]
        if feeds_media_out is not None:
            outcomes.append(feeds_media_out)
        verified = False if False in outcomes else None if None in outcomes or not outcomes else True
        if self.transport == "console":
            route = "direct node calls (the Python Console worker cannot run comp:Execute)"
        elif edit.native_fallback:
            route = ("direct node calls: Resolve did not run the Lua script in this clip's comp, so the bridge set the "
                     "inputs and links through the node handles instead")
        else:
            route = "comp:Execute Lua" + ("" if lua_ok else " (Resolve did not confirm the script finished)")
        result = {"item": info["id"], "stage": key, "nodes": {name: edit.name(tool) for name, tool in tools.items()},
                  "output": edit.name(tools[output]), "links_made": links_made, "feeds_media_out": feeds_media_out,
                  "verified": verified, "fusion_chain": fusion_chain, "route": route, "comp_created": created,
                  "frames": "clip offsets", "fusion_start_frame": edit.t0}
        if warnings:
            result["warnings"] = warnings
        note = self._fusion_summary(verified_values, lua_ok) if checks else None
        if verified is False:
            note = "Something did not read back as requested (see links_made / verified). Inspect it with inspect_fusion."
        result["note"] = note or "Built %s with %d node(s) and %d link(s); %s feeds the chain toward MediaOut." % (
            key, len(tools), len(links_made), edit.name(tools[output]))
        return result

    def _comp_contents(self, comp):
        """(your node names, bridge stage keys) in a comp, leaving out its own MediaIn/MediaOut and modifiers."""
        endpoints = {_fusion_name(tool) for tool in (_fusion_endpoint(comp, "MediaIn"), _fusion_endpoint(comp, "MediaOut"))
                     if tool is not None}
        user, stages = [], set()
        for tool in _fusion_tools(comp):
            name = _fusion_name(tool)
            if name in endpoints or call(tool, "GetAttrs", None, "TOOLS_RegID") in self.FUSION_MODIFIER_IDS:
                continue
            owned = _fusion_role(tool)
            if owned or _is_retired(tool):
                stages.add(owned[0] if owned else "retired bridge node")
            else:
                user.append(name)
        return sorted(user), sorted(stages)

    def _op_fusion_copy_comp(self, params):
        """Copy one clip's Fusion comp onto other clips through ExportFusionComp -> ImportFusionComp.

        There is no verified file-free route: Resolve's scripting API has no comp-to-comp copy, and a comp:Copy /
        comp:Paste script through comp:Execute can run in the wrong comp (seen on Resolve 21.2 Free). The export and
        import round trip works live on 21.2 Free, so the comp goes through a .comp file in the bridge work folder.
        """
        mode = str(params.get("mode") or "replace").strip().lower()
        if mode not in ("replace", "add"):
            raise OperationError('mode must be "replace" (the copy becomes the clip\'s only comp) or "add" (stack it as '
                                 "another comp on the clip). Nothing was changed.")
        ids = params.get("to_item_ids")
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, list) or not ids:
            raise OperationError("to_item_ids must list at least one target clip id. Nothing was changed.")
        approved = params.get("user_approved") is True
        try:
            index = int(params.get("comp_index") or 1)
        except (TypeError, ValueError):
            raise OperationError("comp_index must be a whole number (1 = the clip's first comp). Nothing was changed.")
        source, source_info = self._resolve_one_item(params.get("from_item_id"), track_hint="video")
        count = int(call(source, "GetFusionCompCount", 0) or 0)
        if count < 1:
            raise OperationError("%s has no Fusion composition to copy. Nothing was changed." % source_info["id"])
        if not 1 <= index <= count:
            raise OperationError("comp_index %d is outside 1..%d for %s. Nothing was changed."
                                 % (index, count, source_info["id"]))
        plans, refusals, seen = [], [], set()
        for item, info in self._find_timeline_items(ids):
            if info["id"] in seen:
                continue
            seen.add(info["id"])
            if info["id"] == source_info["id"]:
                raise OperationError("to_item_ids includes the source clip %s. Nothing was changed." % info["id"])
            if info.get("track_type") != "video":
                raise OperationError("%s is on a %s track; only video clips hold Fusion comps. Nothing was changed."
                                     % (info["id"], info.get("track_type")))
            before = int(call(item, "GetFusionCompCount", 0) or 0)
            plan = {"item": item, "info": info, "before": before, "names": self._comp_names(item),
                    "user_nodes": [], "bridge_stages": []}
            if mode == "replace" and before > 1:
                refusals.append('%s already has %d Fusion comps; replace works only on a clip with none or one, and the '
                                'bridge never stacks another comp silently. Use mode="add" to stack the copy, or remove '
                                "the extra comps on the Fusion page." % (info["id"], before))
            elif mode == "replace" and before == 1:
                comp = call(item, "GetFusionCompByIndex", None, 1)
                if comp is None:
                    refusals.append("%s reports a Fusion comp the bridge cannot open, so it cannot check what replace "
                                    "would remove." % info["id"])
                else:
                    plan["user_nodes"], plan["bridge_stages"] = self._comp_contents(comp)
                    held = (["your nodes " + ", ".join(plan["user_nodes"][:20])] if plan["user_nodes"] else []) + (
                        ["bridge effects " + ", ".join(plan["bridge_stages"])] if plan["bridge_stages"] else [])
                    if held and not approved:
                        refusals.append(
                            '%s\'s comp holds %s; replacing it deletes them. Ask the user, then retry with '
                            'user_approved=true (the old comp is exported to a backup file first), or use mode="add".'
                            % (info["id"], " and ".join(held)))
                    elif len(plan["names"]) != 1:
                        refusals.append("Resolve did not name %s's comp, so the bridge could not remove it after the "
                                        "import." % info["id"])
            plans.append(plan)
        if refusals:
            raise OperationError(" ".join(refusals) + " Nothing was changed.")
        work = self._work_dir("comps")
        comp_file = work / ("source_%s_%d.comp" % (uuid.uuid4().hex[:10], index))
        if not (call(source, "ExportFusionComp", False, str(comp_file), index) and comp_file.exists()):
            raise OperationError("Resolve did not export %s's Fusion comp %d (ExportFusionComp to %s). Nothing was "
                                 "changed." % (source_info["id"], index, comp_file))
        declared = setting_tools(comp_file.read_text(encoding="utf-8", errors="replace"))
        targets = []
        for plan in plans:
            try:
                targets.append(self._copy_comp_onto(plan, comp_file, declared, mode, work))
            except Exception as exc:  # Keep the other targets' reports; this one's state is unknown.
                after = int(call(plan["item"], "GetFusionCompCount", 0) or 0)
                targets.append({"item": plan["info"]["id"], "imported": None, "comp_count_before": plan["before"],
                                "comp_count_after": after, "converted_tools": [],
                                "warning": "Unexpected error while copying (%s); check this clip on the Fusion page." % exc})
        done = sum(1 for target in targets if target["imported"] is True)
        result = {"from_item": source_info["id"], "comp_index": index, "mode": mode, "comp_file": str(comp_file),
                  "declared_tools": [{"name": t["name"], "type": t["reg_id"]} for t in declared][:200],
                  "targets": targets, "route": "ExportFusionComp -> ImportFusionComp through a .comp file",
                  "note": "Copied the comp onto %d of %d clip(s)." % (done, len(targets)),
                  "unverified": ("Whether the copied comp's MediaIn shows each target clip's own media (rather than "
                                 "the source clip's) has not been checked in a live Resolve; look at each target on "
                                 "the Fusion page.")}
        if any(target.get("warning") for target in targets):
            result["note"] += " Some targets have warnings; read them before telling the user the copy is intact."
        return result

    def _copy_comp_onto(self, plan, comp_file, declared, mode, work):
        item, info, before, names = plan["item"], plan["info"], plan["before"], plan["names"]
        report = {"item": info["id"], "imported": False, "comp_count_before": before, "comp_count_after": before,
                  "converted_tools": [], "warning": None}
        replacing = mode == "replace" and before == 1
        if replacing:
            backup = work / ("backup_%s_%s.comp" % (self._fusion_identifier(info["id"], "item", "clip")[:40],
                                                    uuid.uuid4().hex[:8]))
            if not (call(item, "ExportFusionComp", False, str(backup), 1) and backup.exists()):
                report["warning"] = ("Could not back up %s's existing comp first, so it was left unchanged and nothing "
                                     "was imported." % info["id"])
                return report
            report.update(backup_file=str(backup), replaced_nodes=plan["user_nodes"],
                          replaced_bridge_stages=plan["bridge_stages"])
        imported = call(item, "ImportFusionComp", None, str(comp_file))
        after = int(call(item, "GetFusionCompCount", 0) or 0)
        report["comp_count_after"] = after
        if not imported or after != before + 1:
            if after == before:
                report["warning"] = "Resolve did not import the comp onto %s; the clip is unchanged." % info["id"]
            else:
                report["imported"] = None
                report["warning"] = ("ImportFusionComp returned %s but the comp count went from %d to %d; check %s on "
                                     "the Fusion page." % (bool(imported), before, after, info["id"]))
            return report
        added = [name for name in self._comp_names(item) if name not in names]
        comp = imported if not isinstance(imported, bool) else call(item, "GetFusionCompByIndex", None, after)
        report.update(imported=True, comp_name=added[0] if len(added) == 1 else None)
        warnings = []
        if replacing:
            old, new = names[0], report["comp_name"]
            removed = False
            if new:
                call(item, "LoadFusionCompByName", None, new)  # Resolve will not delete the comp that is loaded.
                removed = bool(call(item, "DeleteFusionCompByName", False, old)) and int(
                    call(item, "GetFusionCompCount", 0) or 0) == 1
            if removed:
                if call(item, "RenameFusionCompByName", False, new, old):
                    report["comp_name"] = old
                report.update(replaced=True, comp_count_after=1)
            else:
                # Never leave a silent second comp: take the copy off again and keep the original.
                undone = False
                if new:
                    call(item, "LoadFusionCompByName", None, old)
                    undone = bool(call(item, "DeleteFusionCompByName", False, new))
                count = int(call(item, "GetFusionCompCount", 0) or 0)
                report.update(comp_count_after=count, replaced=False)
                if undone and count == before:
                    report.update(imported=False, comp_name=old)
                    report["warning"] = ("Resolve would not delete %s's original comp %s (probably because it is loaded "
                                         "on the Fusion page), so the bridge removed the imported copy again and the "
                                         "clip keeps its original comp. Open another clip on the Fusion page and retry."
                                         % (info["id"], old))
                    return report
                warnings.append("Resolve kept both comps on %s (%d now): the original %s and the copy %s; the bridge "
                                "could not delete either. Remove the one you do not want on the Fusion page (the "
                                "original is also saved at %s)." % (info["id"], count, old, new or "(unnamed)",
                                                                    report["backup_file"]))
        elif before:
            warnings.append("%s now has %d Fusion comps. Resolve shows one comp per clip at a time; whether the copy "
                            "became the active one is not verified, so pick it on the Fusion page." % (info["id"], after))
        problems = fusion_conversions(declared, comp) if comp is not None else []
        if problems:
            report["converted_tools"] = problems
            warnings.append(_conversion_warning(problems, "Fusion comp copy onto %s" % info["id"]))
        present = {_fusion_name(tool) for tool in _fusion_tools(comp)} if comp is not None else set()
        converted = {problem["tool"] for problem in problems}
        missing = [t["name"] for t in declared if t["name"] not in present and t["name"] not in converted
                   and not any(re.fullmatch(re.escape(t["name"]) + r"_\d+", name) for name in present)]
        report["missing_tools"] = missing[:50]
        if missing:
            warnings.append("%s's copy lacks %s, which the exported comp declares." % (info["id"], ", ".join(missing[:20])))
        report["verified"] = (None if not present or not declared or report["imported"] is not True
                              else not missing and not problems)
        report["warning"] = " ".join(warnings) or None
        return report

    # ------------------------------------------------------------- context

    def _lua_timeline_snapshot(self, limit):
        """One-round-trip layout from a Lua worker that advertises it; None means use per-call reads."""
        connection = getattr(self._provider(), "_connection", None)
        if connection is None or not callable(getattr(connection, "request", None)):
            return None
        health = connection.request("health")
        if not isinstance(health, dict) or not health.get("timeline_snapshot"):
            return None  # Older worker: asking would fail the whole operation, so don't.
        reply = connection.request("timeline_snapshot", {"max_items": limit})
        if not isinstance(reply, dict) or not isinstance(reply.get("items"), list):
            return None
        return [dict(item) for item in reply["items"] if isinstance(item, dict)], bool(reply.get("capped"))

    def _op_timeline_fingerprint(self, params):
        """Lean read-only structure of the current timeline (and the project's timeline list) for change
        tracking: positions, source offsets and markers, with no per-clip media lookups."""
        project = self._project()
        timeline = self._timeline()
        limit = max(1, min(int(params.get("max_items", 3000) or 3000), 5000))
        timelines = []
        for index in range(1, int(call(project, "GetTimelineCount", 0) or 0) + 1):
            other = call(project, "GetTimelineByIndex", None, index)
            if other is not None:
                timelines.append({"id": call(other, "GetUniqueId", None), "name": call(other, "GetName", "")})
        items, truncated = [], False
        batched = self._lua_timeline_snapshot(limit)
        transitions = []
        if batched is not None:
            items, truncated = batched
            by_track = {}
            for entry in items:
                by_track.setdefault(entry.get("track"), []).append(entry)
            drop = set()
            for track, entries in by_track.items():
                spans = []
                for entry in entries:
                    try:
                        spans.append((float(entry.get("start")), float(entry.get("end")), entry.get("name")))
                    except (TypeError, ValueError):
                        spans.append((None, None, entry.get("name")))
                for i in self._transition_flags(spans):
                    drop.add(id(entries[i]))
                    transitions.append({"track": track, "start_frame": int(spans[i][0]), "end_frame": int(spans[i][1]),
                                        "frames": int(spans[i][1] - spans[i][0]), "name": entries[i].get("name")})
            items = [entry for entry in items if id(entry) not in drop]
        for kind in (() if batched is not None else ("video", "audio", "subtitle")):
            for track in range(1, int(call(timeline, "GetTrackCount", 0, kind) or 0) + 1):
                for item in self._track_clips(timeline, kind, track):
                    if len(items) >= limit:
                        truncated = True
                        break
                    items.append({"id": call(item, "GetUniqueId", None), "name": call(item, "GetName", ""),
                                  "track": "%s%d" % (kind[0].upper(), track), "start": call(item, "GetStart", None),
                                  "end": call(item, "GetEnd", None), "offset": call(item, "GetLeftOffset", None)})
        return {"project": call(project, "GetName", ""), "timeline": self._timeline_identity(timeline),
                "fps": self._project_rate(), "start_frame": call(timeline, "GetStartFrame", 0),
                "drop_frame": str(call(timeline, "GetSetting", "", "timelineDropFrameTimecode")) in ("1", "True", "true"),
                "timelines": timelines, "items": items, "truncated": truncated,
                "transitions": transitions if batched is not None else self._timeline_transitions(timeline),
                "markers": plain(call(timeline, "GetMarkers", {}) or {})}

    def _op_current_context(self, _params):
        """Cheap snapshot for a UI: never raises for a missing project/timeline, never creates comps."""
        instance = self._provider()
        context = {"online": instance is not None, "transport": self.transport,
                   "product": call(instance, "GetProductName", None), "page": call(instance, "GetCurrentPage", None),
                   "project": None, "timeline": None, "timeline_id": None, "fps": None, "resolution": None,
                   "playhead_frame": None, "playhead_timecode": None, "clips_at_playhead": [],
                   "timeline_count": None, "timelines": []}
        manager = call(instance, "GetProjectManager", None) if instance is not None else None
        project = call(manager, "GetCurrentProject", None) if manager is not None else None
        if project is None:
            return context
        context["project"] = call(project, "GetName", None)
        timeline = call(project, "GetCurrentTimeline", None)
        context["timeline_id"] = call(timeline, "GetUniqueId", None) if timeline is not None else None
        context.update(self._timeline_names(project, context["project"], context["timeline_id"]))
        if timeline is None:
            return context
        context["timeline"] = call(timeline, "GetName", None)
        try:  # One GetMarkers call; on any error the key is simply left out.
            from bridge import notes as notes_logic
            markers = call(timeline, "GetMarkers", None)
            if isinstance(markers, dict):
                context["open_notes"] = notes_logic.count_open(markers)
        except Exception:
            pass
        try:
            fps = self._project_rate()
            width, height = self._project_resolution()
            context.update({"fps": fps, "resolution": [width, height]})
            frame = self._playhead_frame(timeline)
        except (OperationError, TypeError, ValueError):
            return context
        context["playhead_frame"] = frame
        context["playhead_timecode"] = call(timeline, "GetCurrentTimecode", None)
        tracks = int(call(timeline, "GetTrackCount", 0, "video") or 0)
        for track in range(tracks, 0, -1):
            items = self._track_clips(timeline, "video", track)
            items = list(items.values()) if isinstance(items, dict) else list(items)
            low, high = 0, len(items) - 1
            while low <= high:  # Items on a track are ordered and never overlap.
                middle = (low + high) // 2
                item = items[middle]
                start, end = call(item, "GetStart", None), call(item, "GetEnd", None)
                if start is None or end is None:
                    break
                if frame < start:
                    high = middle - 1
                elif frame >= end:
                    low = middle + 1
                else:
                    context["clips_at_playhead"].append({
                        "id": str(call(item, "GetUniqueId", None) or "V%d.%d" % (track, middle + 1)),
                        "label": "V%d.%d" % (track, middle + 1), "name": call(item, "GetName", ""),
                        "track": track, "start": start, "end": end, "offset": frame - start,
                        "enabled": call(item, "GetClipEnabled", None),
                        "has_fusion": bool(call(item, "GetFusionCompCount", 0) or 0)})
                    break
        return context

    def _timeline_names(self, project, project_name, current_id, limit=20, ttl=30.0):
        """First ``limit`` timelines' names and ids, cached briefly: current_context is polled every ~2s."""
        count = call(project, "GetTimelineCount", None)
        try:
            count = int(count)
        except (TypeError, ValueError):
            return {"timeline_count": None, "timelines": []}
        key = (project_name, count)
        cached = _TIMELINE_NAME_CACHE.get(key)
        if cached is None or time.monotonic() - cached[0] > ttl:
            listed = []
            for index in range(1, min(count, limit) + 1):
                item = call(project, "GetTimelineByIndex", None, index)
                if item is not None:
                    listed.append({"index": index, "name": call(item, "GetName", None),
                                   "id": call(item, "GetUniqueId", None)})
            _TIMELINE_NAME_CACHE.clear()
            _TIMELINE_NAME_CACHE[key] = cached = (time.monotonic(), listed)
        timelines = [dict(entry, is_current=bool(current_id) and entry["id"] == current_id) for entry in cached[1]]
        return {"timeline_count": count, "timelines": timelines}

    # ---------------------------------------------------------- whole project

    def _op_project_overview(self, params):
        """Read-only picture of the whole project: timelines, media pool tree, settings, render queue."""
        timeline_limit = max(1, min(int(params.get("timeline_limit", 40) or 40), 200))
        clip_limit = max(0, min(int(params.get("clip_limit", 50) or 0), 300))
        folder_limit = max(1, min(int(params.get("folder_limit", 100) or 100), 300))
        instance = self.resolve
        project = self._project()
        current = call(project, "GetCurrentTimeline", None)
        current_id = call(current, "GetUniqueId", None) if current is not None else None
        truncated = {}
        result = {"project": call(project, "GetName", None), "page": call(instance, "GetCurrentPage", None),
                  "product": call(instance, "GetProductName", None), "version": call(instance, "GetVersionString", None),
                  "current_timeline": ({"name": call(current, "GetName", None), "id": current_id}
                                       if current is not None else None)}
        try:
            width, height = self._project_resolution()
            result["settings"] = {"fps": self._project_rate(), "resolution": [width, height]}
        except (OperationError, TypeError, ValueError):
            result["settings"] = {"fps": None, "resolution": None}

        count = int(call(project, "GetTimelineCount", 0) or 0)
        timelines = []
        for index in range(1, min(count, timeline_limit) + 1):
            item = call(project, "GetTimelineByIndex", None, index)
            if item is None:
                continue
            start, end = call(item, "GetStartFrame", None), call(item, "GetEndFrame", None)
            uid = call(item, "GetUniqueId", None)
            timelines.append({
                "index": index, "name": call(item, "GetName", None), "id": uid, "is_current": bool(uid) and uid == current_id,
                "start_frame": start, "end_frame": end,
                "duration_frames": (end - start) if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None,
                "start_timecode": call(item, "GetStartTimecode", None),
                "fps": call(item, "GetSetting", None, "timelineFrameRate"),
                "tracks": {kind: call(item, "GetTrackCount", None, kind) for kind in ("video", "audio", "subtitle")},
            })
        if count > timeline_limit:
            truncated["timelines"] = "Showing %d of %d timelines; raise timeline_limit for more." % (timeline_limit, count)
        result["timeline_count"] = count
        result["timelines"] = timelines

        pool = call(project, "GetMediaPool", None)
        root = call(pool, "GetRootFolder", None) if pool is not None else None
        folders = []
        clips = []
        if root is not None:
            pending = [(root, "")]
            while pending:
                if len(folders) >= folder_limit:
                    truncated["folders"] = "Stopped after %d bins; raise folder_limit for more." % folder_limit
                    break
                folder, prefix = pending.pop(0)
                name = call(folder, "GetName", None) or "Master"
                path = (prefix + "/" + name).strip("/")
                listed = call(folder, "GetClipList", None) or []
                listed = list(listed.values()) if isinstance(listed, dict) else list(listed)
                subfolders = call(folder, "GetSubFolderList", None) or []
                subfolders = list(subfolders.values()) if isinstance(subfolders, dict) else list(subfolders)
                folders.append({"path": path, "clip_count": len(listed), "subfolder_count": len(subfolders)})
                for clip in listed:
                    if len(clips) >= clip_limit:
                        truncated["clips"] = "Listed the first %d clips; use list_media for every clip." % clip_limit
                        break
                    props = call(clip, "GetClipProperty", None) or {}
                    props = props if isinstance(props, dict) else {}
                    clips.append({"id": call(clip, "GetUniqueId", None),
                                  "name": call(clip, "GetName", None) or props.get("Clip Name"), "bin": path,
                                  "type": props.get("Type"), "duration": props.get("Duration"),
                                  "resolution": props.get("Resolution"), "file_path": props.get("File Path")})
                pending.extend((child, path) for child in subfolders)
        result["media_pool"] = {"bins": folders, "clips": clips,
                                "clip_total": sum(folder["clip_count"] for folder in folders)}
        jobs = call(project, "GetRenderJobList", None)
        jobs = list(jobs.values()) if isinstance(jobs, dict) else jobs
        result["render_jobs"] = len(jobs) if isinstance(jobs, (list, tuple)) else None
        result["truncated"] = truncated or None
        return result

    def _api_root(self, name):
        if name == "resolve":
            return self.resolve
        if name == "project_manager":
            manager = self.resolve.GetProjectManager()
            if manager is None:
                raise OperationError("Resolve did not return a Project Manager.")
            return manager
        if name == "media_storage":
            return self.resolve.GetMediaStorage()
        if name == "fusion":
            return self.resolve.Fusion()
        project = self._project()
        if name == "project":
            return project
        if name == "media_pool":
            return project.GetMediaPool()
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            raise OperationError("No timeline is open; open one with open_timeline or use project.GetTimelineByIndex.")
        return timeline

    @staticmethod
    def _api_is_object(value):
        return value is not None and not isinstance(value, (bool, int, float, str, dict, list, tuple))

    def _api_summary(self, value, depth=0, cap=None, compacted=None):
        """JSON-safe result; Resolve objects become {object, name, id}. With a cap, a list (or Lua array) longer
        than cap becomes {count, items: the first cap as {name, id} or a short value, truncated: true}."""
        if self._api_is_object(value):
            text = str(value)
            kind = text.split(" ", 1)[0].split("(", 1)[0] if text and not text.startswith("<") else "Object"
            summary = {"object": kind or "Object"}
            name = call(value, "GetName", None)
            if isinstance(name, str):
                summary["name"] = name
            uid = call(value, "GetUniqueId", None)
            if isinstance(uid, str):
                summary["id"] = uid
            return summary
        if cap is not None:
            sequence = self._api_sequence(value)
            if sequence is not None and len(sequence) > cap:
                if compacted is not None:
                    compacted.append(len(sequence))
                return {"count": len(sequence), "items": [self._api_brief_item(v) for v in sequence[:cap]],
                        "truncated": True}
        if isinstance(value, dict):
            items = list(value.items())
            out = {str(k): self._api_summary(v, depth + 1, cap, compacted) for k, v in items[:RESOLVE_API_MAX_LIST]}
            if len(items) > RESOLVE_API_MAX_LIST:
                out["__truncated__"] = "%d of %d entries shown" % (RESOLVE_API_MAX_LIST, len(items))
            return out
        if isinstance(value, (list, tuple)):
            out = [self._api_summary(v, depth + 1, cap, compacted) for v in list(value)[:RESOLVE_API_MAX_LIST]]
            if len(value) > RESOLVE_API_MAX_LIST:
                out.append({"__truncated__": "%d of %d items shown" % (RESOLVE_API_MAX_LIST, len(value))})
            return out
        return plain(value)

    @staticmethod
    def _api_sequence(value):
        """A list result as a Python list: a list, a tuple or a Lua array (dict keyed 1..n). None otherwise."""
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, dict):
            keys = {str(k): k for k in value}
            if len(keys) == len(value) and set(keys) == {str(i) for i in range(1, len(value) + 1)}:
                return [value[keys[str(i)]] for i in range(1, len(value) + 1)]
        return None

    def _api_brief_item(self, value):
        """One entry of a compact list: {name, id} for a Resolve object, otherwise the value or a short repr."""
        if self._api_is_object(value):
            summary = self._api_summary(value)
            return {key: summary[key] for key in ("name", "id") if key in summary} or summary
        if value is None or isinstance(value, (bool, int, float)):
            return plain(value)
        text = value if isinstance(value, str) else json.dumps(self._api_summary(value, cap=3), default=str)
        return text if len(text) <= 80 else text[:77] + "..."

    def _api_brief(self, label, value):
        """One line for a step that is not in return_steps, e.g. 'GetItemListInTrack -> list of 47'."""
        sequence = None if self._api_is_object(value) else self._api_sequence(value)
        if self._api_is_object(value):
            text = json.dumps(self._api_brief_item(value), default=str)
        elif sequence is not None:
            text = "list of %d" % len(sequence)
        elif isinstance(value, dict):
            text = "dict of %d keys" % len(value)
        else:
            text = json.dumps(plain(value), default=str)
        text = "%s -> %s" % (label, text)
        return text if len(text) <= 100 else text[:97] + "..."

    @staticmethod
    def _api_error_text(exc):
        return str(exc) if isinstance(exc, OperationError) else "%s: %s" % (type(exc).__name__, exc)

    @staticmethod
    def _api_is_loop_ref(text):
        return text == "$index" or text.startswith("$item")

    @staticmethod
    def _api_target_ok(target, in_loop=False):
        """Syntax check for 'on' / 'get' before anything runs."""
        if not isinstance(target, str):
            return False
        if target in RESOLVE_API_ROOTS:
            return True
        kind, sep, uid = target.partition(":")
        if sep and kind in RESOLVE_API_ID_TARGETS:
            return bool(uid.strip())
        if in_loop and re.fullmatch(r"\$item(?:%s)*" % _RESOLVE_API_PATH, target):
            return True
        return re.fullmatch(r"\$\d+(?:%s)*" % _RESOLVE_API_PATH, target) is not None

    def _api_find_by_id(self, kind, uid):
        """item:/media:/timeline:<GetUniqueId> -> exactly one Resolve object, or a clear error."""
        matches, scanned = [], 0
        if kind == "timeline":
            project = self._project()
            for index in range(1, int(call(project, "GetTimelineCount", 0) or 0) + 1):
                timeline = call(project, "GetTimelineByIndex", None, index)
                scanned += 1
                if timeline is not None and str(call(timeline, "GetUniqueId", "")) == uid:
                    matches.append(timeline)
            where = "the project's %d timeline(s); call list_timelines for ids" % scanned
        elif kind == "item":
            timeline = self._api_root("current_timeline")
            for track_type in ("video", "audio", "subtitle"):
                for track in range(1, int(call(timeline, "GetTrackCount", 0, track_type) or 0) + 1):
                    listed = call(timeline, "GetItemListInTrack", None, track_type, track)
                    for item in self._api_sequence(listed) or []:
                        scanned += 1
                        if str(call(item, "GetUniqueId", "")) == uid:
                            matches.append(item)
            where = ("the %d item(s) on the current timeline; call timeline_overview for ids, or open the timeline "
                     "that holds the clip first" % scanned)
        else:
            pending = [call(self._media_pool(), "GetRootFolder", None)]
            while pending:
                folder = pending.pop()
                if folder is None:
                    continue
                for clip in self._api_sequence(call(folder, "GetClipList", None)) or []:
                    scanned += 1
                    if str(call(clip, "GetUniqueId", "")) == uid:
                        matches.append(clip)
                pending.extend(self._api_sequence(call(folder, "GetSubFolderList", None)) or [])
            where = "the %d clip(s) in every media pool bin; call list_media for ids" % scanned
        if not matches:
            raise OperationError("%s:%s was not found among %s." % (kind, uid, where))
        if len(matches) > 1:
            raise OperationError("%s:%s is ambiguous: %d objects report that unique id." % (kind, uid, len(matches)))
        return matches[0]

    def _api_target(self, target, results, each=None):
        if target in RESOLVE_API_ROOTS:
            return self._api_root(target)
        kind, sep, uid = target.partition(":")
        if sep and kind in RESOLVE_API_ID_TARGETS:
            return self._api_find_by_id(kind, uid.strip())
        if each is not None and self._api_is_loop_ref(target):
            return self._api_item_ref(target, each)
        return self._api_ref(target, results)

    @staticmethod
    def _api_item_ref(text, each):
        """Inside for_each: "$index" is the element's index, "$item" (then [i] / .key) the element itself."""
        item, index = each
        if text == "$index":
            return index
        match = re.fullmatch(r"\$item((?:%s)*)" % _RESOLVE_API_PATH, text)
        if match is None:
            raise OperationError("Bad reference %r; inside for_each use $item, $item[i], $item.key or $index." % text)
        return ResolveOperations._api_walk(text, item, match.group(1))

    @staticmethod
    def _api_ref(text, results):
        """Resolve "$N", "$N[i]", "$N.key" (chainable) against earlier step results."""
        match = re.fullmatch(r"\$(\d+)((?:%s)*)" % _RESOLVE_API_PATH, text)
        if match is None:
            raise OperationError("Bad reference %r; use $N, $N[i] or $N.key with N an earlier step number." % text)
        index = int(match.group(1))
        if index >= len(results):
            raise OperationError("Reference %s points at step %d, which has not run yet." % (text, index))
        return ResolveOperations._api_walk(text, results[index], match.group(2))

    @staticmethod
    def _api_walk(text, value, path):
        """Apply a chain of [i] / .key parts (from a $N or $item reference) to value."""
        for part in re.findall(_RESOLVE_API_PATH, path):
            if part.startswith("["):
                key = int(part[1:-1])
                if isinstance(value, dict):
                    # A Lua array arrives keyed 1..n: [i] means its (i+1)th entry. Any other dict is looked up
                    # by its own key. (Trying both made [0] and [1] both return the first entry.)
                    one_based = bool(value) and {str(k) for k in value} == {str(i) for i in range(1, len(value) + 1)}
                    wanted = (key + 1, str(key + 1)) if one_based else (key, str(key))
                    found = [value[k] for k in wanted if k in value]
                    if not found:
                        raise OperationError("%s: index %d is not in that result." % (text, key))
                    value = found[0]
                elif isinstance(value, (list, tuple)) and -len(value) <= key < len(value):
                    value = value[key]
                else:
                    raise OperationError("%s: index %d is out of range." % (text, key))
            else:
                key = part[1:]
                if not isinstance(value, dict) or key not in value:
                    raise OperationError("%s: no key %r in that result." % (text, key))
                value = value[key]
        return value

    def _api_args(self, value, results, each=None):
        if isinstance(value, str):
            if value.startswith("$$"):
                return value[1:]
            if value.startswith("$") and len(value) > 1 and value[1].isdigit():
                return self._api_ref(value, results)
            if each is not None and self._api_is_loop_ref(value):
                return self._api_item_ref(value, each)
            return value
        if isinstance(value, list):
            return [self._api_args(item, results, each) for item in value]
        if isinstance(value, dict):
            return {key: self._api_args(item, results, each) for key, item in value.items()}
        return value

    def _api_check_step(self, number, step, allow_destructive):
        """Validate one step before anything runs."""
        def bad(message):
            return OperationError("Step %d: %s. Nothing ran." % (number, message))
        if not isinstance(step, dict):
            raise bad('each step must be an object such as {"on": "project", "call": "GetName"}')
        loop = "for_each" in step
        kinds = [key for key in ("call", "getattr", "get") if key in step]
        if len(kinds) != 1 or (loop and kinds == ["get"]):
            raise bad("needs a method name in 'call'" + (" (or 'getattr')" if loop else
                                                         ", an attribute in 'getattr', or a target in 'get'"))
        kind = kinds[0]
        if kind == "get":
            target = step["get"]
        else:
            name = step[kind]
            if not isinstance(name, str) or not name or name.startswith("_") or not name.replace("_", "").isalnum():
                raise bad("%r is not an API %s name" % (name, "method" if kind == "call" else "attribute"))
            if kind == "call" and name in RESOLVE_API_DESTRUCTIVE and not allow_destructive:
                raise OperationError(
                    "Step %d calls %s, which deletes data with no undo through the API. Ask the user, then retry "
                    "with allow_destructive=true. Nothing ran." % (number, name))
            target = step.get("on", "$item" if loop else "project")
        if not self._api_target_ok(target, loop):
            raise bad("'%s' must be one of %s, item:/media:/timeline:<unique id>, or a $N reference%s"
                      % ("get" if kind == "get" else "on", ", ".join(RESOLVE_API_ROOTS), " or $item" if loop else ""))
        if not isinstance(step.get("args", []), list):
            raise bad("args must be a list")
        if loop:
            source = step["for_each"]
            match = (re.fullmatch(r"\$(\d+)(?:%s)*(?:\[\*\])?" % _RESOLVE_API_PATH, source)
                     if isinstance(source, str) else None)
            if match is None or int(match.group(1)) >= number:
                raise bad("for_each must be \"$N[*]\" (or \"$N.key[*]\") with N an earlier step")
            bounds = step.get("range")
            if bounds is not None and not (isinstance(bounds, list) and 1 <= len(bounds) <= 2 and all(
                    b is None or (isinstance(b, int) and not isinstance(b, bool)) for b in bounds)):
                raise bad("range must be [start] or [start, stop] (stop exclusive, like a Python slice)")

    def _api_call(self, step, raw, each=None, obj=None):
        """Run one call / getattr / get step, or one for_each element, and return Resolve's raw value."""
        target = step["get"] if "get" in step else step.get("on", "$item" if each is not None else "project")
        if obj is None:
            obj = self._api_target(target, raw, each)
        if not self._api_is_object(obj):
            raise OperationError("%s is %s, not a Resolve object." % (target, type(obj).__name__))
        if "get" in step:
            return obj
        if "getattr" in step:
            name = step["getattr"]
            try:
                value = getattr(obj, name)
            except AttributeError:
                raise OperationError("%s has no attribute %s in this Resolve build." % (target, name)) from None
            if callable(value):
                raise OperationError('%s.%s is a method; use {"call": "%s"} instead of getattr.' % (target, name, name))
            if value is not None and not isinstance(value, (bool, int, float, str)):
                raise OperationError("%s.%s is a %s; getattr returns only strings, numbers, booleans or null."
                                     % (target, name, type(value).__name__))
            return value
        method = step["call"]
        function = getattr(obj, method, None)
        if not callable(function):
            raise OperationError("%s has no method %s in this Resolve build." % (target, method))
        return function(*self._api_args(step.get("args", []), raw, each))

    def _api_for_each(self, step, raw, calls_made):
        """Run step's call on each selected element of an earlier list. Returns (raw values, stats)."""
        source = step["for_each"]
        listed = self._api_sequence(self._api_ref(source[:-3] if source.endswith("[*]") else source, raw))
        if listed is None:
            raise OperationError("%s is not a list result, so for_each has nothing to walk." % source)
        bounds = step.get("range") or [None]
        indices = list(range(len(listed)))[bounds[0]:bounds[1] if len(bounds) > 1 else None]
        if calls_made + len(indices) > RESOLVE_API_MAX_CALLS:
            raise OperationError(
                "for_each would make %d calls; with the %d already made that passes the %d-call limit per request. "
                "None of this step's calls ran; split it with range." % (len(indices), calls_made, RESOLVE_API_MAX_CALLS))
        target = step.get("on", "$item")
        fixed = None if self._api_is_loop_ref(target) else self._api_target(target, raw)
        values, failed = [], []
        for position, index in enumerate(indices):
            try:
                value = self._api_call(step, raw, (listed[index], index), fixed)
            except Exception as exc:  # Wire failures (BaseException) propagate: the outcome is unknown.
                error = self._api_error_text(exc)
                if not step.get("continue_on_error"):
                    ran = ("indices %d-%d ran first and were not undone" % (indices[0], indices[position - 1])
                           if position else "no earlier index ran")
                    raise OperationError("for_each stopped at index %d (%d of %d selected): %s; %s"
                                         % (index, position + 1, len(indices), error, ran))
                failed.append({"index": index, "error": error[:300]})
                values.append(None)
                continue
            values.append(value)
        failed_at = {entry["index"] for entry in failed}
        stats = {"count": len(indices), "ok_count": len(indices) - len(failed),
                 "first_index": indices[0] if indices else None, "last_index": indices[-1] if indices else None,
                 "failed": failed[:20]}
        if len(failed) > 20:
            stats["failed_count"] = len(failed)
        quiet_failures = [i for i, v in zip(indices, values) if i not in failed_at and (v is False or v is None)]
        if quiet_failures:
            stats["returned_false_or_none"] = quiet_failures[:50]
        return values, stats

    def _op_resolve_api(self, params):
        """Run a short chain of Resolve scripting API calls anywhere in the project."""
        steps = params.get("steps")
        if not isinstance(steps, list) or not steps:
            raise OperationError("steps must be a non-empty list of {on, call, args}.")
        if len(steps) > RESOLVE_API_MAX_STEPS:
            raise OperationError("At most %d steps per call (a for_each step counts as one); split the work."
                                 % RESOLVE_API_MAX_STEPS)
        allow_destructive = bool(params.get("allow_destructive"))
        quiet = bool(params.get("quiet"))
        cap = None if params.get("full_lists") else RESOLVE_API_LIST_CAP
        wanted = params.get("return_steps")
        if wanted is not None:
            if not isinstance(wanted, list) or not all(
                    isinstance(n, int) and not isinstance(n, bool) and 0 <= n < len(steps) for n in wanted):
                raise OperationError("return_steps must list step numbers from 0 to %d. Nothing ran."
                                     % (len(steps) - 1))
            wanted = set(wanted)
        for number, step in enumerate(steps):  # Validate everything before the first call runs.
            self._api_check_step(number, step, allow_destructive)
        raw, entries, compacted, calls, last = [], [], [], 0, None
        for number, step in enumerate(steps):
            head = {"step": number}
            head.update((key, step[key]) for key in ("for_each", "get") if key in step)
            if "get" not in step:
                head["on"] = step.get("on", "$item" if "for_each" in step else "project")
            head.update((key, step[key]) for key in ("call", "getattr") if key in step)
            name = step.get("call") or step.get("getattr")
            label = ("get %s" % step["get"]) if "get" in step else "%s.%s" % (head["on"], name)
            if "for_each" in step:
                label = "for_each %s: %s" % (step["for_each"], label)
            stats = None
            try:
                if "for_each" in step:
                    value, stats = self._api_for_each(step, raw, calls)
                    calls += len(value)
                else:
                    if calls + 1 > RESOLVE_API_MAX_CALLS:
                        raise OperationError("this request already made %d calls, the per-request limit; split the "
                                             "work" % calls)
                    calls += 1
                    value = self._api_call(step, raw)
            except Exception as exc:  # Wire failures (BaseException) propagate: the outcome is unknown.
                done = ("Steps 0-%d already ran and were not undone: %s"
                        % (number - 1, json.dumps(entries, default=str)[:4000])) if entries else "Nothing ran."
                raise OperationError("failed_step %d (%s): %s. %s"
                                     % (number, label, self._api_error_text(exc).rstrip("."), done))
            raw.append(value)
            full = number in wanted if wanted is not None else not quiet
            if full or (quiet and number == len(steps) - 1):
                if stats is None:
                    result = self._api_summary(value, cap=cap, compacted=compacted)
                else:
                    uniform = bool(value) and len({repr(v) for v in value}) == 1 and (
                        value[0] is None or isinstance(value[0], (bool, int, float, str)))
                    result = dict(stats, results={"all": plain(value[0])} if uniform else
                                  self._api_summary(value, cap=cap, compacted=compacted))
                if number == len(steps) - 1:
                    last = result
            if full:
                entries.append(dict(head, result=result))
            elif stats is None:
                entries.append({"step": number, "ok": True, "summary": self._api_brief(name or label, value)})
            else:
                entries.append({"step": number, "ok": True, "summary": "%s -> %d of %d ok%s" % (
                    label, stats["ok_count"], stats["count"],
                    ", %d returned False/None" % len(stats["returned_false_or_none"])
                    if stats.get("returned_false_or_none") else "")})
        output = {"ok": True, "steps_run": len(steps)}
        if quiet:
            output["result"] = last
            if wanted:
                output["steps"] = [entry for entry in entries if "result" in entry]
        else:
            output["steps"] = entries
        if compacted:
            output["lists"] = ("Lists over %d items show only the first %d, as {name, id} or a short value. Reach the "
                               "rest with $N[i] or for_each, or pass full_lists=true." % (cap, cap))
        output["note"] = "Resolve's API often returns False or None instead of raising; check each result."
        return output

    def _op_delete_clips(self, params):
        ids = params.get("item_ids") or []
        if not ids:
            raise OperationError("item_ids is required. Call timeline_overview for valid ids.")
        targets = self._find_timeline_items(ids)
        ripple = bool(params.get("ripple", False))
        deleted = self._delete_timeline_items(self._timeline(), targets, ripple)
        return {"deleted": deleted, "ripple": ripple}

    def _op_save_project(self, _params):
        if not self.resolve.GetProjectManager().SaveProject():
            raise OperationError("Resolve could not save the current project.")
        return {"saved": True, "project": self._project().GetName()}

    def _op_list_render_presets(self, _params):
        project = self._project()
        return {
            "presets": plain(call(project, "GetRenderPresetList", []) or []),
            "formats": plain(call(project, "GetRenderFormats", {}) or {}),
        }

    def _op_render_current_timeline(self, params):
        project = self._project()
        raw_output_dir = str(params.get("output_dir", "")).strip()
        if not raw_output_dir:
            raise OperationError("output_dir is required.")
        output_dir = _absolute(raw_output_dir)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        preset = str(params.get("preset", "")).strip()
        if preset and not project.LoadRenderPreset(preset):
            raise OperationError(
                "Render preset '%s' was not found. Call list_render_presets for valid names."
                % preset
            )
        settings = {"TargetDir": output_dir}
        if params.get("name"):
            settings["CustomName"] = str(params["name"])
        if not project.SetRenderSettings(settings):
            raise OperationError("Resolve rejected the render settings.")
        job_id = project.AddRenderJob()
        if not job_id:
            raise OperationError("Resolve could not add a render job.")
        started = bool(project.StartRendering(job_id)) if params.get("start") else False
        return {
            "job_id": job_id,
            "started": started,
            "output_dir": output_dir,
            "preset": preset or None,
        }

    def _op_render_status(self, params):
        """Progress of queued render jobs, from Resolve's ordinary render API (works in Free)."""
        project = self._project()
        wanted = str(params.get("job_id") or "").strip()
        jobs = call(project, "GetRenderJobList", None)
        if isinstance(jobs, dict):
            # Lua may serialize an empty or keyed table as a mapping.
            jobs = list(jobs.values())
        if not isinstance(jobs, (list, tuple)) or any(not isinstance(job, dict) or not job.get("JobId") for job in jobs):
            raise OperationError("Resolve did not return a usable render queue. Check the Deliver page for queued jobs.")
        listed = {str(job["JobId"]): job for job in jobs}
        if wanted and wanted not in listed:
            raise OperationError("Render job '%s' is not in the queue. Call render_status without job_id to list jobs." % wanted)
        report = []
        for job_id in ([wanted] if wanted else list(listed)):
            job = listed[job_id]
            status = call(project, "GetRenderJobStatus", None, job_id)
            status = status if isinstance(status, dict) else {}
            entry = {
                "job_id": job_id,
                "status": status.get("JobStatus"),
                "percent": status.get("CompletionPercentage"),
                "time_remaining_ms": status.get("EstimatedTimeRemainingInMs"),
                "time_taken_ms": status.get("TimeTakenToRenderInMs"),
                "timeline": job.get("TimelineName"),
                "output": str(Path(job["TargetDir"]) / job["OutputFilename"]) if job.get("TargetDir") and job.get("OutputFilename") else None,
            }
            if status.get("Error"):
                entry["error"] = status["Error"]
            if not status:
                entry["note"] = "Resolve returned no status for this job."
            report.append(entry)
        rendering = call(project, "IsRenderingInProgress", None)
        return {"rendering": rendering if isinstance(rendering, bool) else None, "jobs": report,
                "note": None if report else "The render queue is empty."}

    # ------------------------------------------------------------ render check

    QC_PRESET_PREFIX = "ResolveAIBridge-QC-"
    QC_APP_STORE_IDS = ("com.blackmagic-design.DaVinciResolveLite", "com.blackmagic-design.DaVinciResolveApp",
                        "com.blackmagic-design.DaVinciResolveStudio")

    def _qc_check_dirs(self):
        """Folders a render_check preview may be written to: <runtime home>/renders/check."""
        user = Path.home()
        homes = [self.runtime_home, os.environ.get("RESOLVE_AI_BRIDGE_HOME")]
        if "/Library/Containers/" in str(user):
            homes.append(user / ".resolve-ai-bridge")
        else:
            homes += [user / "Library" / "Containers" / bundle / "Data" / ".resolve-ai-bridge"
                      for bundle in self.QC_APP_STORE_IDS]
        homes.append(user / ".resolve-ai-bridge")
        folders = []
        for home in homes:
            if home:
                folder = Path(_absolute(Path(str(home)).expanduser() / "renders" / "check"))
                if folder not in folders:
                    folders.append(folder)
        return folders

    def _in_qc_dir(self, path):
        try:
            target = Path(_absolute(path))
        except (OSError, RuntimeError, ValueError):
            return False
        return any(target == folder or folder in target.parents for folder in self._qc_check_dirs())

    @staticmethod
    def _render_jobs(project):
        jobs = call(project, "GetRenderJobList", None)
        if isinstance(jobs, dict):  # Lua may serialize an empty or keyed table as a mapping.
            jobs = list(jobs.values())
        if not isinstance(jobs, (list, tuple)):
            raise OperationError("Resolve did not return a usable render queue. Check the Deliver page for queued jobs.")
        return [job for job in jobs if isinstance(job, dict) and job.get("JobId")]

    @staticmethod
    def _qc_format(project):
        """MP4/H.264, else QuickTime/H.264, from the formats and codecs this Resolve reports."""
        formats = call(project, "GetRenderFormats", {}) or {}
        extensions = {str(value).lower(): str(value) for value in (formats.values() if isinstance(formats, dict) else formats)}
        for wanted in ("mp4", "mov"):
            if wanted not in extensions:
                continue
            codecs = call(project, "GetRenderCodecs", {}, extensions[wanted]) or {}
            if not isinstance(codecs, dict):
                continue
            if "H264" in [str(value) for value in codecs.values()]:
                return extensions[wanted], "H264"
            for description, value in sorted(codecs.items()):
                if "264" in str(description) or "264" in str(value):
                    return extensions[wanted], str(value)
        return None

    def _qc_audio_edits(self, timeline, start, end, limit=2000):
        """Absolute frames inside (start, end) where an audio clip starts or ends on an enabled track."""
        points = set()
        for index in range(1, int(call(timeline, "GetTrackCount", 0, "audio") or 0) + 1):
            if call(timeline, "GetIsTrackEnabled", True, "audio", index) is False:
                continue
            for item in self._track_clips(timeline, "audio", index):
                for edge in (call(item, "GetStart", None), call(item, "GetEnd", None)):
                    try:
                        frame = int(round(float(edge)))
                    except (TypeError, ValueError):
                        continue
                    if start < frame < end:
                        points.add(frame)
        return sorted(points)[:limit]

    def _qc_restore(self, project, timeline, preset, before, delete=True):
        """Load the temporary preset back (and delete it), then verify what the API can read back.

        Live on Resolve 21.2 Free, rendering the job put its own format/codec and In/Out range back on the
        Deliver page and timeline, so render_check restores once after queuing and again after the render.
        """
        report = {"preset": preset, "preset_loaded": bool(call(project, "LoadRenderPreset", False, preset)),
                  "preset_deleted": None, "preset_gone": None}
        if delete:
            report["preset_deleted"] = bool(call(project, "DeleteRenderPreset", False, preset))
            presets = plain(call(project, "GetRenderPresetList", None))
            if isinstance(presets, dict):
                presets = list(presets.values())
            report["preset_gone"] = isinstance(presets, list) and preset not in presets
        mode = before["render_mode"]
        if mode in (0, 1) and call(project, "GetCurrentRenderMode", None) != mode:
            call(project, "SetCurrentRenderMode", False, mode)
        report["render_mode_restored"] = (call(project, "GetCurrentRenderMode", None) == mode) if mode in (0, 1) else None
        wanted = before["format_codec"]
        known = isinstance(wanted, dict) and wanted.get("format") not in (None, "", "unknown")
        now = plain(call(project, "GetCurrentRenderFormatAndCodec", None))
        if known and now != wanted:
            call(project, "SetCurrentRenderFormatAndCodec", False, wanted["format"], wanted.get("codec") or "")
            now = plain(call(project, "GetCurrentRenderFormatAndCodec", None))
        report.update(format_codec_before=wanted, format_codec_after=now,
                      format_codec_restored=(now == wanted) if known else None)
        if not known:
            report["format_codec_note"] = ("Resolve reported no current render format before the check, "
                                           "so there was no format/codec to set back.")
        marks = before["mark_in_out"]
        now = plain(call(timeline, "GetMarkInOut", None))
        if isinstance(marks, dict) and now != marks:
            call(timeline, "ClearMarkInOut", False, "all")
            video, audio = marks.get("video") or {}, marks.get("audio") or {}
            # Resolve 21.2 Free rejects type "video"/"audio" (it sets the In, then returns False and drops the
            # Out), but accepts "all". Use "all" when both match, which is how marks are set from the UI.
            if video == audio and "in" in video and "out" in video:
                call(timeline, "SetMarkInOut", False, video["in"], video["out"], "all")
            else:
                for kind, mark in (("video", video), ("audio", audio)):
                    if isinstance(mark, dict) and "in" in mark and "out" in mark:
                        call(timeline, "SetMarkInOut", False, mark["in"], mark["out"], kind)
            now = plain(call(timeline, "GetMarkInOut", None))
        report["mark_in_out_restored"] = (now == marks) if isinstance(marks, dict) else None
        checks = ("preset_loaded", "preset_deleted", "preset_gone", "render_mode_restored", "format_codec_restored",
                  "mark_in_out_restored")
        if not delete:
            checks = tuple(name for name in checks if name not in ("preset_deleted", "preset_gone"))
        report["unverified"] = [name for name in checks if report[name] is None]
        report["settings_restored"] = all(report[name] is not False for name in checks) and report["preset_loaded"]
        report["note"] = ("Resolve has no getter for the other Deliver fields (location, file name, audio options); "
                          "they come back only through the temporary preset that was loaded.")
        return report

    def _op_render_preview(self, params):
        """Queue and start a small H.264 render of the timeline (or [start_frame, end_frame)) for render_check.

        The Deliver settings are saved to a temporary preset first, then loaded back and the preset deleted
        once the job is queued. ``restore.settings_restored`` reports whether every readable check passed.
        """
        project = self._project()
        timeline = self._timeline()
        raw_dir = str(params.get("output_dir") or "").strip()
        if not raw_dir:
            raise OperationError("output_dir is required: the bridge runtime's renders/check folder.")
        output_dir = _absolute(raw_dir)
        if not self._in_qc_dir(output_dir):
            raise OperationError("output_dir must be inside the bridge runtime's renders/check folder.")
        if call(project, "IsRenderingInProgress", False) is True:
            raise OperationError("Resolve is already rendering. Wait for it to finish, then run the render check again.")
        fps = self._project_rate()
        tl_start = int(round(float(timeline.GetStartFrame())))
        tl_end = int(round(float(call(timeline, "GetEndFrame", tl_start) or tl_start)))
        try:
            start = int(params["start_frame"]) if params.get("start_frame") is not None else tl_start
            end = int(params["end_frame"]) if params.get("end_frame") is not None else tl_end
            width = int(params.get("width") or 640)
        except (TypeError, ValueError):
            raise OperationError("start_frame and end_frame must be absolute timeline frames; width a pixel count.")
        if not tl_start <= start < end <= tl_end:
            raise OperationError("Range %d-%d is outside the timeline (%d-%d, end frame exclusive)."
                                 % (start, end, tl_start, tl_end))
        timeline_width, timeline_height = self._project_resolution()
        width = max(160, min(width, 1920)) // 2 * 2
        height = max(2, int(round(width * timeline_height / float(timeline_width or 1) / 2.0)) * 2)
        # Snapshot before any other render call: live, the first format/codec query changed what
        # GetCurrentRenderFormatAndCodec reported ('unknown' before, mov/H264 after).
        # The page first: live on Resolve 21.2 Free, GetCurrentRenderMode alone switches Resolve to the Deliver
        # page (and so does rendering); render_preview_cleanup puts this page back.
        page = call(self.resolve, "GetCurrentPage", None)
        before = {"format_codec": plain(call(project, "GetCurrentRenderFormatAndCodec", None)),
                  "render_mode": call(project, "GetCurrentRenderMode", None),
                  "mark_in_out": plain(call(timeline, "GetMarkInOut", None)),
                  "page": page}
        choice = self._qc_format(project)
        if choice is None:
            raise OperationError("This Resolve reports no MP4 or QuickTime H.264 codec, so no preview was rendered.")
        fmt, codec = choice
        start_tc = call(timeline, "GetStartTimecode", None)
        drop = ";" in str(start_tc or "") or str(call(timeline, "GetSetting", "", "timelineDropFrameTimecode")) in ("1", "True", "true")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        name = "qc-preview-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
        preset = self.QC_PRESET_PREFIX + uuid.uuid4().hex[:8]
        if not call(project, "SaveAsNewRenderPreset", False, preset):
            raise OperationError("Resolve would not save the current Deliver settings as a temporary preset, "
                                 "so nothing was changed and no preview was rendered.")
        job_id, failure = None, None
        settings = {"SelectAllFrames": False, "MarkIn": start, "MarkOut": end - 1, "TargetDir": output_dir,
                    "CustomName": name, "ExportVideo": True, "ExportAudio": True,
                    "FormatWidth": width, "FormatHeight": height}
        try:
            if before["render_mode"] != 1 and not call(project, "SetCurrentRenderMode", False, 1):
                raise OperationError("Resolve would not switch the Deliver page to single-clip rendering.")
            if not call(project, "SetCurrentRenderFormatAndCodec", False, fmt, codec):
                raise OperationError("Resolve rejected the %s/%s preview format." % (fmt, codec))
            if not call(project, "SetRenderSettings", False, settings):
                raise OperationError("Resolve rejected the preview render settings.")
            job_id = call(project, "AddRenderJob", None)
            if not job_id:
                raise OperationError("Resolve could not add the preview render job.")
        except OperationError as exc:
            failure = exc
        # Keep the preset after a successful queue: rendering re-applies the job's settings, so
        # render_preview_cleanup loads it again once the render is over, then deletes it.
        restore = self._qc_restore(project, timeline, preset, before, delete=failure is not None)
        if failure is not None:
            raise OperationError("%s Nothing was rendered. Deliver settings restored: %s." % (failure, restore["settings_restored"]))
        job = next((entry for entry in self._render_jobs(project) if str(entry.get("JobId")) == str(job_id)), {})
        output = str(Path(job["TargetDir"]) / job["OutputFilename"]) if job.get("TargetDir") and job.get("OutputFilename") else None
        started = bool(call(project, "StartRendering", False, job_id))
        result = {
            "job_id": job_id, "started": started, "output": output, "output_dir": output_dir,
            "format": fmt, "codec": codec, "width": width, "height": height,
            "start_frame": start, "end_frame": end, "end_frame_note": "end_frame is exclusive; Resolve's MarkOut is end_frame - 1.",
            "job_mark_in": job.get("MarkIn"), "job_mark_out": job.get("MarkOut"),
            "timeline": call(timeline, "GetName", None), "timeline_id": call(timeline, "GetUniqueId", None),
            "timeline_start_frame": tl_start, "timeline_end_frame": tl_end, "fps": fps, "drop_frame": drop,
            "start_timecode": start_tc, "audio_edit_points": self._qc_audio_edits(timeline, start, end),
            "restore": restore, "settings_restored_before_render": restore["settings_restored"],
            "restore_state": {"preset": preset, "before": before},
            "restore_note": "Pass restore_state to render_preview_cleanup after the render: it restores the Deliver "
                            "settings and In/Out marks again and deletes the temporary preset.",
        }
        if job and (job.get("MarkIn") not in (None, start) or job.get("MarkOut") not in (None, end - 1)):
            result["range_warning"] = "Resolve queued MarkIn/MarkOut %s/%s instead of %d/%d." % (
                job.get("MarkIn"), job.get("MarkOut"), start, end - 1)
        if not started:
            result["note"] = "Resolve did not start the job. It is still queued; remove it with render_preview_cleanup."
        return result

    def _op_render_preview_cleanup(self, params):
        """Remove a render_check preview job, and its file unless keep_file. Refuses every other job.

        With ``restore`` (render_preview's restore_state) it then loads the temporary preset again, puts back
        the format/codec, render mode and In/Out marks, and deletes the preset.
        """
        project = self._project()
        job_id = str(params.get("job_id") or "").strip()
        if not job_id:
            raise OperationError("job_id is required.")
        state = params.get("restore")
        if state is not None:
            if not isinstance(state, dict) or not isinstance(state.get("before"), dict) \
                    or not str(state.get("preset") or "").startswith(self.QC_PRESET_PREFIX):
                raise OperationError("restore must be render_preview's restore_state; only %s* presets are touched. "
                                     "Nothing was removed." % self.QC_PRESET_PREFIX)
            before = state["before"]
            if not all(key in before for key in ("format_codec", "render_mode", "mark_in_out")):
                raise OperationError("restore_state is incomplete. Nothing was removed.")
        job = next((entry for entry in self._render_jobs(project) if str(entry.get("JobId")) == job_id), None)
        if job is None:
            raise OperationError("Render job '%s' is not in the queue; nothing was removed." % job_id)
        target = job.get("TargetDir")
        if not target or not self._in_qc_dir(target):
            raise OperationError("Refusing to remove render job '%s': it writes to %s, not the render check folder. "
                                 "Only render_check previews can be removed here." % (job_id, target or "an unknown folder"))
        status = call(project, "GetRenderJobStatus", None, job_id)
        stopped = False
        if isinstance(status, dict) and str(status.get("JobStatus") or "").lower() == "rendering" \
                and call(project, "IsRenderingInProgress", False) is True:
            call(project, "StopRendering", None)
            stopped = True
            for _ in range(20):
                if call(project, "IsRenderingInProgress", False) is not True:
                    break
                time.sleep(0.25)
        deleted = bool(call(project, "DeleteRenderJob", False, job_id))
        remaining = any(str(entry.get("JobId")) == job_id for entry in self._render_jobs(project))
        output = Path(target) / job["OutputFilename"] if job.get("OutputFilename") else None
        keep = bool(params.get("keep_file"))
        file_deleted = False
        if output is not None and not keep and self._in_qc_dir(output) and output.is_file():
            output.unlink()
            file_deleted = True
        result = {"job_id": job_id, "removed": deleted and not remaining, "stopped_render": stopped,
                  "output": str(output) if output else None, "file_deleted": file_deleted, "file_kept": keep,
                  "file_exists": bool(output and output.is_file())}
        if state is not None:
            restore = self._qc_restore(project, self._timeline(), str(state["preset"]), state["before"], delete=True)
            result.update(restore=restore, settings_restored=restore["settings_restored"])
            page = state["before"].get("page")
            if isinstance(page, str) and page:
                now = call(self.resolve, "GetCurrentPage", None)
                if now != page:
                    call(self.resolve, "OpenPage", None, page)
                    now = call(self.resolve, "GetCurrentPage", None)
                result["page"] = {"before": page, "now": now, "restored": now == page}
        return result

    def _source_window(self, item, timeline_frame):
        """Normal-speed source mapping; refuse known retiming rather than guessing."""
        fps = self._project_rate()
        media = call(item, "GetMediaPoolItem", None)
        source_fps = call(media, "GetClipProperty", None, "FPS")
        try:
            source_fps = float(source_fps)
            if not math.isfinite(source_fps) or source_fps <= 0:
                source_fps = fps
        except (ValueError, TypeError):
            source_fps = fps
        start = float(call(item, "GetStart", 0) or 0)
        end = float(call(item, "GetEnd", start) or start)
        source_start = call(item, "GetSourceStartFrame", None)
        if source_start is None:
            source_start = call(item, "GetLeftOffset", 0) or 0
        source_end = call(item, "GetSourceEndFrame", None)
        if source_end is not None:
            source_seconds = (float(source_end) - float(source_start) + 1) / source_fps
            if abs(source_seconds - (end - start) / fps) > max(2 / source_fps, 2 / fps):
                raise OperationError("Source extraction cannot map this retimed clip reliably. Use a rendered clip or normal-speed source.")
        position = min(max(float(timeline_frame), start), end)
        return float(source_start) / source_fps + (position - start) / fps, max(0, (end - position) / fps)

    @staticmethod
    def _ffmpeg_path(runtime_home=None):
        """Find a user-installed decoder or the installer's optional private binary."""
        import shutil
        binary = os.environ.get("RESOLVE_AI_BRIDGE_FFMPEG") or shutil.which("ffmpeg")
        if binary:
            return binary
        home = Path(runtime_home or os.environ.get("RESOLVE_AI_BRIDGE_HOME", Path.home() / ".resolve-ai-bridge")).expanduser()
        try:
            path = Path((home / "ffmpeg-path.txt").read_text(encoding="utf-8").strip())
            if not path.is_absolute():
                path = home / path
            return str(path) if path.is_file() and os.access(path, os.X_OK) else None
        except OSError:
            return None

    @staticmethod
    def _image_dimensions(path):
        """Read PNG/JPEG dimensions without optional Python image libraries."""
        import struct
        with Path(path).open("rb") as handle:
            header = handle.read(24)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) == 24:
                return struct.unpack(">II", header[16:24])
            if header[:2] == b"\xff\xd8":
                handle.seek(2)
                while True:
                    byte = handle.read(1)
                    if not byte:
                        break
                    if byte != b"\xff":
                        continue
                    marker = handle.read(1)
                    while marker == b"\xff":
                        marker = handle.read(1)
                    if not marker or marker in (b"\xda", b"\xd9"):
                        break
                    if marker == b"\x01" or 0xD0 <= marker[0] <= 0xD8:
                        continue
                    length = handle.read(2)
                    if len(length) != 2:
                        break
                    length = struct.unpack(">H", length)[0]
                    if marker[0] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        data = handle.read(5)
                        if len(data) == 5:
                            height, width = struct.unpack(">HH", data[1:])
                            return width, height
                        break
                    if length < 2:
                        break
                    handle.seek(length - 2, 1)
        return None, None

    def _video_items_at(self, timeline, frame):
        """[(track_index, item)] for video items covering ``frame``, top track first. Binary search per track."""
        found = []
        for track in range(int(call(timeline, "GetTrackCount", 0, "video") or 0), 0, -1):
            items = self._track_clips(timeline, "video", track)
            items = list(items.values()) if isinstance(items, dict) else list(items)
            low, high = 0, len(items) - 1
            while low <= high:
                middle = (low + high) // 2
                start, end = call(items[middle], "GetStart", None), call(items[middle], "GetEnd", None)
                if start is None or end is None:
                    break
                if frame < start:
                    high = middle - 1
                elif frame >= end:
                    low = middle + 1
                else:
                    found.append((track, items[middle]))
                    break
        return found

    # ------------------------------------------------- visual diff (read-only)

    def _timeline_rate(self, timeline):
        from bridge import visual_diff
        return visual_diff.parse_rate(call(timeline, "GetSetting", None, "timelineFrameRate"), self._project_rate())

    def _timeline_tc(self, timeline, fps):
        """frame -> timecode label for ``timeline``; frames are absolute, drop-frame aware."""
        start_tc = str(call(timeline, "GetStartTimecode", "00:00:00:00") or "00:00:00:00")
        start = int(call(timeline, "GetStartFrame", 0) or 0)
        try:
            base, drop = self._tc_frames(start_tc, fps), ";" in start_tc
        except OperationError:
            base, drop = 0, False

        def label(frame):
            try:
                return self._frames_tc(max(0, base + int(frame) - start), fps, drop)
            except (OperationError, TypeError, ValueError):
                return "frame %s" % frame
        return label

    def _visual_diff_clips(self, timeline):
        """Plain video clip data for bridge.visual_diff, read without opening ``timeline``."""
        clips, media = [], {}
        for track in range(1, int(call(timeline, "GetTrackCount", 0, "video") or 0) + 1):
            items = self._track_clips(timeline, "video", track)
            for item in (list(items.values()) if isinstance(items, dict) else list(items)):
                start, end = call(item, "GetStart", None), call(item, "GetEnd", None)
                if start is None or end is None or float(end) <= float(start):
                    continue
                pool_item = call(item, "GetMediaPoolItem", None)
                media_id = call(pool_item, "GetUniqueId", None) if pool_item is not None else None
                if media_id is not None and media_id not in media:
                    path = str(call(pool_item, "GetClipProperty", "", "File Path") or "")
                    kind = str(call(pool_item, "GetClipProperty", "", "Type") or "").lower()
                    try:
                        src_fps = float(call(pool_item, "GetClipProperty", None, "FPS"))
                        src_fps = src_fps if math.isfinite(src_fps) and src_fps > 0 else None
                    except (TypeError, ValueError):
                        src_fps = None
                    media[media_id] = {"file_path": path, "src_fps": src_fps,
                                       "still": "still" in kind or Path(path).suffix.lower() in IMAGE_SUFFIXES}
                info = media.get(media_id, {})
                clips.append({"track": track, "start": float(start), "end": float(end), "name": call(item, "GetName", ""),
                              "item_id": call(item, "GetUniqueId", None), "enabled": call(item, "GetClipEnabled", None),
                              "key": str(media_id) if media_id is not None else None,
                              "file_path": info.get("file_path"), "src_fps": info.get("src_fps"),
                              # The bridge's own transparent caption overlays are not the picture.
                              "overlay": "/.resolve-ai-bridge/captions/" in str(info.get("file_path") or ""),
                              "still": bool(info.get("still")), "src_start": call(item, "GetSourceStartFrame", None),
                              "src_end": call(item, "GetSourceEndFrame", None), "left_offset": call(item, "GetLeftOffset", 0),
                              "props": plain(call(item, "GetProperty", {}) or {})})
        return clips

    def _op_visual_diff(self, params):
        """Where two timeline versions differ, with source-frame references for a before/after grid.

        Read-only: both timelines are read in place and the current timeline is never switched.
        """
        from bridge import visual_diff
        project = self._project()
        current_before = call(project, "GetCurrentTimeline", None)
        original = self._find_timeline(params.get("original"))
        revised = self._find_timeline(params.get("revised"))
        if self._same_timeline(original, revised):
            raise OperationError("original and revised name the same timeline; pass two different timelines.")
        structural = self._op_compare_timelines({"original": params.get("original"), "preview": params.get("revised")})
        sides = {}
        for role, timeline in (("original", original), ("revised", revised)):
            fps = self._timeline_rate(timeline)
            start = int(call(timeline, "GetStartFrame", 0) or 0)
            end = int(call(timeline, "GetEndFrame", start) or start)
            sides[role] = {"fps": fps, "range": (start, end), "tc": self._timeline_tc(timeline, fps),
                           "clips": self._visual_diff_clips(timeline),
                           "identity": dict(self._timeline_identity(timeline), fps=round(fps, 4),
                                            start_frame=start, end_frame=end)}
        o, r = sides["original"], sides["revised"]
        try:
            max_rows = int(params.get("max_rows", 8) or 8)
            merge_seconds = float(params.get("merge_seconds", 1.0))
        except (TypeError, ValueError):
            raise OperationError("max_rows must be a whole number and merge_seconds a number.")
        found = visual_diff.diff(o["clips"], r["clips"], o["fps"], r["fps"], o["range"], r["range"],
                                 max_rows=max_rows, merge_seconds=merge_seconds,
                                 tc_original=o["tc"], tc_revised=r["tc"])
        structural_changes = []
        for change in structural.get("changes", []):
            if "track" in change:
                before, after = change.get("before"), change.get("after")
                structural_changes.append({"track": change["track"],
                                           "status": "added" if before is None else "removed" if after is None else "changed",
                                           "clips_before": len((before or {}).get("clips", [])),
                                           "clips_after": len((after or {}).get("clips", []))})
            elif change.get("field") == "markers":
                structural_changes.append({"field": "markers", "before": len(change.get("before") or {}),
                                           "after": len(change.get("after") or {})})
            else:
                structural_changes.append({"field": change.get("field"), "before": change.get("before"),
                                           "after": change.get("after")})
        current_after = call(project, "GetCurrentTimeline", None)
        result = {"original": o["identity"], "revised": r["identity"], "frame_space": "absolute_timeline",
                  "structurally_equal": structural.get("structurally_equal"), "structural_changes": structural_changes,
                  "summary": found["summary"], "changes": found["changes"][:60], "rows": found["rows"],
                  "opened_timeline": False,
                  "current_timeline": self._timeline_identity(current_after) if current_after is not None else None,
                  "current_timeline_unchanged": self._same_timeline(current_before, current_after)
                  if current_before is not None else None,
                  "limitations": visual_diff.LIMITATIONS}
        if not found["changes"]:
            result["note"] = ("No picture changes found. " + (
                "The timelines are structurally equal." if structural.get("structurally_equal") else
                "structural_changes lists what differs (audio, markers, settings or clips without media)."))
        return result

    # ------------------------------------------------------- styled titles

    def _layout_snapshot(self, timeline):
        """{item id: [kind, track, start, end]} for every clip, to spot clips an insert moved."""
        snapshot = {}
        for kind in ("video", "audio", "subtitle"):
            for track in range(1, int(call(timeline, "GetTrackCount", 0, kind) or 0) + 1):
                items = self._track_clips(timeline, kind, track)
                for position, item in enumerate(list(items.values()) if isinstance(items, dict) else list(items), 1):
                    key = str(call(item, "GetUniqueId", None) or "%s%d.%d" % (kind[0].upper(), track, position))
                    snapshot[key] = [kind, track, call(item, "GetStart", None), call(item, "GetEnd", None),
                                     call(item, "GetName", "")]
        return snapshot

    @staticmethod
    def _title_text_tool(item):
        """(comp, Text+ tool) inside a Fusion title clip, or (None, None)."""
        count = int(call(item, "GetFusionCompCount", 0) or 0)
        for index in range(1, max(count, 1) + 1):
            comp = call(item, "GetFusionCompByIndex", None, index)
            if comp is None:
                continue
            tools = call(comp, "GetToolList", None, False) or {}
            for tool in (tools.values() if isinstance(tools, dict) else list(tools)):
                if call(tool, "GetAttrs", None, "TOOLS_RegID") == "TextPlus":
                    return comp, tool
        return None, None

    def _apply_text_plus(self, comp, tool, inputs):
        """Set Text+ inputs; returns (finished: True/False/None, errors). Values are read back by the caller."""
        errors = []
        if self.transport == "console":
            for input_id, value in inputs:
                try:
                    tool.SetInput(input_id, {1: value[0], 2: value[1]} if isinstance(value, tuple) else value)
                except Exception as exc:
                    errors.append("%s: %s" % (input_id, exc))
            return True, errors
        edit = FusionEdit(self, comp, "styled_title")
        lines = [edit.bind("aib_title", tool)]
        for input_id, value in inputs:
            if isinstance(value, str):
                lines += edit.static_text("aib_title", input_id, value)
            elif isinstance(value, tuple):
                lines += edit.static_point("aib_title", input_id, value[0], value[1])
            else:
                lines += edit.static("aib_title", input_id, float(value))
        try:
            return edit.lua(lines), errors
        except OperationError as exc:
            errors.append(str(exc))
            return False, errors

    def _op_styled_title(self, params):
        """Insert a Fusion Text+ title at an absolute frame with exact styling, then read the values back.

        dry_run=True only computes the layout, font and target track. Placement uses Resolve's ordinary
        InsertFusionTitleIntoTimeline at the playhead, so the track, start and length are reported as Resolve
        actually made them, together with any existing clip the insert moved.
        """
        from bridge import titles
        timeline = self._timeline()
        text = str(params.get("text") or "")
        if not text.strip():
            raise OperationError("text is required.")
        if params.get("start_frame") is None:
            raise OperationError("start_frame is required: an absolute timeline frame, as timeline_overview reports.")
        try:
            start, duration = int(params["start_frame"]), int(params.get("duration_frames") or 0)
        except (TypeError, ValueError):
            raise OperationError("start_frame and duration_frames must be whole frame numbers.")
        if duration < 1:
            raise OperationError("duration_frames must be 1 or more.")
        fps = self._project_rate()
        timeline_start = int(call(timeline, "GetStartFrame", 0) or 0)
        if start < timeline_start:
            raise OperationError("start_frame %d is before the timeline start (%d). Frames are absolute, like "
                                 "timeline_overview's start values." % (start, timeline_start))
        width, height = self._project_resolution()
        size = params.get("size")
        try:
            placement = titles.layout(width, height, params.get("position") or "lower_third",
                                      params.get("align") or "auto", params.get("safe_area") or "title",
                                      0.08 if size in (None, "") else float(size), text)
            color = titles.parse_color(params.get("color") or "#FFFFFF", "color")
            background = titles.parse_color(params["background"], "background") if params.get("background") else None
        except (titles.TitleError, TypeError, ValueError) as exc:
            raise OperationError(str(exc))
        font = titles.resolve_font(params.get("font") or "", params.get("style") or "")
        inputs = titles.text_plus_inputs(text, font, placement["size"], color, placement, background)
        end = start + duration
        video_tracks = int(call(timeline, "GetTrackCount", 0, "video") or 0)
        track = params.get("track")
        if track in (None, "", "new"):
            target, new_track = video_tracks + 1, True
        else:
            try:
                target, new_track = int(track), False
            except (TypeError, ValueError):
                raise OperationError("track must be a video track number, or omitted for a new track.")
            if target < 1 or target > video_tracks:
                raise OperationError("V%d does not exist; the timeline has %d video track(s). Omit track to add a "
                                     "new one." % (target, video_tracks))
        conflicts = []
        if not new_track:
            for item in self._track_clips(timeline, "video", target):
                a, b = call(item, "GetStart", None), call(item, "GetEnd", None)
                if a is not None and b is not None and a < end and b > start:
                    conflicts.append({"id": call(item, "GetUniqueId", None), "name": call(item, "GetName", ""),
                                      "start": a, "end": b})
        start_tc = str(call(timeline, "GetStartTimecode", "00:00:00:00") or "00:00:00:00")
        timecode = self._frames_tc(self._tc_frames(start_tc, fps) + start - timeline_start, fps, ";" in start_tc)
        plan = {"text": text, "start_frame": start, "end_frame": end, "duration_frames": duration,
                "timecode": timecode, "frame_space": "absolute_timeline", "track": "V%d" % target,
                "new_track": new_track, "layout": placement, "font": font, "color": titles.color_hex(color),
                "background": titles.color_hex(background) if background else None,
                "text_plus_inputs": [{"input": key, "value": list(value) if isinstance(value, tuple) else value}
                                     for key, value in inputs]}
        if params.get("dry_run"):
            plan.update(dry_run=True, inserted=False, conflicts=conflicts,
                        note="Dry run: nothing was changed. Ask the user before inserting; a preview_timeline copy is safest.")
            return plan
        if conflicts:
            raise OperationError("V%d already has clips between frames %d and %d (%s). Choose another track, or "
                                 "omit track to add a new one. Nothing was changed."
                                 % (target, start, end, ", ".join(str(c["name"]) for c in conflicts[:3])))

        before = self._layout_snapshot(timeline)
        original_tc = call(timeline, "GetCurrentTimecode", None)
        if new_track and not call(timeline, "AddTrack", False, "video"):
            raise OperationError("Resolve refused to add a video track. Nothing was changed.")
        item = None
        try:
            if not call(timeline, "SetCurrentTimecode", False, timecode):
                raise OperationError("Resolve refused to move the playhead to %s; no title was inserted.%s"
                                     % (timecode, " An empty V%d was added." % target if new_track else ""))
            item = call(timeline, "InsertFusionTitleIntoTimeline", None, "Text+")
        finally:
            if original_tc:
                call(timeline, "SetCurrentTimecode", False, original_tc)
        if not item:
            raise OperationError("Resolve did not insert a Text+ title at %s.%s" % (
                timecode, " An empty V%d was added and left in place." % target if new_track else ""))

        after = self._layout_snapshot(timeline)
        item_id = call(item, "GetUniqueId", None)
        placed = after.get(str(item_id)) if item_id else None
        actual_start, actual_end = call(item, "GetStart", None), call(item, "GetEnd", None)
        actual_track = placed[1] if placed and placed[0] == "video" else None
        moved = [{"id": key, "name": was[4], "before": was[:4], "after": after[key][:4] if key in after else None}
                 for key, was in before.items() if after.get(key) != was]
        comp, tool = self._title_text_tool(item)
        result = dict(plan, inserted=True, item=str(item_id) if item_id else None, method="InsertFusionTitleIntoTimeline")
        try:
            actual_duration = int(actual_end) - int(actual_start)
        except (TypeError, ValueError):
            actual_duration = call(item, "GetDuration", None)
        result["placement"] = {
            "requested": {"track": "V%d" % target, "start_frame": start, "duration_frames": duration},
            "actual": {"track": "V%d" % actual_track if actual_track else None, "start_frame": actual_start,
                       "end_frame": actual_end, "duration_frames": actual_duration},
            "track_matched": actual_track == target if actual_track else None,
            "start_matched": actual_start == start if actual_start is not None else None,
            "duration_matched": actual_duration == duration if actual_duration is not None else None}
        notes = []
        if moved:
            result["other_clips_changed"] = moved[:20]
            notes.append("Inserting the title moved or changed %d existing clip(s); Resolve made an insert or "
                         "overwrite edit. Undo in Resolve (Cmd+Z) if that is not wanted." % len(moved))
        if result["placement"]["track_matched"] is False:
            notes.append("Resolve put the title on V%s, not V%d: scripted titles go where Resolve chooses and the "
                         "ordinary API cannot pick the track. Drag it to V%d.%s" % (
                             actual_track, target, target, " The new V%d was left empty." % target if new_track else ""))
        if result["placement"]["start_matched"] is False:
            notes.append("Resolve placed the title at frame %s, not %d." % (actual_start, start))
        if result["placement"]["duration_matched"] is False:
            notes.append("Requested %d frames; Resolve made the title %s frames (its standard generator length). "
                         "Trim it in the timeline." % (duration, actual_duration))
        if tool is None:
            result.update(text_set=False, font_set=False, size_set=False, center_set=False, verified=False,
                          readback=[])
            notes.append("The inserted title has no Text+ tool the API can reach, so no styling was applied. "
                         "Style it in the Inspector.")
            result["notes"] = notes
            return result
        finished, errors = self._apply_text_plus(comp, tool, inputs)
        readback = []
        for key, value in inputs:
            actual = call(tool, "GetInput", None, key)
            readback.append({"input": key, "requested": list(value) if isinstance(value, tuple) else value,
                             "actual": titles.plain_value(actual), "set": titles.readback_matches(value, actual)})
        status = {entry["input"]: entry["set"] for entry in readback}
        font_actual = call(tool, "GetInput", None, "Font")
        result.update(text_set=status.get("StyledText"), font_set=status.get("Font"), size_set=status.get("Size"),
                      center_set=status.get("Center"), readback=readback, lua_finished=finished,
                      font_actual=titles.plain_value(font_actual))
        flags = [entry["set"] for entry in readback]
        result["verified"] = False if False in flags else None if None in flags else True
        if errors:
            result["apply_errors"] = errors[:10]
        mismatched = [entry["input"] for entry in readback if entry["set"] is False]
        unknown = [entry["input"] for entry in readback if entry["set"] is None]
        if mismatched:
            notes.append("Text+ did not keep %s; check the Inspector." % ", ".join(mismatched))
        if unknown:
            notes.append("Text+ did not report %s back, so they are unverified." % ", ".join(unknown))
        if font.get("note"):
            notes.append(font["note"])
        if isinstance(font_actual, str) and font_actual and not font.get("family"):
            if not titles.resolve_font(font_actual).get("found"):
                notes.append("The template font '%s' is not installed on this Mac; Text+ will substitute one." % font_actual)
        notes += placement.get("notes", [])
        if notes:
            result["notes"] = notes
        return result

    def _op_timeline_frame(self, params):
        import base64
        import shutil
        import subprocess
        import tempfile

        timeline = self._timeline()
        fps = self._project_rate()
        requested_tc = params.get("timecode")
        requested_frame = params.get("frame")
        if requested_tc and requested_frame is not None:
            raise OperationError("Pass timecode or frame, not both.")
        if requested_frame is not None:
            # Public frame inputs are absolute, like timeline_overview and split_clip.
            start_tc = call(timeline, "GetStartTimecode", "00:00:00:00")
            offset = int(requested_frame) - int(call(timeline, "GetStartFrame", 0) or 0)
            requested_tc = self._frames_tc(self._tc_frames(start_tc, fps) + offset, fps, ";" in start_tc)
        if requested_tc:
            self._tc_frames(requested_tc, fps)
            if not timeline.SetCurrentTimecode(str(requested_tc)):
                raise OperationError("Resolve refused to seek to %s." % requested_tc)
        current_tc = call(timeline, "GetCurrentTimecode", "00:00:00:00")
        current_frame = self._playhead_frame(timeline)
        mode = str(params.get("mode", "auto")).lower()
        if mode not in ("auto", "source", "composite"):
            raise OperationError("mode must be auto, source, or composite.")
        fmt = str(params.get("format", "jpg")).lower().lstrip(".")
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in ("png", "jpg"):
            raise OperationError("format must be jpg or png.")
        max_width = int(params.get("max_width", 1280))
        capture_dir = Path(tempfile.gettempdir()) / "resolve-frame-captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        path = capture_dir / ("frame_%s.%s" % (uuid.uuid4().hex, fmt))
        method = None
        clip_name = None
        if mode in ("auto", "composite"):
            if call(self._project(), "ExportCurrentFrameAsStill", False, str(path)) and path.exists() and path.stat().st_size:
                method = "export_current_frame_as_still"
        if method is None and mode in ("auto", "source"):
            # Topmost real picture under the playhead, found with a binary search per track (a few calls even
            # on long timelines); skips disabled clips and the bridge's own caption overlays.
            item, info = None, None
            for track_index, candidate in self._video_items_at(timeline, current_frame):
                if call(candidate, "GetClipEnabled", True) is False:
                    continue
                media_item = call(candidate, "GetMediaPoolItem", None)
                if "/.resolve-ai-bridge/captions/" in str(call(media_item, "GetClipProperty", "", "File Path") or ""):
                    continue
                item = candidate
                info = {"name": call(candidate, "GetName", ""), "track_type": "video", "track_index": track_index}
                break
            if item is None:
                item, info = self._resolve_one_item("playhead")
            clip_name = info.get("name")
            media = call(item, "GetMediaPoolItem", None)
            file_path = call(media, "GetClipProperty", "", "File Path")
            if not file_path or not Path(file_path).is_file():
                raise OperationError("The visible clip has no readable source file. Source fallback cannot capture generators or offline media.")
            source_seconds, _ = self._source_window(item, current_frame) if Path(file_path).suffix.lower() not in IMAGE_SUFFIXES else (0.0, 0.0)
            ffmpeg = self._ffmpeg_path(self.runtime_home)
            if not ffmpeg:
                raise OperationError("Source capture needs ffmpeg on PATH (Free-compatible). Composite capture was unavailable.")
            try:
                result = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", "%.9f" % source_seconds, "-i", str(file_path), "-frames:v", "1", str(path)], capture_output=True, timeout=45)
            except subprocess.TimeoutExpired:
                raise OperationError("Source frame extraction timed out.")
            if result.returncode == 0 and path.exists() and path.stat().st_size:
                method = "source_ffmpeg"
        if method is None:
            raise OperationError("Composite capture is unavailable on this Resolve build. Try mode='source' with ffmpeg installed; source images exclude timeline effects.")
        width, height = self._image_dimensions(path)
        resized = False
        if width and max_width > 0 and width > max_width:
            target = path.with_name(path.stem + "_small" + path.suffix)
            ffmpeg = self._ffmpeg_path(self.runtime_home)
            sips = shutil.which("sips")
            command = ([ffmpeg, "-v", "error", "-y", "-i", str(path), "-vf", "scale=%d:-1" % max_width, str(target)] if ffmpeg else
                       [sips, "--resampleWidth", str(max_width), str(path), "--out", str(target)] if sips else None)
            if command:
                try:
                    result = subprocess.run(command, capture_output=True, timeout=30)
                    if result.returncode == 0 and target.exists() and target.stat().st_size:
                        path.unlink()
                        path = target
                        width, height = self._image_dimensions(path)
                        resized = True
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return {"timecode": current_tc, "frame": current_frame, "frame_space": "absolute_timeline",
                "clip_name": clip_name, "method": method, "composited": method == "export_current_frame_as_still",
                "note": "Source fallback excludes grades, Fusion, transforms, overlays and timeline retiming." if method == "source_ffmpeg" else "Native timeline still.",
                "width": width, "height": height, "resized": resized, "format": fmt,
                "image_base64": base64.b64encode(path.read_bytes()).decode("ascii"), "file_path": str(path)}

    def _op_timeline_audio(self, params):
        import array
        import shutil
        import subprocess
        import sys
        import tempfile
        import wave

        timeline = self._timeline()
        fps = self._project_rate()
        action = str(params.get("action", "analyze")).lower()
        actions = ("analyze", "silence_cuts", "energy_envelope", "onsets", "vad_clusters", "export_slice")
        if action not in actions:
            raise OperationError("Unknown audio action. Choose: %s" % ", ".join(actions))
        current_frame = self._playhead_frame(timeline)
        requested = params.get("item_id")
        if requested:
            target, info = self._find_timeline_items([requested])[0]
        else:
            track = int(params.get("track_index", 1))
            candidates = [(item, info) for item, info in self._timeline_items()
                          if info["track_type"] in ("audio", "video") and info["track_index"] == track
                          and info.get("enabled") is not False
                          and call(timeline, "GetIsTrackEnabled", True, info["track_type"], track) is not False
                          and float(info["start"]) <= current_frame < float(info["end"])]
            candidates.sort(key=lambda pair: pair[1]["track_type"] != "audio")
            if not candidates:
                raise OperationError("No enabled audio/video clip under the playhead on track %d. Pass an explicit item_id." % track)
            target, info = candidates[0]
        media = call(target, "GetMediaPoolItem", None)
        file_path = call(media, "GetClipProperty", "", "File Path")
        if not file_path or not Path(file_path).is_file():
            raise OperationError("Selected clip has no readable source audio file.")
        clip_start = float(call(target, "GetStart", 0) or 0)
        clip_end = float(call(target, "GetEnd", 0) or 0)
        origin = current_frame if clip_start <= current_frame < clip_end else clip_start
        if params.get("whole_clip"):
            origin = clip_start
        source_seconds, duration = self._source_window(target, origin)
        max_seconds = float(params.get("max_duration_seconds", 300))
        if not math.isfinite(max_seconds) or not 0 < max_seconds <= 3600:
            raise OperationError("max_duration_seconds must be between 0 and 3600.")
        duration = min(duration, max_seconds)
        if duration <= 0:
            raise OperationError("The selected source range is empty.")
        threshold = float(params.get("silence_threshold_db", -40))
        minimum = float(params.get("min_silence_duration", 0.3))
        if not math.isfinite(threshold) or not -120 <= threshold <= 0 or not math.isfinite(minimum) or minimum < 0:
            raise OperationError("Use a finite threshold from -120 to 0 dBFS and a nonnegative silence duration.")
        audio_dir = Path(tempfile.gettempdir()) / "resolve-audio-analysis"
        audio_dir.mkdir(parents=True, exist_ok=True)
        slice_path = audio_dir / ("slice_%s.wav" % uuid.uuid4().hex)
        full_path = audio_dir / ("decode_%s.wav" % uuid.uuid4().hex)
        ffmpeg, afconvert = self._ffmpeg_path(self.runtime_home), shutil.which("afconvert")
        try:
            if ffmpeg:
                command = [ffmpeg, "-v", "error", "-y", "-ss", "%.9f" % source_seconds, "-i", str(file_path), "-t", "%.9f" % duration,
                           "-vn", "-acodec", "pcm_s16le", "-ar", "48000", str(slice_path)]
                result = subprocess.run(command, capture_output=True, timeout=90)
                if result.returncode:
                    raise OperationError("ffmpeg could not decode the selected audio range: %s" % result.stderr.decode(errors="replace")[-500:])
            elif afconvert:
                result = subprocess.run([afconvert, "-f", "WAVE", "-d", "LEI16@48000", str(file_path), str(full_path)], capture_output=True, timeout=90)
                if result.returncode:
                    raise OperationError("afconvert could not decode audio. Install ffmpeg for additional formats.")
                with wave.open(str(full_path), "rb") as source:
                    source.setpos(min(source.getnframes(), round(source_seconds * source.getframerate())))
                    raw = source.readframes(round(duration * source.getframerate()))
                    with wave.open(str(slice_path), "wb") as output:
                        output.setparams(source.getparams())
                        output.writeframes(raw)
            else:
                raise OperationError("Audio analysis requires ffmpeg or macOS afconvert; neither requires Resolve Studio.")
            with wave.open(str(slice_path), "rb") as source:
                channels, rate = source.getnchannels(), source.getframerate()
                if source.getsampwidth() != 2:
                    raise OperationError("Decoder did not return 16-bit PCM audio.")
                samples = array.array("h", source.readframes(source.getnframes()))
                if sys.byteorder != "little":
                    samples.byteswap()
        except subprocess.TimeoutExpired:
            slice_path.unlink(missing_ok=True)
            raise OperationError("Audio decoding timed out; select a shorter source or install ffmpeg for efficient range decoding.")
        except Exception:
            slice_path.unlink(missing_ok=True)
            raise
        finally:
            full_path.unlink(missing_ok=True)
        count = len(samples) // channels
        if not count:
            slice_path.unlink(missing_ok=True)
            raise OperationError("No audio samples were decoded in the selected range.")
        duration = count / rate
        report = {"action": action, "item_id": info.get("id"), "clip_name": call(target, "GetName", ""),
                  "track_type": info.get("track_type"), "timecode": call(timeline, "GetCurrentTimecode", None),
                  "source_start_sec": source_seconds, "timeline_start_frame": origin, "frame_rate": fps,
                  "duration_sec": duration, "sample_rate": rate, "channels": channels,
                  "truncated": duration + 1 / fps < (clip_end - origin) / fps,
                  "scope": "source_audio", "note": "Source PCM only: excludes timeline gain, mute, fades, Fairlight effects, mixing and Fusion retiming."}
        def db(value):
            return 20 * math.log10(max(1e-6, value))
        def windows(seconds):
            size = max(1, round(rate * seconds))
            for begin in range(0, count, size):
                finish = min(count, begin + size)
                block = samples[begin * channels:finish * channels]
                energy = sum(float(s) * s for s in block) / len(block) / (32768.0 ** 2)
                yield begin / rate, finish / rate, db(math.sqrt(energy))
        def interval(begin, finish):
            relative_start, relative_end = round(begin * fps), round(finish * fps)
            return {"start_sec": begin, "end_sec": finish, "duration_sec": finish - begin,
                    "start_frame": relative_start, "end_frame": relative_end,
                    "timeline_start_frame": origin + relative_start, "timeline_end_frame": origin + relative_end}
        if action == "analyze":
            levels = []
            for channel in range(channels):
                values = samples[channel::channels]
                peak = max(abs(v) for v in values) / 32768.0
                rms = math.sqrt(sum(float(v) * v for v in values) / len(values)) / 32768.0
                levels.append({"channel": channel + 1, "peak_dbfs": round(db(peak), 2), "rms_dbfs": round(db(rms), 2), "is_clipping": peak >= 32767 / 32768})
            peak = max(level["peak_dbfs"] for level in levels)
            rms = db(math.sqrt(sum(float(v) * v for v in samples) / len(samples)) / 32768.0)
            report.update(peak_dbfs=peak, rms_dbfs=round(rms, 2), is_clipping=any(level["is_clipping"] for level in levels),
                          channel_levels=levels, health="warning_loud" if peak >= -0.5 else "warning_quiet" if rms <= -35 else "healthy")
        elif action in ("silence_cuts", "vad_clusters"):
            speech = action == "vad_clusters"
            boundary = float(params.get("threshold_db", -34)) if speech else threshold
            if not math.isfinite(boundary):
                raise OperationError("threshold_db must be finite.")
            regions, beginning = [], None
            for begin, finish, level in windows(0.05):
                selected = level > boundary if speech else level <= boundary
                if selected and beginning is None:
                    beginning = begin
                if not selected and beginning is not None:
                    if begin - beginning + 1e-9 >= (0 if speech else minimum):
                        regions.append(interval(beginning, begin))
                    beginning = None
            if beginning is not None and duration - beginning + 1e-9 >= (0 if speech else minimum):
                regions.append(interval(beginning, duration))
            report.update(threshold_db=boundary, frame_space="slice_relative; timeline_* fields are absolute", end_exclusive=True)
            report.update({"clusters" if speech else "cuts": regions, "clusters_count" if speech else "silence_intervals_count": len(regions)})
        elif action == "energy_envelope":
            envelope = [{"time_sec": begin, "rms_dbfs": round(level, 2)} for begin, _, level in windows(0.05)]
            report.update(envelope=envelope, samples_count=len(envelope))
        elif action == "onsets":
            previous, onsets = -120, []
            lead = float(params.get("lead_offset_ms", 160)) / 1000
            if not math.isfinite(lead):
                raise OperationError("lead_offset_ms must be finite.")
            for begin, _, level in windows(0.01):
                if level > -34 and level - previous > 6:
                    moment = max(0, begin - lead)
                    onsets.append({"onset_sec": moment, "frame": round(moment * fps), "timeline_frame": origin + round(moment * fps)})
                previous = level
            report.update(onsets=onsets, onsets_count=len(onsets), lead_offset_ms=lead * 1000)
        if action in ("analyze", "export_slice"):
            report["wav_path"] = str(slice_path)
        else:
            slice_path.unlink(missing_ok=True)
        return report

    def _op_change_clip_speed(self, params):
        item, info = self._resolve_one_item(params.get("item_id"), track_hint=params.get("track_index") or "video")
        if info.get("track_type") != "video":
            raise OperationError("Fusion speed changes support video only; linked audio is unchanged.")
        supplied = [(key, params[key]) for key in ("speed", "speed_multiplier", "speed_percent", "slow_down_percent", "speed_up_percent") if params.get(key) is not None]
        if len(supplied) != 1:
            raise OperationError("Provide exactly one speed value: speed, speed_percent, slow_down_percent, or speed_up_percent.")
        key, value = supplied[0]
        try:
            speed = float(value)
            if key == "speed_percent":
                speed /= 100
            elif key == "slow_down_percent":
                speed = 1 - speed / 100
            elif key == "speed_up_percent":
                speed = 1 + speed / 100
        except (ValueError, TypeError):
            raise OperationError("Invalid speed value.")
        if not math.isfinite(speed) or speed <= 0:
            raise OperationError("speed must be finite and greater than zero.")
        method = str(params.get("method", "fusion")).lower()
        reverse = bool(params.get("reverse", False))
        if method in ("clip_attributes", "media_pool", "fps"):
            if reverse:
                raise OperationError("Clip FPS attributes cannot reverse playback.")
            media = call(item, "GetMediaPoolItem", None)
            try:
                original_fps = float(call(media, "GetClipProperty", None, "FPS"))
            except (ValueError, TypeError):
                raise OperationError("Resolve did not report the source frame rate.")
            new_fps = round(original_fps * speed, 6)
            if not math.isfinite(new_fps) or new_fps <= 0 or not call(media, "SetClipProperty", False, "FPS", str(new_fps)):
                raise OperationError("Resolve refused the new source frame rate.")
            actual = call(media, "GetClipProperty", None, "FPS")
            if actual is None or abs(float(actual) - new_fps) > 0.01:
                raise OperationError("Clip FPS read-back differs from the request. Inspect clip attributes before continuing.")
            return {"item_id": info["id"], "method": "clip_attributes_fps", "speed": speed,
                    "previous_clip_fps": original_fps, "clip_fps": actual,
                    "scope": "media_pool", "note": "Changes source interpretation for EVERY use of this media, relative to its current FPS. Does not provide a per-clip reset."}
        if method != "fusion":
            raise OperationError("method must be fusion or clip_attributes.")
        if reverse:
            raise OperationError("Reverse TimeSpeed source-range mapping is not verified; use Resolve's Change Clip Speed dialog for reverse playback.")
        comp = call(item, "GetFusionCompByIndex", None, 1)
        if not comp and abs(speed - 1) < 1e-9:
            return {"item_id": info["id"], "speed": 1.0, "changed": False, "note": "No bridge-owned speed node to reset."}
        comp = comp or call(item, "AddFusionComp", None)
        if not comp:
            raise OperationError("Could not access this clip's Fusion composition.")
        owner = "resolve-ai-bridge.change_clip_speed.v1"
        _comp_open(comp)
        created = False
        node = None
        output_input = None
        previous_output = None
        try:
            tools = comp.GetToolList(False) or {}
            values = list(tools.values()) if isinstance(tools, dict) else list(tools)
            owned = [tool for tool in values if call(tool, "GetData", None, "ResolveAIBridgeOwner") == owner]
            if len(owned) > 1:
                raise OperationError("Multiple bridge speed nodes found. Inspect Fusion before changing speed.")
            node = owned[0] if owned else None
            if node is None and abs(speed - 1) < 1e-9:
                return {"item_id": info["id"], "speed": 1.0, "changed": False,
                        "note": "No bridge-owned speed node to reset. Existing user/legacy TimeSpeed nodes were preserved."}
            if node is None:
                media_out = comp.FindTool("MediaOut1")
                if not media_out:
                    raise OperationError("Cannot locate MediaOut1; inspect Fusion before changing speed.")
                output_input = media_out.FindMainInput(1)
                previous_output = output_input.GetConnectedOutput() if output_input else None
                if not previous_output:
                    raise OperationError("MediaOut has no connected source; nothing was changed.")
                node = comp.AddTool("TimeSpeed")
                if not node:
                    raise OperationError("Resolve could not create the Free-compatible TimeSpeed node.")
                created = True
                node.SetAttrs({"TOOLS_Name": "ResolveAIBridgeTimeSpeed"})
                node.SetData("ResolveAIBridgeOwner", owner)
                if call(node, "GetData", None, "ResolveAIBridgeOwner") != owner:
                    raise OperationError("Could not mark the bridge-owned speed node.")
                if not node.FindMainInput(1).ConnectTo(previous_output):
                    raise OperationError("Could not connect the speed node to the existing Fusion chain.")
            # Neutral speed leaves every pre-existing connection intact.
            node.SetInput("Speed", speed)
            actual = call(node, "GetInput", None, "Speed")
            if actual is None or not math.isfinite(float(actual)) or abs(float(actual) - speed) > 1e-6:
                raise OperationError("TimeSpeed did not accept the requested speed; inspect Fusion.")
            interpolate = bool(params.get("interpolate_frames", True))
            interpolation_set = bool(call(node, "SetInput", False, "InterpolateBetweenFrames", 1.0 if interpolate else 0.0))
            if created and not output_input.ConnectTo(node.FindMainOutput(1)):
                raise OperationError("Could not connect the speed node to MediaOut.")
        except Exception:
            if created and node:
                if output_input and previous_output:
                    output_input.ConnectTo(previous_output)
                node.Delete()
            raise
        finally:
            _comp_close(comp)
        return {"item_id": info["id"], "name": info.get("name"), "method": "fusion_timespeed", "speed": speed,
                "speed_percent": speed * 100, "reverse": False, "tool": getattr(node, "Name", "ResolveAIBridgeTimeSpeed"),
                "interpolate_frames": interpolate, "interpolation_set": interpolation_set,
                "note": "Video-only constant speed. Timeline duration and linked audio are unchanged. Existing Fusion connections are preserved; 1x neutralizes only the bridge-owned node."}

    def _op_create_compound_clip(self, params):
        timeline = self._timeline()
        ids = params.get("item_ids")
        if not ids:
            item_id = params.get("item_id")
            if item_id:
                ids = [item_id]
        if not ids:
            ids = ["playhead"]

        targets = self._find_timeline_items(ids)
        items = [item for item, _ in targets]

        clip_info = {}
        name = params.get("name")
        if name:
            clip_info["name"] = str(name).strip()
        tc = params.get("start_timecode")
        if tc:
            clip_info["startTimecode"] = str(tc).strip()

        created_item = timeline.CreateCompoundClip(items, clip_info) if clip_info else timeline.CreateCompoundClip(items)
        if not created_item:
            raise OperationError("Resolve could not create compound clip from the specified items.")

        new_id = self._item_id_after_rebuild(timeline, created_item) if not isinstance(created_item, bool) else None
        result = {
            "created_compound_clip": {
                "id": new_id,
                "name": str(call(created_item, "GetName", clip_info.get("name", "Compound Clip"))) if not isinstance(created_item, bool) else clip_info.get("name", "Compound Clip"),
                "duration": call(created_item, "GetDuration", None),
                "start_frame": call(created_item, "GetStart", None),
                "end_frame": call(created_item, "GetEnd", None),
                "unique_id": call(created_item, "GetUniqueId", None),
            },
            "source_items": [info["id"] for _, info in targets],
        }
        if not new_id:
            result["note"] = "Resolve reported creation but did not identify the new item. Inspect timeline_overview before targeting it."
        return result

    def _timeline_snapshot(self, timeline, include_track_state=True):
        """Read comparable structure without changing the active timeline."""
        tracks = []
        for kind in ("video", "audio", "subtitle"):
            for index in range(1, int(call(timeline, "GetTrackCount", 0, kind) or 0) + 1):
                clips = []
                for position, item in enumerate(self._track_clips(timeline, kind, index), 1):
                    info = self._item_summary(item, kind, index, position)
                    # Copies have new UUIDs, but identical source ranges and properties.
                    for key in ("id", "unique_id"):
                        info.pop(key, None)
                    info["source_start"] = call(item, "GetSourceStartFrame", None)
                    info["source_end"] = call(item, "GetSourceEndFrame", None)
                    info["properties"] = plain(call(item, "GetProperty", {}) or {})
                    info["fusion_count"] = call(item, "GetFusionCompCount", 0)
                    clips.append(info)
                tracks.append({"type": kind, "index": index, "name": call(timeline, "GetTrackName", "", kind, index),
                               "enabled": call(timeline, "GetIsTrackEnabled", None, kind, index) if include_track_state else None,
                               "locked": call(timeline, "GetIsTrackLocked", None, kind, index) if include_track_state else None, "clips": clips})
        return {"start": call(timeline, "GetStartFrame", None), "end": call(timeline, "GetEndFrame", None),
                "settings": {key: call(timeline, "GetSetting", None, key) for key in ("timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight")},
                "tracks": tracks}

    def _timeline_fingerprint(self, timeline):
        import hashlib
        return hashlib.sha256(json.dumps(self._timeline_snapshot(timeline), sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _timeline_identity(timeline):
        return {"id": call(timeline, "GetUniqueId", None), "name": call(timeline, "GetName", "")}

    @staticmethod
    def _same_timeline(first, second):
        if first is None or second is None:
            return False
        first_id = call(first, "GetUniqueId", None)
        second_id = call(second, "GetUniqueId", None)
        return str(first_id) == str(second_id) if first_id and second_id else first == second

    def _is_current_timeline(self, project, timeline):
        return self._same_timeline(call(project, "GetCurrentTimeline", None), timeline)

    def _activate_timeline(self, project, timeline):
        call(project, "SetCurrentTimeline", False, timeline)
        return self._is_current_timeline(project, timeline)

    def _duplicate_current(self, name, open_duplicate=True):
        project, original = self._project(), self._timeline()
        before = self._timeline_snapshot(original)
        unique_name = "%s — %s" % (str(name or (original.GetName() + " preview"))[:120], uuid.uuid4().hex[:8])
        duplicate = call(original, "DuplicateTimeline", None, unique_name)
        if duplicate is None or isinstance(duplicate, bool) or self._same_timeline(original, duplicate):
            raise OperationError("Resolve did not return a separate timeline copy; no clip edits were attempted.")
        try:
            # DuplicateTimeline may activate its copy. Resolve can report false
            # track-enabled flags on inactive timelines, so verify each while open.
            if not self._activate_timeline(project, original):
                raise OperationError("Could not reopen the original to verify its checkpoint.")
            if self._timeline_snapshot(original) != before:
                raise OperationError("Timeline changed while it was being copied; inspect both timelines before editing.")
            if not self._activate_timeline(project, duplicate):
                raise OperationError("Could not open the duplicate to verify its structure.")
            if self._timeline_snapshot(duplicate) != before:
                raise OperationError("The duplicate did not match the original timeline structure; no edit was attempted.")
            target = duplicate if open_duplicate else original
            if not self._activate_timeline(project, target):
                raise OperationError("Could not open the requested timeline after duplication.")
        except Exception:
            call(project, "SetCurrentTimeline", False, original)
            raise
        identity = self._timeline_identity(duplicate)
        identity["bin"] = self._file_into_bin(duplicate, getattr(self, "_bin", self.AGENT_BIN))
        return duplicate, identity

    def _op_preview_timeline(self, params):
        original = self._timeline_identity(self._timeline())
        _, preview = self._duplicate_current(params.get("name"), open_duplicate=True)
        return {"original": original, "preview": preview, "active": "preview",
                "note": "Original retained. Call timeline_overview for NEW clip IDs, apply edits to the preview, then compare_timelines. No edits have been applied yet."}

    AGENT_BIN = "Agent Work"  # One convention: intermediates, checkpoints and archived copies all live here.
    CREATES_TIMELINES = frozenset({"preview_timeline", "restore_checkpoint", "remove_ranges", "apply_silence_cuts",
                                   "trim_edit", "slip_clip", "slide_clip", "roll_edit", "edit_points", "split_clip",
                                   "add_transitions", "create_timeline"})

    def _preferences_home(self):
        return self.runtime_home or os.environ.get("RESOLVE_AI_BRIDGE_HOME")

    def _preference(self, key, default=None):
        """This project's saved preference (bridge home preferences.json), or default. Never raises."""
        home = self._preferences_home()
        if not home:
            return default
        try:
            from bridge import preferences
            project = call(self._project(), "GetName", None)
            value = preferences.get(home, project).get(key)
        except Exception:
            return default
        return default if value is None else value

    def _op_get_preferences(self, params):
        from bridge import preferences
        home = self._preferences_home()
        project = call(self._project(), "GetName", None)
        return {"project": project, "preferences": preferences.get(home, project) if home else {},
                "known": {key: description for key, (_, description) in preferences.SCHEMA.items()},
                "stored_in": str(Path(str(home)) / preferences.FILE_NAME) if home else None}

    def _op_set_preferences(self, params):
        from bridge import preferences
        home = self._preferences_home()
        if not home:
            raise OperationError("This transport has no bridge home to store preferences in.")
        project = call(self._project(), "GetName", None)
        try:
            entry = preferences.set_values(home, project, params.get("updates"))
        except preferences.PreferenceError as exc:
            raise OperationError(str(exc))
        return {"project": project, "preferences": entry,
                "note": "Saved for this project in the bridge home; nothing in Resolve changed."}
    CHECKPOINT_BIN = AGENT_BIN
    CHECKPOINT_PREFIX = "Checkpoint · "

    def _file_into_bin(self, timeline, name=None):
        """Best effort: move a bridge-made timeline into a top-level bin (created when missing), restoring the
        media pool's current folder (AddSubFolder switches it). Returns the bin name, or None if not filed."""
        name = self.AGENT_BIN if name is None else str(name).strip()
        if not name:
            return None
        pool = call(self._project(), "GetMediaPool", None)
        root = call(pool, "GetRootFolder", None)
        item = call(timeline, "GetMediaPoolItem", None)
        if pool is None or root is None or item is None:
            return None
        browsing = call(pool, "GetCurrentFolder", None)
        try:
            folder = next((f for f in call(root, "GetSubFolderList", []) or [] if call(f, "GetName", "") == name), None)
            if folder is None:
                folder = call(pool, "AddSubFolder", None, root, name)
            if folder is None or not call(pool, "MoveClips", False, [item], folder):
                return None
            return name
        finally:  # AddSubFolder opens the new bin; put the user's media pool view back where it was.
            if browsing is not None:
                call(pool, "SetCurrentFolder", False, browsing)

    def _file_into_checkpoint_bin(self, timeline):
        return self._file_into_bin(timeline, self.CHECKPOINT_BIN)

    def _op_checkpoint_timeline(self, params):
        """Save a verified copy of the current timeline as it is now, then stay on the original."""
        original = self._timeline()
        name = "%s%s · %s" % (self.CHECKPOINT_PREFIX, original.GetName(), str(params.get("label") or "before AI edit")[:60])
        self._bin = self.CHECKPOINT_BIN
        duplicate, identity = self._duplicate_current(name, open_duplicate=False)
        return {"checkpoint": identity, "timeline": self._timeline_identity(original), "bin": identity.get("bin")}

    def _op_restore_checkpoint(self, params):
        """Open a fresh copy of a checkpoint. Nothing is deleted: the timeline that was open stays as it was,
        and the checkpoint itself stays untouched so it can be restored again."""
        project = self._project()
        previous = self._timeline_identity(self._timeline())
        try:
            checkpoint = self._find_timeline(params.get("checkpoint_id"))
        except OperationError:
            raise OperationError("That checkpoint timeline is gone (deleted or renamed in Resolve); nothing was changed.")
        if not self._activate_timeline(project, checkpoint):
            raise OperationError("Resolve would not open the checkpoint timeline; nothing was changed.")
        base = str(checkpoint.GetName())
        if base.startswith(self.CHECKPOINT_PREFIX):
            base = base[len(self.CHECKPOINT_PREFIX):].rsplit(" · ", 1)[0]
        restored, identity = self._duplicate_current("%s (rewound)" % base, open_duplicate=True)
        return {"restored": identity, "from_checkpoint": self._timeline_identity(checkpoint), "previous": previous,
                "note": "Opened a copy of the timeline as it was. The previous timeline was kept, unchanged. "
                        "Every clip in the copy has a NEW unique id: re-read ids with timeline_overview before editing."}

    def _op_list_checkpoints(self, params):
        """Read-only: every checkpoint/snapshot timeline (named "Checkpoint · <timeline> · <label> — <hex>")."""
        from bridge import timeline_cleanup
        project = self._project()
        current = call(call(project, "GetCurrentTimeline", None), "GetUniqueId", None)
        limit = max(1, min(int(params.get("limit") or 100), 500))
        found = []
        for index in range(1, int(call(project, "GetTimelineCount", 0) or 0) + 1):
            timeline = call(project, "GetTimelineByIndex", None, index)
            name = str(call(timeline, "GetName", "") or "")
            info = timeline_cleanup.classify(name)
            if not info or info["kind"] != "checkpoint":
                continue
            rest = name[len(self.CHECKPOINT_PREFIX):].rsplit(" — ", 1)[0]
            source, _, label = rest.rpartition(" · ")
            uid = call(timeline, "GetUniqueId", None)
            found.append({"id": uid, "name": name, "timeline": source or rest, "label": label if source else "",
                          "current": bool(uid and str(uid) == str(current))})
        return {"snapshots": found[-limit:], "count": len(found), "truncated": len(found) > limit,
                "note": "Oldest first. Restoring opens a NEW copy of the snapshot and deletes nothing."}

    def _diff_state(self, timeline, limit=3000, properties=True):
        """Comparable per-clip state of a timeline (does not open it)."""
        items = []
        for item, info in self._timeline_items(timeline)[:limit]:
            entry = {"id": info["id"], "track": "%s%d" % (info["label"][0], info["track_index"]), "name": info["name"],
                     "start": info["start"], "end": info["end"], "source": info["left_offset"],
                     "media": info["media_pool_id"], "enabled": info["enabled"], "color": info["color"]}
            if properties:
                props = plain(call(item, "GetProperty", {}) or {})
                entry["properties"] = props if isinstance(props, dict) else {}
                if info["track_type"] == "audio":
                    entry["properties"].setdefault("AudioVolume", plain(call(item, "GetProperty", None, "AudioVolume")))
            items.append(entry)
        return {"items": items, "transitions": self._timeline_transitions(timeline),
                "markers": plain(call(timeline, "GetMarkers", {}) or {})}

    @staticmethod
    def _diff_states(before, after):
        """Structured changes between two _diff_state results (or timeline_fingerprint-like dicts)."""
        def key(entry):
            return (entry.get("track"), entry.get("media"), entry.get("source"))
        remaining = list(after["items"])
        by_id = {e["id"]: e for e in remaining if e.get("id")}
        changes, matched = [], []
        for old in before["items"]:
            new = by_id.get(old.get("id"))
            if new is None:
                new = next((e for e in remaining if key(e) == key(old) and e.get("media") is not None), None)
            if new is None:
                new = next((e for e in remaining if e.get("track") == old.get("track") and e.get("media") == old.get("media")
                            and e.get("media") is not None and e.get("start") is not None and old.get("start") is not None
                            and e["start"] < old["end"] and old["start"] < e["end"]), None)
            if new is None:
                changes.append({"change": "removed", "track": old.get("track"), "name": old.get("name"),
                                "before": {k: old.get(k) for k in ("start", "end", "source")}})
                continue
            remaining.remove(new)
            entry = {"item": new.get("id"), "track": new.get("track"), "name": new.get("name")}
            if (old.get("start"), old.get("end")) != (new.get("start"), new.get("end")) or old.get("source") != new.get("source"):
                same_length = None not in (old.get("start"), old.get("end"), new.get("start"), new.get("end")) and \
                    old["end"] - old["start"] == new["end"] - new["start"]
                kind = "moved" if same_length and old.get("source") == new.get("source") else "trimmed"
                changes.append(dict(entry, change=kind, before={k: old.get(k) for k in ("start", "end", "source")},
                                    after={k: new.get(k) for k in ("start", "end", "source")}))
            for field in ("enabled", "color"):
                if old.get(field) != new.get(field):
                    changes.append(dict(entry, change=field, before=old.get(field), after=new.get(field)))
            props_old, props_new = old.get("properties") or {}, new.get("properties") or {}
            diff = {k: [props_old.get(k), props_new.get(k)] for k in set(props_old) | set(props_new)
                    if props_old.get(k) != props_new.get(k)}
            if diff:
                changes.append(dict(entry, change="properties", properties=diff))
        for new in remaining:
            changes.append({"change": "added", "item": new.get("id"), "track": new.get("track"), "name": new.get("name"),
                            "after": {k: new.get(k) for k in ("start", "end", "source")}})
        spans = lambda ts: {(t["track"], t["start_frame"], t["end_frame"], t["name"]) for t in ts or []}
        t_old, t_new = spans(before.get("transitions")), spans(after.get("transitions"))
        m_old, m_new = before.get("markers") or {}, after.get("markers") or {}
        markers = {"added": sorted(set(map(str, m_new)) - set(map(str, m_old))),
                   "removed": sorted(set(map(str, m_old)) - set(map(str, m_new))),
                   "changed": sorted(k for k in set(map(str, m_old)) & set(map(str, m_new))
                                     if {str(a): b for a, b in m_old.items()}[k] != {str(a): b for a, b in m_new.items()}[k])}
        counts = {}
        for change in changes:
            counts[change["change"]] = counts.get(change["change"], 0) + 1
        return {"changes": changes, "counts": counts,
                "transitions": {"added": [dict(zip(("track", "start_frame", "end_frame", "name"), t)) for t in sorted(t_new - t_old)],
                                "removed": [dict(zip(("track", "start_frame", "end_frame", "name"), t)) for t in sorted(t_old - t_new)]},
                "markers": markers}

    def _op_diff_since(self, params):
        """Read-only: what changed on the open timeline since a snapshot timeline, or since a `baseline` state."""
        timeline = self._timeline()
        limit = max(1, min(int(params.get("max_changes") or 300), 2000))
        after = self._diff_state(timeline)
        if params.get("baseline") is not None:
            before = params["baseline"]
            if not isinstance(before, dict) or not isinstance(before.get("items"), list):
                raise OperationError("baseline must be an earlier diff_state result ({items, transitions, markers}).")
            source = {"baseline": True}
        else:
            snapshot_id = params.get("snapshot_id")
            if not snapshot_id:
                raise OperationError("Pass snapshot_id (from snapshot_timeline / list_snapshots) or a baseline.")
            snapshot = self._find_timeline(snapshot_id)
            before = self._diff_state(snapshot)
            source = {"snapshot": self._timeline_identity(snapshot)}
        result = self._diff_states(before, after)
        result["changes_truncated"] = len(result["changes"]) > limit
        result["changes"] = result["changes"][:limit]
        result.update(source, timeline=self._timeline_identity(timeline),
                      note="Clips are matched by unique id, then by track + media + source in, then by overlap. Fades, "
                           "keyframes and Fusion/grade contents are not compared.")
        if params.get("include_state"):
            result["state"] = after  # Pass back as baseline next time ("since last turn").
        return result

    def _find_timeline(self, identifier):
        matches = []
        project = self._project()
        for index in range(1, int(project.GetTimelineCount() or 0) + 1):
            item = project.GetTimelineByIndex(index)
            if str(identifier) in (str(call(item, "GetUniqueId", None)), str(call(item, "GetName", ""))):
                matches.append(item)
        if len(matches) != 1:
            raise OperationError("Timeline identifier is missing or ambiguous. Use a unique ID from list_timelines.")
        return matches[0]

    # ------------------------------------------------ bridge-made timeline cleanup

    def _ai_cleanup_scan(self, project):
        """Every timeline with its bridge-name classification. Never opens a timeline."""
        from bridge import timeline_cleanup
        current = call(project, "GetCurrentTimeline", None)
        current_id = call(current, "GetUniqueId", None)
        entries = []
        for index in range(1, int(call(project, "GetTimelineCount", 0) or 0) + 1):
            item = call(project, "GetTimelineByIndex", None, index)
            if item is None:
                continue
            uid = call(item, "GetUniqueId", None)
            name = str(call(item, "GetName", "") or "")
            info = timeline_cleanup.classify(name)
            is_current = (str(uid) == str(current_id)) if uid and current_id else (current is not None and item == current)
            entries.append({"timeline": item, "id": None if uid is None else str(uid), "index": index, "name": name,
                            "current": bool(is_current), "info": info, "kind": info["kind"] if info else None})
        return entries

    def _ai_cleanup_timeline_ids(self, project):
        """Unique IDs of every timeline now in the project, or None when Resolve will not say."""
        try:
            count = int(call(project, "GetTimelineCount", None))
        except (TypeError, ValueError):
            return None
        ids = set()
        for index in range(1, count + 1):
            uid = call(call(project, "GetTimelineByIndex", None, index), "GetUniqueId", None)
            if uid is None:
                return None
            ids.add(str(uid))
        return ids

    def _ai_cleanup_bin(self, pool, root, create):
        """The top-level 'Agent Work' bin as (folder, created). AddSubFolder opens the new bin; callers restore the view."""
        from bridge import timeline_cleanup
        folder = next((f for f in call(root, "GetSubFolderList", []) or []
                       if call(f, "GetName", "") == timeline_cleanup.ARCHIVE_BIN), None)
        if folder is not None or not create:
            return folder, False
        folder = call(pool, "AddSubFolder", None, root, timeline_cleanup.ARCHIVE_BIN)
        return folder, folder is not None

    @staticmethod
    def _ai_cleanup_bin_item_ids(folder):
        """Unique IDs of the Media Pool items in a bin, or None when they cannot be read."""
        if folder is None:
            return set()
        ids = set()
        for clip in call(folder, "GetClipList", None) or []:
            uid = call(clip, "GetUniqueId", None)
            if uid is None:
                return None
            ids.add(str(uid))
        return ids

    def _op_list_ai_timelines(self, params):
        """Read-only: list the timelines this bridge created, classified by name. Opens, moves and deletes nothing."""
        from bridge import timeline_cleanup
        project = self._project()
        entries = self._ai_cleanup_scan(project)
        names = [{"id": entry["id"], "name": entry["name"]} for entry in entries]
        pool = call(project, "GetMediaPool", None)
        archive, _ = self._ai_cleanup_bin(pool, call(pool, "GetRootFolder", None), create=False)
        archived_ids = self._ai_cleanup_bin_item_ids(archive)
        listed = []
        for entry in entries:
            if not entry["kind"]:
                continue
            timeline, info = entry["timeline"], entry["info"]
            items = {}
            for kind in ("video", "audio", "subtitle"):
                try:
                    tracks = int(call(timeline, "GetTrackCount", 0, kind) or 0)
                except (TypeError, ValueError):
                    tracks = 0
                items[kind] = sum(len(self._track_clips(timeline, kind, index))
                                  for index in range(1, tracks + 1))
            start, end = call(timeline, "GetStartFrame", None), call(timeline, "GetEndFrame", None)
            frames = (end - start) if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None
            try:
                rate = float(call(timeline, "GetSetting", None, "timelineFrameRate"))
            except (TypeError, ValueError):
                rate = None
            source, match, candidates = timeline_cleanup.likely_original(info, names, own_id=entry["id"])
            if archived_ids is None or archive is None:
                in_archive = None if archive is not None else False
            else:
                media_id = call(call(timeline, "GetMediaPoolItem", None), "GetUniqueId", None)
                in_archive = (str(media_id) in archived_ids) if media_id is not None else None
            why_not = timeline_cleanup.refusal(entry, include_checkpoints=True)
            record = {"id": entry["id"], "name": entry["name"], "kind": entry["kind"], "made_by": info["made_by"],
                      "current": entry["current"], "removable": why_not is None,
                      "clip_count": sum(items.values()), "items": items,
                      "start_frame": start, "end_frame": end, "duration_frames": frames,
                      "duration_seconds": round(frames / rate, 3) if frames is not None and rate else None,
                      "copied_from": source, "likely_original": match, "in_archive_bin": in_archive}
            if candidates:
                record["original_candidates"] = candidates
            if why_not:
                record["why_not_removable"] = why_not
            if entry["kind"] == "checkpoint":
                record["protected"] = "Chat panel restore point; cleanup skips it unless include_checkpoints=true."
            listed.append(record)
        return {"project": call(project, "GetName", None), "total_timelines": len(entries),
                "ai_timeline_count": len(listed),
                "by_kind": {kind: sum(1 for r in listed if r["kind"] == kind) for kind in timeline_cleanup.KINDS},
                "ai_timelines": listed,
                "user_timelines": [{"id": e["id"], "name": e["name"], "current": e["current"]} for e in entries if not e["kind"]],
                "archive_bin": timeline_cleanup.ARCHIVE_BIN if archive is not None else None,
                "note": ("Recognised by name only: '<name> — <8 hex>' as the bridge names its copies. A renamed copy is "
                         "not recognised, and timelines without that suffix are never removable. clip_count counts timeline "
                         "items on every track (linked audio separately), read without opening any timeline. "
                         "likely_original is a best-effort exact-name match. Nothing was changed.")}

    def _op_cleanup_ai_timelines(self, params):
        """Archive (move into the 'Agent Work' bin) or delete bridge-made timelines, then verify by re-listing."""
        from bridge import timeline_cleanup
        action = str(params.get("action") or "archive").strip().lower()
        if action not in ("archive", "delete"):
            raise OperationError("action must be 'archive' or 'delete'.")
        requested = params.get("timeline_ids")
        if isinstance(requested, str):
            requested = [requested]
        if not isinstance(requested, (list, tuple)) or not requested:
            raise OperationError("timeline_ids must be a non-empty list of unique IDs from list_ai_timelines.")
        include_checkpoints = params.get("include_checkpoints") in (True, "true", "True")
        project = self._project()
        entries = self._ai_cleanup_scan(project)
        by_id = {}
        for entry in entries:
            by_id.setdefault(entry["id"], []).append(entry)
        accepted, refused, seen = [], [], []
        for raw in requested:
            timeline_id = str(raw).strip()
            if timeline_id in seen:
                continue
            seen.append(timeline_id)
            found = by_id.get(timeline_id) or []
            entry = found[0] if len(found) == 1 else None
            reason = ("Several timelines report this ID; refusing to guess." if len(found) > 1
                      else timeline_cleanup.refusal(entry, include_checkpoints))
            if reason:
                refused.append({"id": timeline_id, "name": entry["name"] if entry else None,
                                "kind": entry["kind"] if entry else None, "reason": reason})
            else:
                accepted.append(entry)
        result = {"action": action, "requested": len(seen), "refused": refused, "include_checkpoints": include_checkpoints,
                  "checkpoints": ("Included on request." if include_checkpoints else
                                  "Checkpoints (chat panel restore points) were excluded; pass include_checkpoints=true to include them.")}
        if not accepted:
            result.update({"archived" if action == "archive" else "deleted": [], "failed": [], "changed": False,
                           "note": "Nothing was changed: no requested timeline could be cleaned up."})
            return result
        if action == "delete":
            result.update(self._ai_cleanup_delete(project, accepted))
            gone = [r for r in result["deleted"] if r["kind"] == "checkpoint"]
            if gone:
                result["warning"] = ("Deleted %d checkpoint(s): the chat panel can no longer restore the timeline for "
                                     "the messages they belonged to." % len(gone))
        else:
            result.update(self._ai_cleanup_archive(project, accepted))
            if any(r["kind"] == "checkpoint" for r in result["archived"]):
                result["checkpoint_note"] = "Archived checkpoints still work for Rewind: it finds them by unique ID, not by bin."
        current = call(project, "GetCurrentTimeline", None)
        result["current_timeline"] = self._timeline_identity(current) if current is not None else None
        return result

    def _ai_cleanup_delete(self, project, accepted):
        pool = self._media_pool()
        returned = {}
        for entry in accepted:
            if self._is_current_timeline(project, entry["timeline"]):
                returned[entry["id"]] = "current"
                continue
            returned[entry["id"]] = call(pool, "DeleteTimelines", None, [entry["timeline"]])
        _TIMELINE_NAME_CACHE.clear()
        remaining = self._ai_cleanup_timeline_ids(project)
        deleted, failed, unverified = [], [], []
        for entry in accepted:
            record = {"id": entry["id"], "name": entry["name"], "kind": entry["kind"]}
            outcome = returned[entry["id"]]
            if outcome == "current":
                failed.append(dict(record, reason="It became the open timeline; not deleted."))
            elif remaining is None:
                unverified.append(dict(record, resolve_returned=plain(outcome),
                                       reason="Resolve would not list timelines afterwards; check the project before retrying."))
            elif entry["id"] in remaining:
                failed.append(dict(record, reason="Still in the project after DeleteTimelines (Resolve returned %r)." % (outcome,)))
            else:
                if outcome is not True:
                    record["resolve_returned"] = plain(outcome)
                deleted.append(record)
        result = {"deleted": deleted, "failed": failed, "changed": bool(deleted) or bool(unverified),
                  "timeline_count_after": None if remaining is None else len(remaining),
                  "verified_by": "re-listing every timeline by unique ID after deleting"}
        if unverified:
            result["unverified"] = unverified
        if failed or unverified:
            result["note"] = "Partial result: only the timelines under 'deleted' are confirmed gone."
        return result

    def _ai_cleanup_archive(self, project, accepted):
        from bridge import timeline_cleanup
        pool = self._media_pool()
        root = call(pool, "GetRootFolder", None)
        if root is None:
            raise OperationError("Resolve did not return the Media Pool root folder; nothing was moved.")
        browsing = call(pool, "GetCurrentFolder", None)
        attempts, failed, created, folder = [], [], False, None
        try:
            folder, created = self._ai_cleanup_bin(pool, root, create=True)
            if folder is None:
                raise OperationError("Could not find or create the '%s' bin; nothing was moved." % timeline_cleanup.ARCHIVE_BIN)
            before = self._ai_cleanup_bin_item_ids(folder) or set()
            for entry in accepted:
                record = {"id": entry["id"], "name": entry["name"], "kind": entry["kind"]}
                item = call(entry["timeline"], "GetMediaPoolItem", None)
                if item is None:
                    failed.append(dict(record, reason="Resolve returned no Media Pool item for this timeline; not moved."))
                    continue
                media_id = call(item, "GetUniqueId", None)
                if media_id is not None and str(media_id) in before:
                    attempts.append((record, media_id, "already"))
                    continue
                attempts.append((record, media_id, call(pool, "MoveClips", None, [item], folder)))
        finally:  # AddSubFolder opens the new bin; put the user's media pool view back where it was.
            view = "unknown: Resolve did not report the open bin beforehand"
            if browsing is not None:
                call(pool, "SetCurrentFolder", False, browsing)
                now = call(pool, "GetCurrentFolder", None)
                same = (now == browsing or (call(now, "GetUniqueId", None) is not None and
                                            call(now, "GetUniqueId", None) == call(browsing, "GetUniqueId", None)))
                view = "restored" if same else "not confirmed: Resolve did not report the previously open bin as open again"
        in_bin = self._ai_cleanup_bin_item_ids(folder)
        still = self._ai_cleanup_timeline_ids(project)
        archived, unverified = [], []
        for record, media_id, outcome in attempts:
            if still is not None and record["id"] not in still:
                failed.append(dict(record, reason="The timeline is no longer in the project after the move."))
            elif outcome == "already":
                archived.append(dict(record, already_archived=True))
            elif media_id is not None and in_bin is not None:
                if str(media_id) in in_bin:
                    archived.append(record if outcome is True else dict(record, resolve_returned=plain(outcome)))
                else:
                    failed.append(dict(record, reason="Not in the '%s' bin after MoveClips (Resolve returned %r)."
                                       % (timeline_cleanup.ARCHIVE_BIN, outcome)))
            else:
                unverified.append(dict(record, resolve_returned=plain(outcome),
                                       reason="Resolve did not expose bin contents to confirm the move."))
        result = {"archived": archived, "failed": failed, "bin": timeline_cleanup.ARCHIVE_BIN, "bin_created": created,
                  "media_pool_view": view, "changed": any(not r.get("already_archived") for r in archived) or bool(unverified),
                  "verified_by": "reading the bin's contents and re-listing every timeline by unique ID"}
        if unverified:
            result["unverified"] = unverified
        if failed or unverified:
            result["note"] = "Partial result: only the timelines under 'archived' are confirmed in the bin."
        return result

    def _op_compare_timelines(self, params):
        original = self._find_timeline(params.get("original"))
        preview = self._find_timeline(params.get("preview"))
        # Comparing without opening either timeline cannot reliably compare track
        # enabled/locked flags on all builds. Omit those flags from both snapshots.
        before = self._timeline_snapshot(original, include_track_state=False)
        after = self._timeline_snapshot(preview, include_track_state=False)
        changes = []
        for key in ("start", "end", "settings"):
            if before[key] != after[key]:
                changes.append({"field": key, "before": before[key], "after": after[key]})
        old = {(track["type"], track["index"]): track for track in before["tracks"]}
        new = {(track["type"], track["index"]): track for track in after["tracks"]}
        for key in sorted(old.keys() | new.keys()):
            if old.get(key) != new.get(key):
                changes.append({"track": "%s%d" % key, "before": old.get(key), "after": new.get(key)})
        before_markers = plain(call(original, "GetMarkers", {}) or {})
        after_markers = plain(call(preview, "GetMarkers", {}) or {})
        if before_markers != after_markers:
            changes.append({"field": "markers", "before": before_markers, "after": after_markers})
        return {"original": self._timeline_identity(original), "preview": self._timeline_identity(preview),
                "structurally_equal": not changes, "changes": changes,
                "limitations": "Compares track names/counts, ranges, source references, readable clip properties, Fusion counts and markers. Track enabled/locked flags are omitted because inactive timelines can report them incorrectly. Does not compare rendered pixels, internal Fusion graphs, full color grades or Fairlight processing."}

    def _op_project_health(self, params):
        timeline, project = self._timeline(), self._project()
        findings = []
        start, end = float(timeline.GetStartFrame()), float(timeline.GetEndFrame())
        for track in self._timeline_snapshot(timeline)["tracks"]:
            label = "%s%d" % (track["type"], track["index"])
            if track["enabled"] is False:
                findings.append({"type": "disabled_track", "track": label, "severity": "info"})
            if track["locked"]:
                findings.append({"type": "locked_track", "track": label, "severity": "info"})
            cursor = start
            for clip in sorted(track["clips"], key=lambda clip: clip["start"]):
                if clip["enabled"] is False:
                    findings.append({"type": "disabled_clip", "clip": clip["label"], "severity": "info"})
                if clip["start"] > cursor:
                    findings.append({"type": "gap", "track": label, "start_frame": cursor, "end_frame": clip["start"], "severity": "info"})
                cursor = max(cursor, clip["end"])
            if track["clips"] and cursor < end:
                findings.append({"type": "gap", "track": label, "start_frame": cursor, "end_frame": end, "severity": "info"})
        seen = set()
        for item, info in self._timeline_items():
            media = call(item, "GetMediaPoolItem", None)
            path = call(media, "GetClipProperty", "", "File Path")
            if path and not Path(path).exists() and path not in seen:
                findings.append({"type": "missing_source", "item_id": info["id"], "path": path, "severity": "warning"})
                seen.add(path)
        expected_fps = params.get("expected_frame_rate")
        expected_width, expected_height = params.get("expected_width"), params.get("expected_height")
        width, height = self._project_resolution()
        for field, actual, expected in (("frame_rate", self._project_rate(), expected_fps), ("width", width, expected_width), ("height", height, expected_height)):
            if expected is not None and abs(float(actual) - float(expected)) > 0.01:
                findings.append({"type": "delivery_mismatch", "field": field, "actual": actual, "expected": expected, "severity": "warning"})
        findings.extend(self._free_limits(width, height, self._project_rate()))
        return {"project": project.GetName(), "timeline": self._timeline_identity(timeline), "findings": findings,
                "warning_count": sum(f["severity"] == "warning" for f in findings),
                "scope": "Active timeline. Gaps/disabled tracks can be intentional. Missing-file checks may flag image-sequence patterns. Delivery settings are compared with the expectations you supply and, on Resolve Free, with Free's UHD/60 fps delivery ceiling; this does not inspect Fairlight mute automation or queued render settings."}

    def _free_limits(self, width, height, fps):
        """Timeline settings DaVinci Resolve (Free) cannot deliver: above UHD or 60 fps."""
        product = str(call(self.resolve, "GetProductName", "") or "")
        if "studio" in product.lower():
            return []
        severity = "warning" if product else "info"
        limits = []
        try:
            if max(int(width), int(height)) > FREE_MAX_LONG_EDGE or min(int(width), int(height)) > FREE_MAX_SHORT_EDGE:
                limits.append({"type": "free_resolution_limit", "actual": "%dx%d" % (int(width), int(height)),
                               "limit": "%dx%d" % (FREE_MAX_LONG_EDGE, FREE_MAX_SHORT_EDGE), "severity": severity,
                               "note": "DaVinci Resolve (Free) renders at most UHD. Lower the timeline resolution or render with Studio."})
            if float(fps) > FREE_MAX_FPS + 0.01:
                limits.append({"type": "free_frame_rate_limit", "actual": round(float(fps), 3), "limit": FREE_MAX_FPS,
                               "severity": severity,
                               "note": "DaVinci Resolve (Free) delivers at most 60 fps. Lower the timeline frame rate or render with Studio."})
        except (TypeError, ValueError):
            return []
        if limits and not product:
            for finding in limits:
                finding["note"] += " Resolve did not report its edition, so this applies only if you use the Free version."
        return limits

    def _op_bridge_capabilities(self, _params):
        import shutil
        instance = self.resolve
        project = call(call(instance, "GetProjectManager", None), "GetCurrentProject", None)
        timeline = call(project, "GetCurrentTimeline", None)
        ffmpeg = self._ffmpeg_path(self.runtime_home)
        return {"bridge_version": AGENT_VERSION, "resolve_version": call(instance, "GetVersionString", None),
                "product": call(instance, "GetProductName", None), "transport": self.transport,
                "timeline_open": timeline is not None,
                "api_present": {method: callable(getattr(timeline, method, None)) if timeline is not None else None for method in ("DuplicateTimeline", "GetMarkers", "AddMarker", "DeleteClips", "GetUniqueId")},
                "source_frame_decoder": ffmpeg is not None,
                "source_audio_decoder": bool(ffmpeg or shutil.which("afconvert")),
                "free_workflow": "On Free 21.1+, use Start AI Bridge (Lua): authenticated native file APIs carry the shared operations without Python inside Resolve. The older Python worker remains available on Python-enabled builds. Review tools use ordinary APIs and source decoding.",
                "limitations": ["API presence is not proof a call will succeed on this build; each operation checks its result.",
                                "Composite frame export is attempted at capture time; source fallback needs ffmpeg and excludes timeline effects.",
                                "Audio analysis reads source PCM, not the processed timeline mix.",
                                "Dialogue cut application supports an isolated clip or one aligned video/audio pair; complex timelines receive markers for manual review."]}

    def _op_review_silence(self, params):
        timeline = self._timeline()
        fingerprint = self._timeline_fingerprint(timeline)
        analysis_params = dict(params, action="silence_cuts", whole_clip=True)
        analysis = self._op_timeline_audio(analysis_params)
        if self._timeline_fingerprint(timeline) != fingerprint:
            raise OperationError("Timeline changed during analysis; no markers were added. Inspect the timeline and retry.")
        existing = call(timeline, "GetMarkers", {}) or {}
        markers, skipped = [], []
        timeline_start = float(timeline.GetStartFrame())
        for cut in analysis["cuts"]:
            start, end = cut["timeline_start_frame"], cut["timeline_end_frame"]
            marker_frame = start - timeline_start
            if end <= start or marker_frame in existing or str(marker_frame) in existing:
                skipped.append(cut)
                continue
            marker_id = uuid.uuid4().hex
            data = {"bridge": "silence-review-v1", "id": marker_id, "timeline_id": call(timeline, "GetUniqueId", None),
                    "fingerprint": fingerprint, "item_id": analysis["item_id"], "start": start, "end": end}
            ok = timeline.AddMarker(marker_frame, "Yellow", "Review silence", "Source audio appears silent; review before cutting.", end - start, json.dumps(data))
            if ok:
                markers.append(dict(cut, marker_id=marker_id, marker_frame=marker_frame))
            else:
                skipped.append(cut)
        return {"item_id": analysis["item_id"], "timeline": self._timeline_identity(timeline), "markers": markers,
                "skipped": skipped, "analysis_truncated": analysis["truncated"],
                "note": "Markers only; no clips removed. Audition candidates, then pass selected marker_ids to apply_silence_cuts. Selection applies on a new duplicate timeline."}

    def _op_apply_silence_cuts(self, params):
        """Apply reviewed ranges only to a dedicated dialogue clip or aligned AV pair."""
        timeline = self._timeline()
        wanted = params.get("marker_ids") or []
        if not wanted or len(wanted) != len(set(wanted)):
            raise OperationError("Provide a nonempty list of distinct reviewed marker_ids.")
        fingerprint = self._timeline_fingerprint(timeline)
        reviews = {}
        for marker in (call(timeline, "GetMarkers", {}) or {}).values():
            try:
                data = json.loads(marker.get("customData", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and data.get("bridge") == "silence-review-v1":
                reviews[data.get("id")] = data
        selected = []
        for key in wanted:
            data = reviews.get(key)
            if not data or data.get("timeline_id") != call(timeline, "GetUniqueId", None) or data.get("fingerprint") != fingerprint:
                raise OperationError("A review marker is missing or stale. Run review_silence again after timeline edits.")
            selected.append(data)
        if len({data["item_id"] for data in selected}) != 1:
            raise OperationError("Select review markers from one dialogue clip at a time.")
        targets = self._timeline_items()
        # Whole-timeline ripple across arbitrary clips is deliberately not guessed.
        if not 1 <= len(targets) <= 2 or any(info["track_type"] not in ("audio", "video") for _, info in targets):
            raise OperationError("Automatic cleanup needs an isolated dialogue clip or one aligned video/audio pair. Use the review markers for manual cuts on this multi-clip timeline.")
        starts = {info["start"] for _, info in targets}
        ends = {info["end"] for _, info in targets}
        if len(starts) != 1 or len(ends) != 1 or (len(targets) == 2 and {info["track_type"] for _, info in targets} != {"audio", "video"}):
            raise OperationError("The video/audio pair must have identical timeline boundaries.")
        start, end = float(next(iter(starts))), float(next(iter(ends)))
        if not start.is_integer() or not end.is_integer():
            raise OperationError("Subframe clip boundaries cannot be rebuilt by this workflow.")
        start, end = int(start), int(end)
        ranges = sorted((int(data["start"]), int(data["end"])) for data in selected)
        if any(a < start or b > end or a >= b for a, b in ranges) or any(ranges[i][0] < ranges[i-1][1] for i in range(1, len(ranges))):
            raise OperationError("Selected silence ranges are invalid or overlap.")
        kept, cursor = [], start
        for begin, finish in ranges:
            if begin > cursor:
                kept.append((cursor, begin))
            cursor = finish
        if cursor < end:
            kept.append((cursor, end))
        if not kept:
            raise OperationError("These cuts would remove the entire dialogue clip. Nothing was changed.")
        originals = []
        for item, info in targets:
            if call(item, "GetFusionCompCount", 0) or call(timeline, "GetIsTrackLocked", False, info["track_type"], info["track_index"]):
                raise OperationError("Unlock tracks and use dialogue clips without Fusion compositions for automatic cleanup.")
            self._source_window(item, start)
            media = call(item, "GetMediaPoolItem", None)
            source = call(item, "GetSourceStartFrame", None)
            raw_fps = call(media, "GetClipProperty", None, "FPS")
            if media is None or source is None or (raw_fps and abs(float(raw_fps) - self._project_rate()) > 0.01):
                raise OperationError("Cannot safely rebuild this source range or mixed frame rate.")
            originals.append((item, info, media, int(source), self._read_transform(item)))
        original_identity = self._timeline_identity(timeline)
        preview, preview_identity = self._duplicate_current(params.get("name") or "Dialogue cleanup", open_duplicate=True)
        try:
            self._delete_timeline_items(preview, self._timeline_items(preview))
            batches = []
            record = start
            for begin, finish in kept:
                segment_items = []
                for original, info, media, source, transform in originals:
                    request = {"mediaPoolItem": media, "startFrame": source + begin - start,
                               "endFrame": source + finish - start, "recordFrame": record,
                               "trackIndex": info["track_index"], "mediaType": 1 if info["track_type"] == "video" else 2}
                    pool = self._media_pool()
                    if not self._is_current_timeline(self._project(), preview):
                        raise OperationError("The active timeline changed before dialogue could be placed on the preview.")
                    created = pool.AppendToTimeline([request]) or []
                    if len(created) != 1 or call(created[0], "GetStart", None) != record or call(created[0], "GetEnd", None) != record + finish - begin:
                        raise OperationError("Resolve returned an incorrect rebuilt dialogue segment.")
                    piece = created[0]
                    _, rejected = self._apply_transform(piece, transform_params_from_props(transform), verify=True)
                    if rejected:
                        raise OperationError("Could not restore the dialogue transform.")
                    if info.get("enabled") is not None and not call(piece, "SetClipEnabled", False, info["enabled"]):
                        raise OperationError("Could not restore enabled state.")
                    if info.get("color") and not call(piece, "SetClipColor", False, info["color"]):
                        raise OperationError("Could not restore clip color.")
                    if info["track_type"] == "video" and not call(original, "CopyGrades", False, [piece]):
                        raise OperationError("Could not copy the current grade layer.")
                    segment_items.append(piece)
                if len(segment_items) == 2 and not preview.SetClipsLinked(segment_items, True):
                    raise OperationError("Could not link the rebuilt video/audio segment.")
                batches.append({"start": record, "end": record + finish - begin, "item_ids": [call(piece, "GetUniqueId", None) for piece in segment_items]})
                record += finish - begin
            # Markers refer to the original timing. Clear only bridge review markers on the copy;
            # other markers remain, explicitly reported below rather than silently re-timed.
            for offset, marker in (call(preview, "GetMarkers", {}) or {}).items():
                try:
                    data = json.loads(marker.get("customData", ""))
                    if isinstance(data, dict) and data.get("bridge") == "silence-review-v1":
                        # JSON transports stringify numeric dictionary keys.
                        if not preview.DeleteMarkerAtFrame(float(offset)):
                            raise OperationError("Could not remove stale review markers on the preview.")
                except (ValueError, TypeError):
                    pass
        except Exception as exc:
            reopened = self._activate_timeline(self._project(), timeline)
            raise OperationError("Dialogue cleanup failed: %s. Original '%s' is intact%s. Partial preview '%s' retained for inspection."
                                 % (exc, original_identity["name"], " and reopened" if reopened else "; open it manually", preview_identity["name"]))
        return {"original": original_identity, "preview": preview_identity, "removed_frames": sum(b-a for a, b in ranges),
                "segments": batches, "selected_marker_ids": wanted,
                "note": "Original retained. Only selected intervals removed, with remaining segments closed up. Current grade layer and static transforms copied. Review fades, audio gain, other grade layers, clip metadata and any non-review markers before accepting the preview."}


    # ------------------------------------------------------------- dispatch

    # ------------------------------------------------------------ transcripts
    #
    # Speech-to-text runs outside Resolve (bridge/transcribe.py, whisper.cpp).
    # These operations only read clip mappings or write reviewable results back.

    def _source_entry(self, timeline, item, info, fps):
        media = call(item, "GetMediaPoolItem", None)
        file_path = call(media, "GetClipProperty", "", "File Path") if media is not None else ""
        start = float(call(item, "GetStart", 0) or 0)
        end = float(call(item, "GetEnd", start) or start)
        kind = str((call(media, "GetClipProperty", "", "Type") if media is not None else "") or "").lower()
        if "still" in kind or Path(str(file_path or "")).suffix.lower() in IMAGE_SUFFIXES:
            # A still has no source timeline: every frame of the clip shows the same image.
            source_seconds, duration = 0.0, max(0.0, (end - start) / fps)
        else:
            source_seconds, duration = self._source_window(item, start)
        return {"item_id": info.get("id"), "label": info.get("label"), "name": call(item, "GetName", ""),
                "track": "%s%d" % ("A" if info["track_type"] == "audio" else "V", info["track_index"]),
                "file_path": file_path, "source_start_sec": source_seconds, "duration_sec": duration,
                "timeline_start_frame": start, "timeline_end_frame": end, "fps": fps,
                "media_fps": self._media_fps(media)}

    @staticmethod
    def _media_fps(media):
        try:
            value = float(call(media, "GetClipProperty", None, "FPS")) if media is not None else None
        except (TypeError, ValueError):
            return None
        return value if value and math.isfinite(value) and value > 0 else None

    def _op_transcription_source(self, params):
        """Where each clip's speech lives: source file, source window and timeline origin. Read-only."""
        timeline = self._timeline()
        fps = self._project_rate()
        self._pinned_rate = fps
        try:
            return self._transcription_source(timeline, fps, params)
        finally:
            self._pinned_rate = None

    def _transcription_source(self, timeline, fps, params):
        scope = str(params.get("scope", "clip")).lower()
        if scope not in ("clip", "track", "timeline"):
            raise OperationError("scope must be clip, track or timeline.")
        track_index = params.get("track_index") if scope != "clip" else None

        def enabled(pairs):
            return [(item, info) for item, info in pairs if info.get("enabled") is not False]

        if scope == "clip":
            items = enabled(self._timeline_items(timeline, ("video", "audio")))
        else:
            # Only the tracks this scope can use: audio first, video when the timeline has no audio clips.
            items = enabled(self._timeline_items(timeline, ("audio",), track_index))
            if not items:
                items = enabled(self._timeline_items(timeline, ("video",), track_index))
        if scope == "clip":
            if params.get("item_id") and params.get("item_id") != "playhead":
                picked = [self._find_timeline_items([params["item_id"]])[0]]
            else:
                frame = self._playhead_frame(timeline)
                under = [(i, n) for i, n in items if float(n["start"]) <= frame < float(n["end"])]
                # Prefer dialogue on audio tracks, lowest track first (A1 is usually the voice).
                under.sort(key=lambda pair: (pair[1]["track_type"] != "audio", pair[1]["track_index"]))
                if not under:
                    raise OperationError("No clip under the playhead. Pass item_id, or scope='track'/'timeline'.")
                picked = [under[0]]
        else:
            audio = [(i, n) for i, n in items if n["track_type"] == "audio"]
            pool = audio or items
            if scope == "track" or params.get("track_index"):
                kind = "audio" if audio else "video"
                track = int(params.get("track_index") or 1)
                pool = [(i, n) for i, n in pool if n["track_type"] == kind and n["track_index"] == track]
            elif audio:  # Whole timeline: the audio track holding the most material.
                totals = {}
                for _, n in audio:
                    totals[n["track_index"]] = totals.get(n["track_index"], 0) + float(n["end"]) - float(n["start"])
                best = max(totals, key=totals.get)
                pool = [(i, n) for i, n in audio if n["track_index"] == best]
            picked = sorted(pool, key=lambda pair: float(pair[1]["start"]))
            if not picked:
                raise OperationError("No enabled clips with audio on the selected track.")
        sources, skipped = [], []
        for item, info in picked[:200]:
            try:
                entry = self._source_entry(timeline, item, info, fps)
            except OperationError as exc:
                skipped.append({"item_id": info.get("id"), "name": call(item, "GetName", ""), "reason": str(exc)})
                continue
            if not entry["file_path"] or not Path(entry["file_path"]).is_file():
                skipped.append({"item_id": entry["item_id"], "name": entry["name"], "reason": "Media is offline or has no file."})
                continue
            sources.append(entry)
        fingerprint = self._timeline_fingerprint(timeline) if params.get("fingerprint", True) else None
        return {"timeline": self._timeline_identity(timeline), "timeline_start_frame": float(timeline.GetStartFrame()),
                "fps": fps, "fingerprint": fingerprint, "sources": sources, "skipped": skipped}

    def _op_review_transcript_cuts(self, params):
        """Mark transcript-chosen ranges for review; apply_silence_cuts applies them on a duplicate."""
        timeline = self._timeline()
        item, info = self._resolve_one_item(params.get("item_id"))
        ranges = params.get("ranges") or []
        if not isinstance(ranges, list) or not ranges:
            raise OperationError("ranges must list {start_frame, end_frame, label} in absolute timeline frames.")
        start, end = float(call(item, "GetStart", 0) or 0), float(call(item, "GetEnd", 0) or 0)
        expected = params.get("fingerprint")
        fingerprint = self._timeline_fingerprint(timeline)
        if expected and expected != fingerprint:
            raise OperationError("The timeline changed since it was transcribed; transcribe again before marking cuts.")
        existing = call(timeline, "GetMarkers", {}) or {}
        timeline_start = float(timeline.GetStartFrame())
        markers, skipped = [], []
        for entry in ranges[:500]:
            try:
                a, b = int(entry["start_frame"]), int(entry["end_frame"])
            except (KeyError, TypeError, ValueError):
                raise OperationError("Each range needs integer start_frame and end_frame.")
            a, b = max(a, int(start)), min(b, int(end))
            label = str(entry.get("label") or "transcript cut")[:80]
            marker_frame = a - timeline_start
            if b <= a or marker_frame in existing or str(marker_frame) in existing:
                skipped.append(dict(entry, reason="Outside the clip, empty, or a marker already sits there."))
                continue
            marker_id = uuid.uuid4().hex
            data = {"bridge": "silence-review-v1", "kind": "transcript", "id": marker_id,
                    "timeline_id": call(timeline, "GetUniqueId", None), "fingerprint": fingerprint,
                    "item_id": info["id"], "start": a, "end": b}
            if timeline.AddMarker(marker_frame, "Pink", "Review cut: %s" % label, "From the transcript. Audition before cutting.",
                                  b - a, json.dumps(data)):
                markers.append({"marker_id": marker_id, "start_frame": a, "end_frame": b, "label": label})
                existing[marker_frame] = True
            else:
                skipped.append(dict(entry, reason="Resolve refused the marker."))
        return {"item_id": info["id"], "markers": markers, "skipped": skipped,
                "note": "Markers only; nothing was cut. Pass the chosen marker_ids to apply_silence_cuts, which "
                        "cuts on a new duplicate timeline and keeps the original."}

    def _op_import_subtitles(self, params):
        """Import an .srt and place it on a subtitle track aligned to the timeline start.

        ``first_cue_frame`` (absolute) lets the operation verify alignment; misplaced items are removed again.
        """
        path = _absolute(params.get("path", ""))
        if not path.lower().endswith(".srt") or not Path(path).is_file():
            raise OperationError("path must be an existing .srt file.")
        timeline = self._timeline()
        pool = self._media_pool()
        tl_start = int(float(timeline.GetStartFrame()))
        expected = params.get("first_cue_frame")

        def subtitle_items():
            found = []
            for t in range(1, int(call(timeline, "GetTrackCount", 0, "subtitle") or 0) + 1):
                found += list(self._track_clips(timeline, "subtitle", t))
            return found

        before = {call(i, "GetUniqueId", None) for i in subtitle_items()}
        imported = call(pool, "ImportMedia", None, [path]) or []
        if not imported:
            raise OperationError("Resolve did not import the subtitle file. Import it with File > Import > Subtitle.")
        item = imported[0]
        attempts, placed_ok, added = [], False, []
        for label, request in (("clipInfo at timeline start", [{"mediaPoolItem": item, "recordFrame": tl_start}]),
                               ("clipInfo on subtitle track 1", [{"mediaPoolItem": item, "recordFrame": tl_start, "trackIndex": 1}])):
            call(pool, "AppendToTimeline", None, request)
            attempts.append(label)
            added = [i for i in subtitle_items() if call(i, "GetUniqueId", None) not in before]
            if not added:
                continue
            first = min(int(float(call(i, "GetStart", 0) or 0)) for i in added)
            if expected is None or abs(first - int(expected)) <= 1:
                placed_ok = True
                break
            # Landed in the wrong place (e.g. appended after the last clip): take it back off.
            call(timeline, "DeleteClips", None, added, False)
            attempts[-1] += " (landed at %d, expected %s; removed)" % (first, expected)
            added = []
        return {"media_pool_item": call(item, "GetName", ""), "on_timeline": placed_ok,
                "subtitle_items_added": len(added) if placed_ok else 0, "attempts": attempts,
                "note": None if placed_ok else (
                    "The subtitle file is in the media pool, but Resolve did not place it at the right time via scripting. "
                    "Drag it from the media pool onto the start of the timeline (it lands on a subtitle track).")}

    # --------------------------------------------------------- visual sources

    def _op_media_sources(self, params):
        """Video sources for contact sheets and shot detection. Read-only.

        scope: "clip" (item_id or the top video clip under the playhead), "track", "timeline"
        (every video clip, top track first), or "media_pool" (pool clips, optional bin filter).
        """
        scope = str(params.get("scope", "timeline")).lower()
        limit = max(1, min(int(params.get("limit", 120) or 120), 400))
        fps = self._project_rate()
        if scope == "media_pool":
            wanted_bin = str(params.get("bin") or "").strip().lower()
            sources, skipped = [], []
            for clip in self._walk_media(self._media_pool().GetRootFolder(), "", 2000):
                if wanted_bin and wanted_bin not in str(clip.get("bin") or "").lower():
                    continue
                kind = str(clip.get("type") or "").lower()
                if "audio" in kind and "video" not in kind:
                    continue
                path = clip.get("file_path")
                if not path or not Path(path).is_file():
                    skipped.append({"name": clip.get("name"), "reason": "offline or no file"})
                    continue
                try:
                    frames = float(clip.get("frames") or 0)
                    clip_fps = fps
                    duration = frames / clip_fps if frames > 1 else 0.0
                except (TypeError, ValueError):
                    duration = 0.0
                sources.append({"item_id": clip.get("id"), "label": clip.get("bin") or "Master", "name": clip.get("name"),
                                "track": "Pool", "file_path": path, "source_start_sec": 0.0, "duration_sec": duration,
                                "timeline_start_frame": None, "timeline_end_frame": None, "fps": fps, "media_pool": True})
                if len(sources) >= limit:
                    break
            return {"scope": scope, "fps": fps, "sources": sources, "skipped": skipped}
        timeline = self._timeline()
        items = [(item, info) for item, info in self._timeline_items()
                 if info["track_type"] == "video" and info.get("enabled") is not False]
        if scope == "clip":
            if params.get("item_id") and params.get("item_id") != "playhead":
                picked = [self._find_timeline_items([params["item_id"]])[0]]
            else:
                frame = self._playhead_frame(timeline)
                picked = sorted([(i, n) for i, n in items if float(n["start"]) <= frame < float(n["end"])],
                                key=lambda pair: -pair[1]["track_index"])[:1]
        elif scope == "track":
            track = int(params.get("track_index") or 1)
            picked = [(i, n) for i, n in items if n["track_index"] == track]
        elif scope == "timeline":
            picked = items
        else:
            raise OperationError("scope must be clip, track, timeline or media_pool.")
        picked = sorted(picked, key=lambda pair: (-pair[1]["track_index"], float(pair[1]["start"])))
        if not picked:
            raise OperationError("No enabled video clips in that scope.")
        sources, skipped = [], []
        for item, info in picked[:limit]:
            try:
                entry = self._source_entry(timeline, item, info, fps)
            except OperationError as exc:
                skipped.append({"item_id": info.get("id"), "name": call(item, "GetName", ""), "reason": str(exc)})
                continue
            if not entry["file_path"] or not Path(entry["file_path"]).is_file():
                skipped.append({"item_id": entry["item_id"], "name": entry["name"], "reason": "offline or generated (no file)"})
                continue
            sources.append(entry)
        return {"scope": scope, "fps": fps, "timeline": self._timeline_identity(timeline),
                "sources": sources, "skipped": skipped, "truncated": len(picked) > limit}

    # ------------------------------------------------------- timeline notes

    def _op_timeline_notes(self, params):
        """User-made markers (notes) with status, timecode and the clips under each start frame. Read-only."""
        from bridge import notes as notes_logic
        timeline = self._timeline()
        start = float(timeline.GetStartFrame())
        drop = notes_logic.is_drop_frame(call(timeline, "GetSetting", "", "timelineDropFrameTimecode"))
        found = notes_logic.collect(call(timeline, "GetMarkers", {}) or {}, start, self._project_rate(), drop)
        result = notes_logic.summary(found, bool(params.get("include_done", False)))
        if result["notes"]:
            for kind, letter in (("video", "V"), ("audio", "A")):
                for track in range(1, int(call(timeline, "GetTrackCount", 0, kind) or 0) + 1):
                    items = self._track_clips(timeline, kind, track)
                    items = list(items.values()) if isinstance(items, dict) else list(items)
                    spans = {}

                    def bounds(index, items=items, spans=spans):
                        if index not in spans:
                            first, last = call(items[index], "GetStart", None), call(items[index], "GetEnd", None)
                            spans[index] = None if first is None or last is None else (float(first), float(last))
                        return spans[index]

                    for note in result["notes"]:
                        index = notes_logic.covering(note["frame"], len(items), bounds)
                        if index is not None:
                            item = items[index]
                            note["clips"].append({"name": call(item, "GetName", ""), "track": "%s%d" % (letter, track),
                                                  "item_id": str(call(item, "GetUniqueId", "") or "")})
        result["timeline"] = self._timeline_identity(timeline)
        return result

    def _op_resolve_timeline_note(self, params):
        """Mark one note done/skipped (or open again): status in customData, new color, reply in the note."""
        from bridge import notes as notes_logic
        status = str(params.get("status") or "done").strip().lower()
        if status not in notes_logic.STATUSES:
            raise OperationError("status must be done, skipped or open.")
        try:
            frame = float(params.get("frame"))
        except (TypeError, ValueError):
            raise OperationError("frame must be the absolute timeline frame that timeline_notes returned.")
        timeline = self._timeline()
        start = float(timeline.GetStartFrame())

        def marker_at(markers):
            for key, marker in (markers or {}).items():
                try:
                    if abs(start + float(key) - frame) < 0.5:
                        return float(key), marker
                except (TypeError, ValueError):
                    continue
            return None, None

        offset, original = marker_at(call(timeline, "GetMarkers", {}) or {})
        if original is None:
            raise OperationError("No marker at frame %s. Call timeline_notes for the current note frames." % frame)
        original = dict(original)
        if not notes_logic.is_note(original):
            raise OperationError("The marker at frame %s was made by the bridge or has no name or note, so it is "
                                 "not a timeline note; it was left unchanged." % frame)
        wanted = notes_logic.resolved_marker(original, status, params.get("reply", ""))
        drop = notes_logic.is_drop_frame(call(timeline, "GetSetting", "", "timelineDropFrameTimecode"))
        result = {"frame": notes_logic.absolute_frame(offset, start), "status": status,
                  "timecode": notes_logic.timecode(start + offset, self._project_rate(), drop),
                  "name": wanted["name"], "reply": notes_logic.note_state({"customData": wanted["customData"]})["reply"]}

        def read_back():
            return marker_at(call(timeline, "GetMarkers", {}) or {})[1]

        def finish(method, actual, expected):
            result.update(method=method, verified=notes_logic.same_marker(expected, actual),
                          color=str((actual or {}).get("color") or ""), note=str((actual or {}).get("note") or ""))
            if not result["verified"]:
                result["warning"] = ("Resolve reports a different marker than was written; check it on the timeline: %s"
                                     % json.dumps(plain(actual), ensure_ascii=False))
            result["open_remaining"] = notes_logic.count_open(call(timeline, "GetMarkers", {}) or {})
            return result

        # Only customData changes (e.g. the marker is already the target color): edit it in place.
        if all(str(wanted[k]) == str(original.get(k) or "") for k in ("color", "name", "note")) and \
                wanted["duration"] == max(1, int(original.get("duration") or 1)) and \
                call(timeline, "UpdateMarkerCustomData", False, offset, wanted["customData"]):
            actual = read_back()
            if notes_logic.same_marker(wanted, actual):
                return finish("custom_data", actual, wanted)
        if not call(timeline, "DeleteMarkerAtFrame", False, offset) and read_back() is not None:
            raise OperationError("Resolve would not replace the marker at frame %s; it is unchanged." % frame)
        colors = [wanted["color"]] + ([notes_logic.FALLBACK_COLORS[wanted["color"]]]
                                      if wanted["color"] in notes_logic.FALLBACK_COLORS else [])
        for color in colors:
            attempt = dict(wanted, color=color)
            if call(timeline, "AddMarker", False, offset, color, attempt["name"], attempt["note"],
                    attempt["duration"], attempt["customData"]):
                actual = read_back()
                if actual is not None:
                    return finish("re-added", actual, attempt)
        # The re-add failed: put the user's marker back exactly as it was.
        restored = call(timeline, "AddMarker", False, offset, original.get("color") or "Blue", original.get("name") or "",
                        original.get("note") or "", max(1, int(original.get("duration") or 1)),
                        original.get("customData") or "") and notes_logic.same_marker(original, read_back())
        if restored:
            raise OperationError("Resolve would not re-add the marker at frame %s as %s. The original marker was "
                                 "restored unchanged." % (frame, status))
        raise OperationError("Resolve would not re-add the marker at frame %s and could not restore it. Re-create it "
                             "by hand: %s" % (frame, json.dumps(plain(original), ensure_ascii=False)))

    def _op_add_markers(self, params):
        """Add many markers in one call: [{frame (absolute timeline), color, name, note, duration}]."""
        timeline = self._timeline()
        markers = params.get("markers") or []
        if not isinstance(markers, list) or not markers:
            raise OperationError("markers must be a non-empty list.")
        colors = {"Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose", "Lavender",
                  "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream"}
        start = float(timeline.GetStartFrame())
        end = float(call(timeline, "GetEndFrame", start) or start)
        existing = {float(k) for k in (call(timeline, "GetMarkers", {}) or {})}
        added, skipped = [], []
        for m in markers[:2000]:
            try:
                frame = float(m["frame"])
            except (KeyError, TypeError, ValueError):
                skipped.append({"marker": m, "reason": "needs an absolute timeline frame"})
                continue
            offset = frame - start
            color = str(m.get("color") or "Blue").capitalize()
            if color not in colors:
                color = "Blue"
            if not start <= frame <= end or offset in existing:
                skipped.append({"frame": frame, "reason": "outside the timeline or a marker is already there"})
                continue
            ok = timeline.AddMarker(offset, color, str(m.get("name") or "")[:120], str(m.get("note") or "")[:500],
                                    max(1, int(m.get("duration") or 1)), _notes_tag(m.get("custom_data")))
            if ok:
                added.append({"frame": frame, "name": m.get("name"), "color": color})
                existing.add(offset)
            else:
                skipped.append({"frame": frame, "reason": "Resolve refused the marker"})
        return {"added": len(added), "markers": added[:200], "skipped": skipped[:50]}

    # ------------------------------------------------------- remove time ranges

    # ----------------------------------------------- trim, slip, slide and roll

    def _trim_layout(self, timeline):
        """Every item's position and source mapping, for bridge/trim.py. Read-only."""
        from bridge import trim as trim_plan
        fps = self._project_rate()
        locked, clips, objects = {}, [], {}
        for item, info in self._timeline_items(timeline):
            track = (info["track_type"], info["track_index"])
            if track not in locked:
                locked[track] = call(timeline, "GetIsTrackLocked", False, *track) is True
            start, end = float(info["start"] or 0), float(info["end"] or 0)
            entry = {"key": str(info["id"]), "label": info["label"], "name": info.get("name"),
                     "track_type": info["track_type"], "track_index": info["track_index"],
                     "start": int(start), "end": int(end), "source": None, "ratio": 1.0, "media_frames": None,
                     "media_id": info.get("media_pool_id"), "linked": [], "rebuildable": False, "reason": None,
                     "locked": locked[track], "enabled": info.get("enabled"), "color": info.get("color")}
            objects[entry["key"]] = item
            clips.append(entry)
            if info["track_type"] == "subtitle":
                entry["reason"] = "subtitle items cannot be rebuilt through the API"
                continue
            if not start.is_integer() or not end.is_integer():
                entry["reason"] = "subframe clip boundaries cannot be rebuilt"
                continue
            media = call(item, "GetMediaPoolItem", None)
            source = call(item, "GetSourceStartFrame", None)
            if source is None:
                source = call(item, "GetLeftOffset", None)
            if media is None or source is None:
                entry["reason"] = "it has no media pool source (generator, title or compound clip)"
                continue
            entry["source"] = int(round(float(source)))
            raw_fps = call(media, "GetClipProperty", None, "FPS")
            try:
                source_fps = float(raw_fps) if raw_fps else fps
                if not math.isfinite(source_fps) or source_fps <= 0:
                    source_fps = fps
            except (TypeError, ValueError):
                source_fps = fps
            still = "still" in str(call(media, "GetClipProperty", "", "Type") or "").lower()
            # Mixed rates play in real time, so map them by time; near-equal rates (29.97 vs 30/1.001) are 1:1.
            entry["ratio"] = ratio = 1.0 if still or abs(source_fps - fps) <= 0.01 else source_fps / fps
            if not still:
                source_end = call(item, "GetSourceEndFrame", None)
                if source_end is not None:  # Same test as _source_window: refuse real retiming.
                    seconds = (float(source_end) - float(source) + 1) / source_fps
                    if abs(seconds - (end - start) / fps) > max(2 / source_fps, 2 / fps):
                        entry["reason"] = "it is retimed (speed change), which a rebuild cannot keep"
                        continue
                try:
                    entry["media_frames"] = int(float(call(media, "GetClipProperty", None, "Frames")))
                except (TypeError, ValueError):
                    right = call(item, "GetRightOffset", None)  # Tail handle in timeline frames.
                    used = entry["source"] + trim_plan.source_length(entry["end"] - entry["start"], ratio)
                    try:
                        entry["media_frames"] = used + max(0, int(math.floor(float(right) * ratio + 1e-9)))
                    except (TypeError, ValueError):
                        entry["media_frames"] = used  # Unknown media length: no tail handle is assumed.
            linked = call(item, "GetLinkedItems", None) or []
            linked = list(linked.values()) if isinstance(linked, dict) else list(linked)
            entry["linked"] = [str(uid) for uid in (call(other, "GetUniqueId", None) for other in linked) if uid]
            entry["rebuildable"] = True
        return clips, objects, fps

    def _trim_read_tracks(self, timeline):
        """Read back {"V1": [[start, end, source_in], ...]} for verification."""
        tracks = {}
        for item, info in self._timeline_items(timeline):
            if info["track_type"] == "subtitle":
                continue
            source = call(item, "GetSourceStartFrame", None)
            if source is None:
                source = call(item, "GetLeftOffset", None)
            try:
                source = int(round(float(source)))
            except (TypeError, ValueError):
                source = None
            name = "%s%d" % ("V" if info["track_type"] == "video" else "A", info["track_index"])
            tracks.setdefault(name, []).append([int(float(info["start"] or 0)), int(float(info["end"] or 0)), source])
        return {name: sorted(entries) for name, entries in sorted(tracks.items())}

    # Read back per clip and written onto rebuilt pieces by _carry_extras (transform keys go through
    # _apply_transform; these are anything else GetProperty reports, plus keys GetProperty() may omit).
    CARRY_EXTRA_PROPERTIES = ("AudioVolume",)
    CARRY_NEVER = frozenset({"Start", "End", "Duration", "LeftOffset", "RightOffset", "SourceStart", "SourceEnd"})
    NOT_CARRIED_ALWAYS = ("transitions (the scripting API cannot read or add them)",
                          "audio and video fades and keyframed audio levels (not readable through the API)",
                          "retimed clips (refused before the rebuild)")

    def _carry_transform(self, piece, transform, label, warnings):
        """Re-apply only the transform keys whose value differs from the piece's current one; warn only on those."""
        current = self._read_transform(piece) or {}
        wanted = {key: value for key, value in (transform or {}).items()
                  if not (key in current and self._same_value(current.get(key), value))}
        if not wanted:
            return []
        _, rejected = self._apply_transform(piece, transform_params_from_props(wanted), verify=False)
        names = sorted(map(str, rejected if isinstance(rejected, (list, tuple, set)) else list(rejected or [])))
        if names:
            warnings.append("Transform settings of %s not restored: %s." % (label, ", ".join(names)[:120]))
        return names

    def _carry_snapshot(self, item):
        """Everything per clip a rebuild can copy, read from the untouched original."""
        props = plain(call(item, "GetProperty", {}) or {})
        props = props if isinstance(props, dict) else {}
        for key in self.CARRY_EXTRA_PROPERTIES:
            if key not in props:
                value = plain(call(item, "GetProperty", None, key))
                if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                    props[key] = value
        markers = plain(call(item, "GetMarkers", {}) or {})
        source = call(item, "GetSourceStartFrame", None)
        if source is None:
            source = call(item, "GetLeftOffset", None)
        return {"properties": {k: v for k, v in props.items() if k not in READABLE_TRANSFORM_KEYS
                               and k not in self.CARRY_NEVER and isinstance(v, (int, float, str)) and not isinstance(v, bool)},
                "markers": markers if isinstance(markers, dict) else {}, "source": source}

    @staticmethod
    def _same_value(a, b):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-6)
        except (TypeError, ValueError):
            return str(a) == str(b)

    def _carry_extras(self, snapshot, piece, label, piece_source, piece_frames, stats):
        """Copy the snapshot's extra properties and (when safe) clip markers onto one rebuilt piece.

        Every property is read back. Clip markers are copied only onto a piece that starts at the original's
        source frame: whether TimelineItem marker frames count from the item or the source is unverified, and
        the two conventions agree only there.
        """
        current = plain(call(piece, "GetProperty", {}) or {})
        current = current if isinstance(current, dict) else {}
        for key, value in snapshot["properties"].items():
            have = current.get(key, call(piece, "GetProperty", None, key))
            if have is not None and self._same_value(have, value):
                continue
            returned = call(piece, "SetProperty", False, key, value)
            back = call(piece, "GetProperty", None, key)
            if returned and back is not None and self._same_value(back, value):
                stats["properties"].add(key)
            else:
                stats["properties_failed"].add(key)
                stats["warnings"].append("%s: property %s=%r was not carried (Resolve returned %r, reads back %r)."
                                         % (label, key, value, returned, back))
        markers = snapshot["markers"]
        if not markers:
            return
        try:
            same_head = piece_source is not None and snapshot["source"] is not None and \
                int(round(float(piece_source))) == int(round(float(snapshot["source"])))
        except (TypeError, ValueError):
            same_head = False
        if not same_head:
            # Count only markers this piece would hold if frames count from the item start (the usual reading).
            try:
                lo, hi = float(piece_source), float(piece_source) + piece_frames
                origin = float(snapshot["source"])
                stats["markers_skipped"] += sum(1 for offset in markers if lo <= origin + float(offset) < hi)
            except (TypeError, ValueError):
                stats["markers_skipped"] += len(markers)
            return
        for offset, marker in markers.items():
            try:
                frame = float(offset)
            except (TypeError, ValueError):
                stats["markers_skipped"] += 1
                continue
            if not isinstance(marker, dict) or frame >= piece_frames:
                stats["markers_skipped"] += 1
                continue
            if call(piece, "AddMarker", False, frame, marker.get("color", "Blue"), marker.get("name", ""),
                    marker.get("note", ""), marker.get("duration", 1), marker.get("customData", "")):
                stats["markers"] += 1
            else:
                stats["markers_skipped"] += 1

    @staticmethod
    def _carry_stats():
        return {"properties": set(), "properties_failed": set(), "markers": 0, "markers_skipped": 0,
                "links": 0, "links_failed": 0, "comps": 0, "conversions": [], "warnings": []}

    def _import_comp(self, piece, comp_file, declared, label, stats):
        """ImportFusionComp, then check Resolve kept file Loaders as Loaders. False if the import failed."""
        imported = call(piece, "ImportFusionComp", None, str(comp_file))
        if not imported:
            return False
        stats["comps"] += 1
        if any(t["reg_id"] == "Loader" and not t["media_in"] for t in declared):
            comp = imported if not isinstance(imported, bool) else call(
                piece, "GetFusionCompByIndex", None, int(call(piece, "GetFusionCompCount", 1) or 1))
            problems = fusion_conversions(declared, comp) if comp is not None else []
            if problems:
                stats["conversions"].extend(dict(p, clip=label) for p in problems)
                stats["warnings"].append(_conversion_warning(problems, "Fusion comp re-import on %s" % label))
        return True

    def _carry_report(self, stats, base):
        carried = list(base)
        if stats["comps"]:
            carried.append("Fusion comps (%d re-imported)" % stats["comps"])
        if stats["properties"]:
            carried.append("clip properties read back: %s" % ", ".join(sorted(stats["properties"])))
        if stats["markers"]:
            carried.append("%d clip marker(s)" % stats["markers"])
        if stats["links"]:
            carried.append("%d audio/video link group(s)" % stats["links"])
        not_carried = list(self.NOT_CARRIED_ALWAYS)
        if stats["properties_failed"]:
            not_carried.append("clip properties Resolve would not set: %s" % ", ".join(sorted(stats["properties_failed"])))
        if stats["markers_skipped"]:
            not_carried.append("%d clip marker(s) on pieces whose source in moved or outside the piece (clip-marker "
                               "frame convention is unverified; the original keeps them)" % stats["markers_skipped"])
        if stats["links_failed"]:
            not_carried.append("%d audio/video link group(s) Resolve would not relink" % stats["links_failed"])
        if stats["conversions"]:
            not_carried.append("file Loaders converted by Resolve on Fusion comp re-import: %s"
                               % ", ".join("%s on %s" % (p["tool"], p["clip"]) for p in stats["conversions"][:20]))
        return {"carried": carried, "not_carried": not_carried}

    def _trim_run(self, params, plan, suffix):
        """Plan one edit, then rebuild only the clips it changes on a verified duplicate."""
        import shutil
        import tempfile
        from bridge import trim as trim_plan
        timeline = self._timeline()
        tl_start = int(float(timeline.GetStartFrame()))
        clips, objects, fps = self._trim_layout(timeline)
        try:
            result = plan(trim_plan, clips, tl_start)
        except trim_plan.PlanError as exc:
            message = str(exc)
            raise OperationError(message if "Nothing was changed" in message else message + " Nothing was changed.")
        original_identity = self._timeline_identity(timeline)
        changed = result["changed"]
        report = {"original": original_identity, "edit": result["edit"], "fps": fps,
                  "changed": [{k: v for k, v in entry.items() if k not in ("track_type", "track_index")} for entry in changed[:100]],
                  "changed_count": len(changed), "notes": result["notes"][:30]}
        if params.get("dry_run"):
            report.update(dry_run=True, note="Plan only; nothing was changed. Run again without dry_run to build it "
                                             "on a new timeline (the original is kept).")
            return report
        by_key = {clip["key"]: clip for clip in clips}
        transforms = {entry["key"]: self._read_transform(objects[entry["key"]])
                      for entry in changed if entry["track_type"] == "video"}
        snapshots = {entry["key"]: self._carry_snapshot(objects[entry["key"]]) for entry in changed}
        stats = self._carry_stats()
        name = params.get("name") or "%s (%s)" % (original_identity.get("name") or "Timeline", suffix)
        preview, preview_identity = self._duplicate_current(name, open_duplicate=True)
        comps_dir = self._work_dir("comps")  # Resolve must be able to write it (App Store sandbox).
        warnings, rebuilt, markers_moved, markers_lost = [], {}, 0, 0
        try:
            copies = {info["label"]: (item, info) for item, info in self._timeline_items(preview)}
            targets = []
            for entry in changed:
                pair = copies.get(entry["label"])
                if pair is None or (int(float(pair[1]["start"] or 0)), int(float(pair[1]["end"] or 0))) != (
                        entry["before"]["start"], entry["before"]["end"]):
                    raise OperationError("the copy's %s does not match the original" % entry["label"])
                targets.append(pair)
            comp_files = {}
            for entry in changed:  # Export Fusion comps from the untouched original first.
                original, files = objects[entry["key"]], []
                for index in range(1, int(call(original, "GetFusionCompCount", 0) or 0) + 1):
                    target = comps_dir / ("%s_%d.comp" % (uuid.uuid4().hex, index))
                    if call(original, "ExportFusionComp", False, str(target), index) and target.exists():
                        files.append((target, setting_tools(target.read_text(encoding="utf-8", errors="replace"))))
                    else:
                        warnings.append("Could not export the Fusion comp of %s; its rebuilt clip lacks it." % entry["label"])
                comp_files[entry["key"]] = files
            self._delete_timeline_items(preview, targets)
            pool = self._media_pool()
            for entry in sorted(changed, key=lambda e: e["after"]["start"]):
                clip, original, after = by_key[entry["key"]], objects[entry["key"]], entry["after"]
                request = {"mediaPoolItem": call(original, "GetMediaPoolItem", None), "startFrame": after["source_in"],
                           "endFrame": after["source_in"] + trim_plan.source_length(after["duration"], clip["ratio"]),
                           "recordFrame": after["start"], "trackIndex": clip["track_index"],
                           "mediaType": 1 if clip["track_type"] == "video" else 2}
                if not self._is_current_timeline(self._project(), preview):
                    raise OperationError("the active timeline changed while rebuilding")
                created = pool.AppendToTimeline([request]) or []
                if len(created) != 1:
                    raise OperationError("Resolve did not place the rebuilt %s" % entry["label"])
                piece = rebuilt[entry["key"]] = created[0]
                if entry["key"] in transforms:
                    self._carry_transform(piece, transforms[entry["key"]], entry["label"], warnings)
                    if not call(original, "CopyGrades", False, [piece]):
                        warnings.append("The grade of %s was not copied." % entry["label"])
                if clip.get("enabled") is False:
                    call(piece, "SetClipEnabled", None, False)
                if clip.get("color"):
                    call(piece, "SetClipColor", None, clip["color"])
                for comp_file, declared in comp_files[entry["key"]]:
                    if not self._import_comp(piece, comp_file, declared, entry["label"], stats):
                        warnings.append("Could not re-import the Fusion comp on %s." % entry["label"])
                self._carry_extras(snapshots[entry["key"]], piece, entry["label"], after["source_in"],
                                   after["duration"], stats)
            linked_groups = set()
            for key in rebuilt:  # Relink audio and video that Resolve reported as linked.
                group = tuple(sorted({key} | {k for k in by_key[key]["linked"] if k in rebuilt}))
                if len(group) > 1:
                    linked_groups.add(group)
            for group in linked_groups:
                if not call(preview, "SetClipsLinked", False, [rebuilt[k] for k in group], True):
                    stats["links_failed"] += 1
                    warnings.append("Could not relink %s." % ", ".join(by_key[k]["label"] for k in group))
                else:
                    stats["links"] += 1
            if result["marker_shift"]:  # Ripple: markers after the edit move with the material.
                threshold, delta = result["marker_shift"]
                moving = [(float(offset), marker) for offset, marker in (call(preview, "GetMarkers", {}) or {}).items()
                          if tl_start + float(offset) >= threshold]
                moving = [(offset, marker) for offset, marker in moving if preview.DeleteMarkerAtFrame(offset)]
                for offset, marker in moving:
                    if call(preview, "AddMarker", False, offset + delta, marker.get("color", "Blue"), marker.get("name", ""),
                            marker.get("note", ""), marker.get("duration", 1), marker.get("customData", "")):
                        markers_moved += 1
                    else:
                        markers_lost += 1
                if markers_lost:
                    warnings.append("%d markers could not be re-added at their rippled position on the new timeline "
                                    "(another marker sits there); the original still has them." % markers_lost)
        except Exception as exc:
            reopened = self._activate_timeline(self._project(), timeline)
            raise OperationError("Rebuild failed: %s. Original '%s' is intact%s. Partial copy '%s' kept for inspection."
                                 % (exc, original_identity["name"], " and reopened" if reopened else "", preview_identity["name"]))
        finally:
            shutil.rmtree(comps_dir, ignore_errors=True)
        actual = self._trim_read_tracks(preview)
        mismatches = trim_plan.compare_tracks(result["expected_tracks"], actual)
        clip_checks = []
        for entry in changed[:100]:
            piece, after = rebuilt[entry["key"]], entry["after"]
            source = call(piece, "GetSourceStartFrame", None)
            got = [call(piece, "GetStart", None), call(piece, "GetEnd", None), source]
            want = [after["start"], after["end"], after["source_in"]]
            try:
                ok = [int(float(v)) for v in got] == want
            except (TypeError, ValueError):
                ok = False
            clip_checks.append({"label": entry["label"], "track": entry["track"], "expected": want, "actual": got, "ok": ok})
            if not ok:
                mismatches.append("%s on %s: expected %s-%s (source in %s), Resolve reports %s-%s (source in %s)."
                                  % tuple([entry["label"], entry["track"]] + want + got))
        report.update(result_timeline=preview_identity, verified=not mismatches,
                      verification={"mismatches": sorted(set(mismatches))[:50], "clips": clip_checks},
                      markers_moved=markers_moved, warnings=sorted(set(warnings + stats["warnings"]))[:30],
                      **self._carry_report(stats, ("static transform, crop, composite mode and opacity (video)",
                                                   "current grade layer via CopyGrades (video)", "enabled state",
                                                   "clip color")))
        report.update(note="Original timeline untouched; the edited copy is now open. Only the %d clips that changed were "
                           "rebuilt; see carried / not_carried for what was copied onto them (transitions, fades and "
                           "keyframed audio levels on those clips are not carried over). "
                           "Everything else is Resolve's own duplicate.%s"
                           % (len(changed), "" if not mismatches else " VERIFICATION FAILED: Resolve did not place "
                              "everything where planned; see verification.mismatches before using this timeline."))
        return report

    @staticmethod
    def _trim_frames(params, key="frames"):
        value = params.get(key)
        if isinstance(value, bool) or value is None:
            raise OperationError("%s must be a whole number of timeline frames (positive = later)." % key)
        return value

    def _op_trim_edit(self, params):
        """Move one clip's head or tail by N timeline frames, rippling or not, on a new duplicate."""
        ripple = params.get("ripple", True)
        if isinstance(ripple, str):
            ripple = ripple.strip().lower() not in ("false", "0", "no", "off")
        frames = self._trim_frames(params)
        return self._trim_run(params, lambda t, clips, start: t.plan_trim(
            clips, params.get("item_id"), params.get("edge", "tail"), frames, bool(ripple), start), "trimmed")

    def _op_slip_clip(self, params):
        """Shift a clip's source in and out by N frames; position and length stay. On a new duplicate."""
        frames = self._trim_frames(params)
        return self._trim_run(params, lambda t, clips, start: t.plan_slip(clips, params.get("item_id"), frames, start), "slipped")

    def _op_slide_clip(self, params):
        """Move a clip along its track; touching neighbours absorb the change. On a new duplicate."""
        frames = self._trim_frames(params)
        return self._trim_run(params, lambda t, clips, start: t.plan_slide(clips, params.get("item_id"), frames, start), "slid")

    def _op_roll_edit(self, params):
        """Move the cut between two touching clips by N frames. On a new duplicate."""
        frames = self._trim_frames(params)
        return self._trim_run(params, lambda t, clips, start: t.plan_roll(
            clips, params.get("left_item_id"), params.get("right_item_id"), frames, start), "rolled")

    def _op_remove_ranges(self, params):
        """Remove time ranges from the WHOLE timeline (every video/audio track) on a new duplicate.

        The rebuilt copy closes the gaps; the original timeline is untouched. Copied per piece:
        static transforms, enabled state, clip color, the current grade layer and Fusion comps.
        """
        import tempfile
        timeline = self._timeline()
        fps = self._project_rate()
        raw = params.get("ranges") or []
        if not isinstance(raw, list) or not raw:
            raise OperationError("ranges must list {start_frame, end_frame} in absolute timeline frames.")
        tl_start = int(float(timeline.GetStartFrame()))
        tl_end = int(float(call(timeline, "GetEndFrame", tl_start) or tl_start))
        ranges = []
        for r in raw[:5000]:
            try:
                a, b = int(r["start_frame"]), int(r["end_frame"])
            except (KeyError, TypeError, ValueError):
                raise OperationError("Each range needs integer start_frame and end_frame.")
            a, b = max(a, tl_start), min(b, tl_end)
            if b > a:
                ranges.append([a, b])
        ranges.sort()
        merged = []
        for a, b in ranges:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        if not merged:
            raise OperationError("No ranges fall inside the timeline.")
        removed_total = sum(b - a for a, b in merged)
        if removed_total >= tl_end - tl_start:
            raise OperationError("These ranges would remove the whole timeline. Nothing was changed.")

        def new_position(x):
            shift = 0
            for a, b in merged:
                if x >= b:
                    shift += b - a
                elif x > a:
                    return None
            return x - shift

        def kept_parts(s, e):
            parts, cursor = [], s
            for a, b in merged:
                if b <= cursor or a >= e:
                    continue
                if a > cursor:
                    parts.append((cursor, min(a, e)))
                cursor = max(cursor, b)
            if cursor < e:
                parts.append((cursor, e))
            return parts

        originals, skipped_tracks, snapshots = [], [], {}
        for item, info in self._timeline_items():
            if info["track_type"] == "subtitle":
                skipped_tracks.append("subtitle")
                continue
            media = call(item, "GetMediaPoolItem", None)
            source = call(item, "GetSourceStartFrame", None)
            if source is None:
                source = call(item, "GetLeftOffset", None)
            if media is None or source is None:
                raise OperationError("Clip %s (%s) has no media pool source (generator, title or compound); "
                                     "remove it or work on a timeline of plain media." % (info.get("label"), call(item, "GetName", "")))
            raw_fps = call(media, "GetClipProperty", None, "FPS")
            try:
                source_fps = float(raw_fps) if raw_fps else fps
                if not math.isfinite(source_fps) or source_fps <= 0:
                    source_fps = fps
            except (TypeError, ValueError):
                source_fps = fps
            kind = str(call(media, "GetClipProperty", "", "Type") or "").lower()
            if "still" not in kind and abs(source_fps - fps) > 0.01:
                # Mixed rates are fine at normal speed (Resolve plays them in real time); refuse real retiming.
                self._source_window(item, float(info["start"]))
            s, e = int(float(info["start"])), int(float(info["end"]))
            if not float(info["start"]).is_integer() or not float(info["end"]).is_integer():
                raise OperationError("Subframe clip boundaries cannot be rebuilt.")
            ratio = 1.0 if "still" in kind else source_fps / fps
            originals.append((item, info, media, int(source), s, e, self._read_transform(item) if info["track_type"] == "video" else None, ratio))
            linked = call(item, "GetLinkedItems", None) or []
            linked = list(linked.values()) if isinstance(linked, dict) else list(linked)
            snapshots[id(item)] = dict(self._carry_snapshot(item), uid=call(item, "GetUniqueId", None),
                                       linked=[str(u) for u in (call(o, "GetUniqueId", None) for o in linked) if u])
        if not originals:
            raise OperationError("The timeline has no clips to rebuild.")

        dropped_transitions = self._timeline_transitions(timeline)  # A rebuild cannot re-create them.
        original_identity = self._timeline_identity(timeline)
        name = params.get("name") or "%s (tightened)" % original_identity.get("name", "Timeline")
        preview, preview_identity = self._duplicate_current(name, open_duplicate=True)
        comps_dir = self._work_dir("comps")  # Resolve must be able to write it (App Store sandbox).
        pieces, warnings, stats, placed = 0, [], self._carry_stats(), {}
        try:
            self._delete_timeline_items(preview, self._timeline_items(preview))
            pool = self._media_pool()
            for original, info, media, source, s, e, transform, ratio in originals:
                comp_files = []
                for index in range(1, int(call(original, "GetFusionCompCount", 0) or 0) + 1):
                    target = comps_dir / ("%s_%d.comp" % (uuid.uuid4().hex, index))
                    if call(original, "ExportFusionComp", False, str(target), index) and target.exists():
                        comp_files.append((target, setting_tools(target.read_text(encoding="utf-8", errors="replace"))))
                    else:
                        warnings.append("Could not export the Fusion comp of %s; its rebuilt pieces lack it." % info.get("label"))
                for a, b in kept_parts(s, e):
                    record = new_position(a)
                    if record is None:
                        continue
                    # Timeline frames -> the clip's own frames (time-based for mixed frame rates).
                    # Resolve floors the conformed length, so round the source length UP: the piece then
                    # lands at exactly b - a timeline frames (no 1-frame gaps on mixed frame rates).
                    first = source + int(round((a - s) * ratio))
                    request = {"mediaPoolItem": media, "startFrame": first,
                               "endFrame": first + int(math.ceil((b - a) * ratio - 1e-9)),
                               "recordFrame": record, "trackIndex": info["track_index"],
                               "mediaType": 1 if info["track_type"] == "video" else 2}
                    if not self._is_current_timeline(self._project(), preview):
                        raise OperationError("The active timeline changed while rebuilding.")
                    created = pool.AppendToTimeline([request]) or []
                    if len(created) != 1:
                        raise OperationError("Resolve did not place a rebuilt piece of %s." % info.get("label"))
                    piece = created[0]
                    got_start, got_end = call(piece, "GetStart", None), call(piece, "GetEnd", None)
                    if got_start != record or (got_end is not None and abs(float(got_end) - (record + b - a)) > 1):
                        warnings.append("A piece of %s landed at %s-%s instead of %s-%s." % (info.get("label"), got_start, got_end, record, record + b - a))
                    if transform is not None:
                        self._carry_transform(piece, transform, info.get("label"), warnings)
                        if not call(original, "CopyGrades", False, [piece]):
                            warnings.append("The grade of %s was not copied." % info.get("label"))
                    if info.get("enabled") is False:
                        call(piece, "SetClipEnabled", None, False)
                    if info.get("color"):
                        call(piece, "SetClipColor", None, info["color"])
                    for comp_file, declared in comp_files:
                        if not self._import_comp(piece, comp_file, declared, info.get("label"), stats):
                            warnings.append("Could not re-import the Fusion comp on a piece of %s." % info.get("label"))
                    snapshot = snapshots[id(original)]
                    self._carry_extras(snapshot, piece, info.get("label"), first, b - a, stats)
                    if snapshot["uid"]:
                        placed[(str(snapshot["uid"]), a, b)] = piece
                    pieces += 1
            # Relink pieces cut from linked originals over the same kept span (video with its audio).
            groups = set()
            for snapshot in snapshots.values():
                for (uid, a, b) in list(placed):
                    if uid != str(snapshot["uid"]) or not snapshot["linked"]:
                        continue
                    group = tuple(sorted({uid} | {other for other in snapshot["linked"] if (other, a, b) in placed}))
                    if len(group) > 1:
                        groups.add((group, a, b))
            for group, a, b in sorted(groups):
                if call(preview, "SetClipsLinked", False, [placed[(uid, a, b)] for uid in group], True):
                    stats["links"] += 1
                else:
                    stats["links_failed"] += 1
            # Re-time markers: those inside removed ranges are dropped, the rest move with the edit.
            moved, dropped = 0, 0
            for offset, marker in list((call(preview, "GetMarkers", {}) or {}).items()):
                absolute = tl_start + float(offset)
                target = new_position(int(absolute))
                if not preview.DeleteMarkerAtFrame(float(offset)):
                    continue
                if target is None:
                    dropped += 1
                    continue
                call(preview, "AddMarker", None, float(target - tl_start), marker.get("color", "Blue"), marker.get("name", ""),
                     marker.get("note", ""), marker.get("duration", 1), marker.get("customData", ""))
                moved += 1
        except Exception as exc:
            reopened = self._activate_timeline(self._project(), timeline)
            raise OperationError("Rebuild failed: %s. Original '%s' is intact%s. Partial copy '%s' kept for inspection."
                                 % (exc, original_identity["name"], " and reopened" if reopened else "", preview_identity["name"]))
        finally:
            import shutil
            shutil.rmtree(comps_dir, ignore_errors=True)
        return {"original": original_identity, "result_timeline": preview_identity, "ranges_removed": len(merged),
                "removed_frames": removed_total, "removed_seconds": round(removed_total / fps, 2),
                "pieces": pieces, "markers_moved": moved, "markers_dropped": dropped,
                "warnings": sorted(set(warnings + stats["warnings"]))[:30],
                **self._carry_report(stats, ("static transform, crop, composite mode and opacity (video)",
                                             "current grade layer via CopyGrades (video)", "enabled state", "clip color",
                                             "timeline markers (moved with the edit)")),
                "transitions_dropped": dropped_transitions,
                "note": "Original timeline untouched; the tightened copy is now open. See carried / not_carried for what "
                        "each rebuilt piece kept. Transitions, fades and keyframed audio levels are not carried over%s."
                        % ("" if not skipped_tracks else "; subtitle tracks were not copied (run generate_subtitles on "
                                                         "the new timeline)")}


    # ------------------------------------------------------- in-place trims and clip volume
    #
    # Verified live on Resolve 21.2 Free (24 fps clip on a 24 fps timeline): TimelineItem.SetLeftOffset(n) and
    # SetRightOffset(n) take ABSOLUTE offsets, trim or extend in place with no ripple, leave neighbours alone and
    # move linked audio with the video. Mixed-rate clips quantize (set 25, read back 24), so every result is read
    # back. Overlapping a neighbour was not tested, so it is refused. SetPosition / MoveItems exist on 21.2 Free but
    # return False for every signature tried: there is no in-place move.

    def _item_window(self, item):
        def number(method):
            value = call(item, method, None)
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return {"start": number("GetStart"), "end": number("GetEnd"), "left_offset": number("GetLeftOffset"),
                "right_offset": number("GetRightOffset")}

    def _track_neighbours(self, timeline, item, info):
        """(end of the clip before, start of the clip after) on the item's own track; None at either end."""
        items = self._track_clips(timeline, info["track_type"], info["track_index"])
        start, end = float(info["start"]), float(info["end"])
        uid = call(item, "GetUniqueId", None)
        before, after = None, None
        for other in items:
            if other is item or (uid and call(other, "GetUniqueId", None) == uid):
                continue
            o_start, o_end = float(call(other, "GetStart", 0) or 0), float(call(other, "GetEnd", 0) or 0)
            if o_end <= start and (before is None or o_end > before):
                before = o_end
            if o_start >= end and (after is None or o_start < after):
                after = o_start
        return before, after

    def _linked_items(self, item):
        linked = call(item, "GetLinkedItems", None) or []
        linked = list(linked.values()) if isinstance(linked, dict) else list(linked)
        uid = call(item, "GetUniqueId", None)
        return [other for other in linked if other is not item and call(other, "GetUniqueId", None) != uid]

    def _in_place_trim(self, item, info, head, tail, dry_run=False, still_or_generator_ok=False):
        """Trim (positive) or extend (negative) a clip's head and tail in place with Set*Offset; no ripple."""
        timeline = self._timeline()
        head, tail = int(head or 0), int(tail or 0)
        if not head and not tail:
            raise OperationError("Pass head_frames and/or tail_frames (positive trims, negative extends). Nothing was changed.")
        if info["track_type"] == "subtitle":
            raise OperationError("Subtitle items cannot be trimmed this way. Nothing was changed.")
        media = call(item, "GetMediaPoolItem", None)
        kind = str(call(media, "GetClipProperty", "", "Type") or "").lower() if media is not None else ""
        still = "still" in kind
        if media is None and not still_or_generator_ok:
            raise OperationError("%s has no media pool source (title, generator or compound); use set_clip_length "
                                 "for its length. Nothing was changed." % info["label"])
        group = [(item, info)] + [(other, None) for other in self._linked_items(item)]
        window = self._item_window(item)
        if None in window.values():
            raise OperationError("Resolve did not report %s's position and offsets. Nothing was changed." % info["label"])
        new_start, new_end = window["start"] + head, window["end"] - tail
        if new_end - new_start < 1:
            raise OperationError("That would leave %s with no frames (it has %d). Nothing was changed."
                                 % (info["label"], int(window["end"] - window["start"])))
        problems, plans = [], []
        for member, member_info in group:
            if member_info is None:
                member_info = next((n for i, n in self._timeline_items(timeline) if i is member or (
                    call(i, "GetUniqueId", None) and call(i, "GetUniqueId", None) == call(member, "GetUniqueId", None))), None)
                if member_info is None:
                    problems.append("a linked item of %s is not on this timeline" % info["label"])
                    continue
            w = self._item_window(member)
            if (w["start"], w["end"]) != (window["start"], window["end"]):
                problems.append("linked %s does not share %s's start and end, and Resolve moves linked items together"
                                % (member_info["label"], info["label"]))
                continue
            before, after = self._track_neighbours(timeline, member, member_info)
            if head < 0 and before is not None and new_start < before:
                problems.append("extending %s's head by %d frames would overlap the clip ending at %d on %s (room: %d)"
                                % (member_info["label"], -head, int(before), member_info["label"].split(".")[0],
                                   int(window["start"] - before)))
            if tail < 0 and after is not None and new_end > after:
                problems.append("extending %s's tail by %d frames would overlap the clip starting at %d on %s (room: %d)"
                                % (member_info["label"], -tail, int(after), member_info["label"].split(".")[0],
                                   int(after - window["end"])))
            if head < 0 and media is not None and w["left_offset"] + head < 0:
                problems.append("%s has only %d frames of media before its head" % (member_info["label"], int(w["left_offset"])))
            if tail < 0 and media is not None and not still and w["right_offset"] + tail < 0:
                problems.append("%s has only %d frames of media after its tail" % (member_info["label"], int(w["right_offset"])))
            plans.append((member, member_info, w, before, after))
        if new_start < float(call(timeline, "GetStartFrame", 0) or 0):
            problems.append("the head would move before the timeline start")
        if problems:
            raise OperationError("Refused: %s. Nothing was changed." % "; ".join(problems))
        plan = {"item": info["id"], "label": info["label"], "head_frames": head, "tail_frames": tail,
                "before": {"start": int(window["start"]), "end": int(window["end"])},
                "planned": {"start": int(new_start), "end": int(new_end), "left_offset": int(window["left_offset"] + head),
                            "right_offset": int(window["right_offset"] + tail)},
                "linked": [p[1]["label"] for p in plans[1:]]}
        if dry_run:
            return dict(plan, dry_run=True, note="Plan only; nothing was changed.")
        neighbours = {}
        for _, member_info, _, before, after in plans:
            for other, other_info in self._timeline_items(timeline):
                if other_info["track_type"] == member_info["track_type"] and other_info["track_index"] == member_info["track_index"] \
                        and other_info["id"] != member_info["id"]:
                    neighbours[other_info["id"]] = (other_info["label"], other, self._item_window(other))
        calls = []
        if head:
            calls.append(("SetLeftOffset", call(item, "SetLeftOffset", None, int(window["left_offset"] + head))))
        if tail:
            calls.append(("SetRightOffset", call(item, "SetRightOffset", None, int(window["right_offset"] + tail))))
        after = self._item_window(item)
        moved = [{"item": label, "before": w, "after": self._item_window(other)} for _, (label, other, w) in neighbours.items()
                 if self._item_window(other) != w]
        linked_after = [{"item": p[1]["label"], "start": self._item_window(p[0])["start"], "end": self._item_window(p[0])["end"]}
                        for p in plans[1:]]
        exact = after["start"] == new_start and after["end"] == new_end
        gaps = []  # No ripple: trimmed frames stay empty on the clip's track (and its linked audio's).
        if head > 0 and after["start"] is not None and after["start"] > window["start"]:
            gaps.append({"start_frame": int(window["start"]), "end_frame": int(after["start"]), "where": "before the clip",
                         "touches_previous_clip": plans[0][3] == window["start"]})
        if tail > 0 and after["end"] is not None and after["end"] < window["end"]:
            gaps.append({"start_frame": int(after["end"]), "end_frame": int(window["end"]), "where": "after the clip",
                         "touches_next_clip": plans[0][4] == window["end"]})
        result = dict(plan, calls=[{"call": c, "returned": r} for c, r in calls], actual={k: v if v is None else int(v)
                      for k, v in after.items()}, exact=exact, linked_after=linked_after, gaps_left=gaps,
                      other_clips_moved=moved, verified=exact and not moved and all(
                          (l["start"], l["end"]) == (after["start"], after["end"]) for l in linked_after))
        if head < 0:
            result["unverified_call"] = ("Extending a head earlier (a smaller SetLeftOffset) was not live-tested; the "
                                         "read-back above is the evidence.")
        if not exact:
            result["note"] = ("Resolve placed %s at %s-%s instead of %d-%d (mixed frame rates quantize offsets). This is "
                              "what is on the timeline now." % (info["label"], after["start"], after["end"], new_start, new_end))
        elif moved:
            result["note"] = "Other clips moved unexpectedly; inspect timeline_overview."
        else:
            result["note"] = ("Trimmed in place on the open timeline (no copy, no ripple). Gaps left: %d. Use trim_edit for a "
                              "rippled edit on a copy." % len(gaps))
        return result

    def _op_trim_in_place(self, params):
        """Trim or extend one clip's head/tail in place (no ripple, no copy), verified by read-back."""
        item, info = self._resolve_one_item(params.get("item_id"))
        return self._in_place_trim(item, info, self._int_param(params, "head_frames"), self._int_param(params, "tail_frames"),
                                   dry_run=bool(params.get("dry_run")))

    def _op_set_clip_length(self, params):
        """Set a clip's length in frames by moving its tail (stills, generators and titles too), in place."""
        item, info = self._resolve_one_item(params.get("item_id"))
        frames = self._int_param(params, "frames")
        if frames is None or frames < 1:
            raise OperationError("frames must be a whole number of timeline frames, at least 1.")
        duration = int(float(info["end"]) - float(info["start"]))
        if frames == duration:
            return {"item": info["id"], "frames": frames, "note": "Already %d frames; nothing was changed." % frames}
        result = self._in_place_trim(item, info, 0, duration - frames, dry_run=bool(params.get("dry_run")),
                                     still_or_generator_ok=True)
        if call(item, "GetMediaPoolItem", None) is None:
            result["unverified"] = "SetRightOffset on titles and generators is not live-verified; check the read-back."
        return result

    @staticmethod
    def _int_param(params, key):
        value = params.get(key)
        if value is None:
            return None
        if isinstance(value, bool):
            raise OperationError("%s must be a whole number of frames." % key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise OperationError("%s must be a whole number of frames." % key)
        if not number.is_integer():
            raise OperationError("%s must be a whole number of frames." % key)
        return int(number)

    def _in_place_roll(self, left_id, right_id, frames, dry_run=False):
        """Move the cut between two touching clips by `frames` (positive = later) with Set*Offset, in place."""
        left, li = self._resolve_one_item(left_id)
        right, ri = self._resolve_one_item(right_id)
        if (li["track_type"], li["track_index"]) != (ri["track_type"], ri["track_index"]):
            raise OperationError("roll needs two clips on the same track. Nothing was changed.")
        if float(li["start"]) > float(ri["start"]):
            left, li, right, ri = right, ri, left, li
        if float(li["end"]) != float(ri["start"]):
            raise OperationError("%s and %s do not touch (%s vs %s), so there is no cut to roll. Nothing was changed."
                                 % (li["label"], ri["label"], li["end"], ri["start"]))
        if not frames:
            raise OperationError("frames must be non-zero. Nothing was changed.")
        # Make room first: the clip that shrinks goes before the one that grows, so nothing ever overlaps.
        steps = [(right, ri, frames, 0), (left, li, 0, -frames)] if frames > 0 else [(left, li, 0, -frames), (right, ri, frames, 0)]
        plans = [self._in_place_trim(item, info, head, tail, dry_run=True) for item, info, head, tail in steps[:1]]
        if dry_run:
            return {"dry_run": True, "op": "roll", "cut_before": int(float(li["end"])), "cut_after": int(float(li["end"])) + frames,
                    "first": plans[0], "note": "Plan only; nothing was changed. The second side is checked after the first."}
        done = []
        for item, info, head, tail in steps:
            done.append(self._in_place_trim(item, info, head, tail))
            if not done[-1].get("verified"):
                break
        verified = len(done) == 2 and all(d.get("verified") for d in done)
        return {"op": "roll", "cut_before": int(float(li["end"])), "cut_after": int(float(li["end"])) + frames,
                "steps": done, "verified": verified,
                "note": "Rolled the cut in place (no copy)." if verified else
                        "The roll did not complete as planned; see steps (the first side may already be changed)."}

    EDIT_POINT_OPS = ("trim", "roll", "slip", "slide")

    def _op_edit_points(self, params):
        """trim / roll in place (Set*Offset), or one trim / slip / slide / roll on a new timeline."""
        ops = params.get("ops")
        if not isinstance(ops, list) or not ops:
            raise OperationError("ops must list edits like {op: trim, item_id, head, tail} or {op: roll, left_item_id, "
                                 "right_item_id, frames}.")
        mode = str(params.get("mode") or "in_place").lower()
        if mode not in ("in_place", "new_timeline"):
            raise OperationError("mode must be in_place or new_timeline.")
        dry_run = bool(params.get("dry_run"))
        for op in ops:
            if not isinstance(op, dict) or op.get("op") not in self.EDIT_POINT_OPS:
                raise OperationError("Each op needs op: one of %s." % ", ".join(self.EDIT_POINT_OPS))
        if mode == "new_timeline":
            if len(ops) != 1:
                raise OperationError("new_timeline mode builds one timeline per call from ONE op (the rebuild planner "
                                     "handles one edit at a time). Use mode=in_place for several trims/rolls, or call again.")
            op = dict(ops[0], dry_run=dry_run, name=params.get("name") or ops[0].get("name", ""))
            kind = op["op"]
            if kind == "trim":
                edge = "head" if op.get("head") else "tail"
                amount = op.get("head") if edge == "head" else -int(op.get("tail") or 0)
                return self._op_trim_edit(dict(op, edge=edge, frames=amount, ripple=op.get("ripple", True)))
            return {"slip": self._op_slip_clip, "slide": self._op_slide_clip, "roll": self._op_roll_edit}[kind](op)
        refused = [op["op"] for op in ops if op["op"] in ("slip", "slide")]
        if refused:
            raise OperationError(
                "%s cannot be done in place on Resolve 21.2 Free: it needs the clip to move or its source to change "
                "without its position, and TimelineItem.SetPosition / Timeline.MoveItems return False on Free. Use "
                "mode=new_timeline (a rebuilt copy). Nothing was changed." % " and ".join(sorted(set(refused))))
        results = []
        for index, op in enumerate(ops):
            try:
                if op["op"] == "trim":
                    item, info = self._resolve_one_item(op.get("item_id"))
                    results.append(dict(self._in_place_trim(item, info, self._int_param(op, "head") or 0,
                                                            self._int_param(op, "tail") or 0, dry_run=dry_run), op="trim"))
                else:
                    results.append(self._in_place_roll(op.get("left_item_id"), op.get("right_item_id"),
                                                       self._int_param(op, "frames") or 0, dry_run=dry_run))
            except OperationError as exc:
                return {"mode": mode, "applied": results, "failed": {"index": index, "op": op, "error": str(exc)},
                        "verified": False, "note": "Stopped at op %d; ops before it were applied and read back%s."
                        % (index, " (dry run: nothing was changed)" if dry_run else "")}
        return {"mode": mode, "applied": results, "dry_run": dry_run,
                "verified": None if dry_run else all(r.get("verified") for r in results),
                "note": "In place on the open timeline: no copy, no ripple; gaps_left shows what trims opened."}

    def _op_insert_clip(self, params):
        """Place media into EMPTY space at an absolute frame on the open timeline (no ripple), verified by read-back."""
        if params.get("ripple"):
            raise OperationError("A rippled insert needs every later clip to move, and clips cannot be moved in place on "
                                 "Resolve 21.2 Free (SetPosition / MoveItems are rejected). Open room first (trim_in_place, "
                                 "or remove_ranges on a copy), or insert into a gap with ripple=false. Nothing was changed.")
        media = self._find_media_items([params.get("media_id")])[0]
        src_in, src_out = self._int_param(params, "src_in"), self._int_param(params, "src_out")
        at = self._int_param(params, "at_frame")
        if src_in is None or src_out is None or at is None or src_out <= src_in or src_in < 0:
            raise OperationError("Pass src_in and src_out (source frames, src_out EXCLUSIVE) and at_frame (absolute).")
        kind = str(params.get("kind") or "av").lower()
        if kind not in ("av", "v", "a"):
            raise OperationError("kind must be av, v or a.")
        timeline = self._timeline()
        track = int(params.get("track_index") or 1)
        length = src_out - src_in
        busy = []
        for item, info in self._timeline_items(timeline):
            if info["track_index"] != track or info["track_type"] not in ({"av": ("video", "audio"), "v": ("video",),
                                                                            "a": ("audio",)}[kind]):
                continue
            if float(info["start"]) < at + length and at < float(info["end"]):
                busy.append("%s (%s-%s)" % (info["label"], info["start"], info["end"]))
        if busy:
            raise OperationError("%d frames at %d on track %d would overlap %s. Nothing was changed."
                                 % (length, at, track, ", ".join(busy[:6])))
        types = {"av": [None], "v": [1], "a": [2]}[kind]
        created = []
        for media_type in types:
            # endFrame is EXCLUSIVE in AppendToTimeline, so src_out maps straight through.
            request = {"mediaPoolItem": media, "startFrame": src_in, "endFrame": src_out, "recordFrame": at, "trackIndex": track}
            if media_type is not None:
                request["mediaType"] = media_type
            created += list(self._media_pool().AppendToTimeline([request]) or [])
        if not created:
            raise OperationError("Resolve placed nothing (the media may not hold that range). Nothing was changed.")
        placed = [{"item": call(c, "GetUniqueId", None), "start": call(c, "GetStart", None), "end": call(c, "GetEnd", None),
                   "source_in": call(c, "GetSourceStartFrame", None)} for c in created]
        verified = all(p["start"] == at and p["end"] == at + length for p in placed)
        return {"placed": placed, "requested": {"at_frame": at, "frames": length, "src_in": src_in, "src_out": src_out,
                                                "track_index": track, "kind": kind}, "verified": verified,
                "note": "Placed in place on the open timeline (no copy, nothing moved)." if verified else
                        "Resolve placed it somewhere other than requested; see placed (mixed frame rates conform lengths)."}

    CLIP_VOLUME_RANGE = (-60.0, 24.0)

    def _volume_targets(self, item, info):
        """The audio items whose clip volume stands for this item: itself, or a video item's linked audio."""
        if info["track_type"] == "audio":
            return [(item, info)]
        return [(other, {"id": call(other, "GetUniqueId", None), "label": "linked audio of %s" % info["label"]})
                for other in self._linked_items(item)]

    def _op_read_clip_volumes(self, params):
        """Read-only: current clip volume (dB) of each item, a video item through its linked audio."""
        ids = params.get("item_ids") or []
        if not isinstance(ids, list) or not ids:
            raise OperationError("item_ids must list timeline item ids.")
        clips = []
        for item_id in ids[:500]:
            try:
                item, info = self._resolve_one_item(item_id)
            except OperationError as exc:
                clips.append({"requested_for": item_id, "error": str(exc)})
                continue
            for target, target_info in self._volume_targets(item, info):
                clips.append({"requested_for": info["id"], "item": target_info.get("id"),
                              "label": target_info.get("label"),
                              "db": plain(call(target, "GetProperty", None, "AudioVolume"))})
        return {"clips": clips}

    def _op_set_clip_volume(self, params):
        """Set Inspector clip volume (TimelineItem AudioVolume, dB) on audio items, each read back.

        params: {"items": [{"item_id", "db"}]} or {"item_ids": [...], "db": x}. A video item is mapped to its linked
        audio. Verified live on Resolve 21.2 Free at -6, 0 and +6 dB.
        """
        requests = params.get("items")
        if requests is None:
            ids = params.get("item_ids") or ([params["item_id"]] if params.get("item_id") else [])
            requests = [{"item_id": i, "db": params.get("db")} for i in ids]
        if not isinstance(requests, list) or not requests:
            raise OperationError("Pass item_ids and db, or items: [{item_id, db}].")
        low, high = self.CLIP_VOLUME_RANGE
        plan = []
        for request in requests[:500]:
            try:
                db = round(float(request.get("db")), 2)
            except (TypeError, ValueError, AttributeError):
                raise OperationError("Each item needs a numeric db. Nothing was changed.")
            if not math.isfinite(db) or not low <= db <= high:
                raise OperationError("db must be between %g and %g. Nothing was changed." % (low, high))
            item, info = self._resolve_one_item(request.get("item_id"))
            targets = self._volume_targets(item, info)
            if not targets:
                raise OperationError("%s is a %s item with no linked audio. Nothing was changed." % (info["label"], info["track_type"]))
            plan += [(target, target_info, db, info["id"]) for target, target_info in targets]
        results = []
        for target, target_info, db, requested in plan:
            before = call(target, "GetProperty", None, "AudioVolume")
            returned = call(target, "SetProperty", None, "AudioVolume", db)
            after = call(target, "GetProperty", None, "AudioVolume")
            try:
                ok = after is not None and abs(float(after) - db) <= 0.05
            except (TypeError, ValueError):
                ok = False
            results.append({"item": target_info.get("id"), "label": target_info.get("label"), "requested_for": requested,
                            "db": db, "before_db": before, "after_db": after, "returned": returned, "verified": ok})
        return {"clips": results, "verified": all(r["verified"] for r in results),
                "note": "Clip volume (Inspector > Audio > Volume) set in dB and read back. Track faders and Fairlight "
                        "effects are unchanged." if all(r["verified"] for r in results) else
                        "Some clips did not read back the requested volume; see clips[].after_db."}

    # --------------------------------------------------------------- transitions (FCPXML)
    #
    # Resolve's API cannot add a transition, but on Resolve 21.2 Free (verified live): Timeline.Export(path, 6)
    # writes FCPXML 1.10 as a bundle folder path/Info.fcpxml; a <transition name="Cross Dissolve"> inserted in the
    # spine and MediaPool.ImportTimelineFromFile(edited, {"timelineName", "importSourceClips": True}) gives a NEW
    # timeline with a Cross Dissolve item that survives re-export, and spine positions import exactly. Audio fades
    # (adjust-volume fadeIn/fadeOut) did not show in a re-export: unverified. Everything else a round trip might
    # lose is carried explicitly by the rebuild carry code and read back.

    FCPXML_EXPORT_TYPE = 6  # resolve.EXPORT_FCPXML_1_10 on Resolve 21.2 Free; read from the API when exposed.
    CROSS_DISSOLVE_UID = "FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265"
    TRANSITION_NAME = "Cross Dissolve"

    def _is_still(self, item):
        media = call(item, "GetMediaPoolItem", None)
        return media is not None and "still" in str(call(media, "GetClipProperty", "", "Type") or "").lower()

    def _media_pool_ids(self, pool, limit=20000):
        """{unique id: (item, folder)} for every media pool item, read with as few calls as possible."""
        found, stack = {}, [call(pool, "GetRootFolder", None)]
        while stack and len(found) < limit:
            folder = stack.pop()
            if folder is None:
                continue
            for clip in call(folder, "GetClipList", []) or []:
                uid = call(clip, "GetUniqueId", None)
                if uid:
                    found[str(uid)] = (clip, folder)
            stack += list(call(folder, "GetSubFolderList", []) or [])
        return found

    def _file_new_media(self, pool, before, timeline, bin_name):
        """ImportTimelineFromFile(importSourceClips=True) adds media pool entries for files already in the pool.
        Move the new entries (not the timeline itself) into the agent bin and say how many there were."""
        after = self._media_pool_ids(pool)
        own = call(call(timeline, "GetMediaPoolItem", None), "GetUniqueId", None)
        new = [clip for uid, (clip, _) in after.items() if uid not in before and uid != own]
        report = {"added": len(new), "filed_into": None,
                  "note": "Resolve added %d media pool entr%s for files already in the pool (the same files on disk, "
                          "not copies)." % (len(new), "y" if len(new) == 1 else "ies") if new else None}
        if not new or not bin_name:
            return report
        root = call(pool, "GetRootFolder", None)
        browsing = call(pool, "GetCurrentFolder", None)
        try:
            folder = next((f for f in call(root, "GetSubFolderList", []) or [] if call(f, "GetName", "") == bin_name), None)
            if folder is None:
                folder = call(pool, "AddSubFolder", None, root, bin_name)
            if folder is not None and call(pool, "MoveClips", False, new, folder):
                report["filed_into"] = bin_name
                report["note"] += " They were moved into the %s bin." % bin_name
        finally:
            if browsing is not None:
                call(pool, "SetCurrentFolder", False, browsing)
        return report

    def _work_dir(self, kind):
        """A fresh folder Resolve can write: inside the runtime home (the App Store sandbox only allows its own
        container), or a system temp folder when no runtime home is known."""
        import tempfile
        base = self.runtime_home or os.environ.get("RESOLVE_AI_BRIDGE_HOME")
        if base:
            folder = Path(str(base)).expanduser() / "work" / ("%s-%s" % (kind, uuid.uuid4().hex[:10]))
            folder.mkdir(parents=True, exist_ok=True)
            return folder
        return Path(tempfile.mkdtemp(prefix="resolve-%s-" % kind))

    @staticmethod
    def _fcpxml_time(text):
        """FCPXML rational time ("86447/24s", "3600/1s", "1s", "0s") as a Fraction of seconds."""
        from fractions import Fraction
        value = str(text or "0s").strip()
        if not value.endswith("s"):
            raise OperationError("Unexpected FCPXML time %r." % text)
        return Fraction(value[:-1] or "0")

    @staticmethod
    def _fcpxml_frames(frames, frame):
        """``frames`` as FCPXML time over the sequence's frameDuration denominator, as Resolve writes it
        ("86435/24s", or "x/24000s" at 23.976)."""
        return "%d/%ds" % (int(frames) * frame.numerator, frame.denominator)

    def _transition_layout(self, timeline):
        """V1 clips plus checks that this timeline is a shape the FCPXML route was verified on."""
        problems, video, audio = [], [], []
        for item, info in self._timeline_items(timeline):
            if info["track_type"] == "subtitle":
                problems.append("it has subtitle items (S%d)" % info["track_index"])
            elif info["track_type"] == "video":
                if info["track_index"] != 1:
                    problems.append("it has clips on V%d (only a single video track was verified)" % info["track_index"])
                elif call(item, "GetMediaPoolItem", None) is None:
                    problems.append("%s is a title, generator or compound clip" % info["label"])
                video.append((item, info))
            else:
                if info["track_index"] != 1:
                    problems.append("it has clips on A%d (only the clips' own audio on A1 was verified)" % info["track_index"])
                audio.append((item, info))
        spans = {(float(i["start"]), float(i["end"])) for _, i in video}
        for _, info in audio:
            if (float(info["start"]), float(info["end"])) not in spans:
                problems.append("audio %s does not line up with a video clip" % info["label"])
        if not video:
            problems.append("V1 is empty")
        video.sort(key=lambda pair: float(pair[1]["start"]))
        return video, audio, sorted(set(problems))

    @staticmethod
    def _is_still_item(item):
        """True when a timeline item's media is a single-frame still (an image)."""
        media = call(item, "GetMediaPoolItem", None)
        if media is None:
            return False
        props = plain(call(media, "GetClipProperty", None)) or {}
        if not isinstance(props, dict):
            return False
        if str(props.get("Type", "")).lower() in ("still", "image"):
            return True
        try:
            return int(str(props.get("Frames", "")).strip() or -1) == 1
        except ValueError:
            return False

    def _op_add_transitions(self, params):
        """Cross dissolves at V1 cuts, built on a NEW timeline through an FCPXML round trip. The original is untouched."""
        import xml.etree.ElementTree as ET
        from fractions import Fraction
        project, timeline = self._project(), self._timeline()
        fps = self._project_rate()
        frames = self._int_param(params, "duration_frames")
        frames = int(round(fps)) if frames is None else frames
        if frames < 2 or frames > 10 * fps:
            raise OperationError("duration_frames must be between 2 and %d. Nothing was changed." % int(10 * fps))
        video, audio, problems = self._transition_layout(timeline)
        if problems:
            raise OperationError("add_transitions only handles one video track with its own audio on A1, which is what "
                                 "the FCPXML route was verified on. This timeline can't be represented safely: %s. "
                                 "Nothing was changed." % "; ".join(problems[:8]))
        cuts = {}
        for (left, li), (right, ri) in zip(video, video[1:]):
            if float(li["end"]) == float(ri["start"]):
                cuts[int(float(li["end"]))] = (left, li, right, ri)
        wanted = params.get("cuts", "all")
        if wanted in (None, "all", ["all"]):
            wanted = sorted(cuts)
        elif not isinstance(wanted, list) or not wanted:
            raise OperationError("cuts must be 'all' or a list of absolute timeline frames where two V1 clips meet.")
        lead = frames // 2  # frames before the cut; the rest come after it
        plan, skipped = [], []
        for raw in wanted:
            cut = self._int_param({"cut": raw}, "cut")
            if cut not in cuts:
                skipped.append({"cut_frame": cut, "reason": "no V1 cut there (clips must touch); cuts are %s"
                                % (sorted(cuts)[:20] or "none")})
                continue
            left, li, right, ri = cuts[cut]
            tail_handle, head_handle = float(li["right_offset"] or 0), float(ri["left_offset"] or 0)
            # A still has no real source range: any handle length is available.
            if self._is_still(left):
                tail_handle = float("inf")
            if self._is_still(right):
                head_handle = float("inf")
            if tail_handle < frames - lead or head_handle < lead:
                skipped.append({"cut_frame": cut, "reason": "a %d-frame dissolve needs %d frames after %s's end (has %d) and "
                                "%d before %s's start (has %d)" % (frames, frames - lead, li["label"], tail_handle, lead,
                                                                   ri["label"], head_handle)})
                continue
            plan.append({"cut_frame": cut, "start_frame": cut - lead, "end_frame": cut - lead + frames,
                         "left": li["label"], "right": ri["label"]})
        if not plan:
            raise OperationError("No dissolve can be added: %s. Nothing was changed."
                                 % "; ".join("%s: %s" % (s["cut_frame"], s["reason"]) for s in skipped[:6]))
        if params.get("dry_run"):
            return {"dry_run": True, "transitions": plan, "skipped": skipped, "duration_frames": frames,
                    "note": "Plan only; nothing was changed."}

        original_identity = self._timeline_identity(timeline)
        tl_start = int(float(call(timeline, "GetStartFrame", 0) or 0))
        work = self._work_dir("transitions")
        export_type = getattr(self.resolve, "EXPORT_FCPXML_1_10", None) if self.resolve is not None else None
        export_type = export_type if isinstance(export_type, int) and not isinstance(export_type, bool) else self.FCPXML_EXPORT_TYPE
        target = work / "original.fcpxml"
        if not call(timeline, "Export", False, str(target), export_type):
            raise OperationError("Resolve would not export the timeline as FCPXML. Nothing was changed.")
        source = target / "Info.fcpxml" if target.is_dir() else target
        if not source.is_file():
            raise OperationError("Resolve reported an FCPXML export but wrote no file at %s. Nothing was changed." % target)
        try:
            tree = ET.parse(str(source))
        except ET.ParseError as exc:
            raise OperationError("Could not read Resolve's FCPXML export (%s). Nothing was changed." % exc)
        root = tree.getroot()
        sequence = root.find(".//sequence")
        spine = sequence.find("spine") if sequence is not None else None
        formats = {f.get("id"): f for f in root.iter("format")}
        fmt = formats.get(sequence.get("format")) if sequence is not None else None
        if spine is None or fmt is None or not fmt.get("frameDuration"):
            raise OperationError("Resolve's FCPXML export has no sequence spine or frame rate. Nothing was changed.")
        frame = self._fcpxml_time(fmt.get("frameDuration"))
        if abs(float(1 / frame) - fps) > 0.01:
            raise OperationError("The FCPXML frame rate (%.3f) differs from the timeline's (%.3f). Nothing was changed."
                                 % (float(1 / frame), fps))
        if self._fcpxml_time(sequence.get("tcStart")) != tl_start * frame:
            raise OperationError("The FCPXML start (%s) differs from the timeline start (frame %d). Nothing was changed."
                                 % (sequence.get("tcStart"), tl_start))
        # Resolve writes stills (photos) as <video> elements; both carry the same offset/start/duration attributes.
        unsupported = sorted({child.tag for child in spine if child.tag not in ("asset-clip", "video", "gap", "transition")})
        nested = [child.tag for clip in spine for child in clip.iter() if child is not clip and child.get("lane") is not None]
        retimed = [clip.get("name") for clip in spine if clip.find(".//timeMap") is not None]
        if unsupported or nested or retimed:
            raise OperationError("The FCPXML export holds %s, which this route was not verified on. Nothing was changed."
                                 % ", ".join(filter(None, ["spine elements %s" % unsupported if unsupported else "",
                                                           "connected clips (lanes)" if nested else "",
                                                           "retimed clips %s" % retimed if retimed else ""])))
        clips = {}
        for child in spine:
            if child.tag in ("asset-clip", "video"):
                clips[int(round(self._fcpxml_time(child.get("offset")) / frame))] = child
        existing = {int(round(self._fcpxml_time(c.get("offset")) / frame)) for c in spine if c.tag == "transition"}
        resources = root.find("resources")
        ids = {element.get("id") for element in root.iter() if element.get("id")}
        effect_id = next("r%d" % n for n in range(len(ids) + 100) if "r%d" % n not in ids)
        ET.SubElement(resources, "effect", {"id": effect_id, "name": self.TRANSITION_NAME, "uid": self.CROSS_DISSOLVE_UID})
        fades = bool(params.get("audio_fades"))
        fade_frames = self._int_param(params, "fade_frames") or frames // 2
        placed = []
        for entry in plan:
            right = clips.get(entry["cut_frame"])
            if right is None:
                skipped.append({"cut_frame": entry["cut_frame"], "reason": "the export has no clip starting there"})
                continue
            if any(entry["start_frame"] <= t < entry["end_frame"] for t in existing):
                skipped.append({"cut_frame": entry["cut_frame"], "reason": "a transition is already there"})
                continue
            element = ET.Element("transition", {"name": self.TRANSITION_NAME,
                                                "offset": self._fcpxml_frames(entry["start_frame"], frame),
                                                "duration": self._fcpxml_frames(frames, frame)})
            ET.SubElement(element, "filter-video", {"ref": effect_id, "name": self.TRANSITION_NAME})
            spine.insert(list(spine).index(right), element)
            placed.append(entry)
        fade_notes = []
        if fades:
            for child in spine:
                if child.tag not in ("asset-clip",):  # Fades are audio; stills (<video>) have none.
                    continue
                if child.find("adjust-volume") is not None:
                    fade_notes.append("%s already has a volume adjustment; no fades added" % child.get("name"))
                    continue
                volume = ET.SubElement(child, "adjust-volume")
                amount = ET.SubElement(volume, "param", {"name": "amount"})
                for tag in ("fadeIn", "fadeOut"):
                    ET.SubElement(amount, tag, {"type": "linear", "duration": self._fcpxml_frames(fade_frames, frame)})
        if not placed:
            raise OperationError("No dissolve could be placed: %s. Nothing was changed."
                                 % "; ".join("%s: %s" % (s["cut_frame"], s["reason"]) for s in skipped[:6]))
        edited = work / "with-transitions.fcpxml"
        tree.write(str(edited), encoding="UTF-8", xml_declaration=True)

        # Everything per clip that the round trip may drop, read from the untouched original first.
        originals = {}
        for item, info in video + audio:
            originals[(info["track_type"], int(float(info["start"])), int(float(info["end"])))] = (
                item, info, self._carry_snapshot(item), self._read_transform(item) if info["track_type"] == "video" else None)
        comp_files = {}
        for key, (item, info, _, _) in originals.items():
            files = []
            for index in range(1, int(call(item, "GetFusionCompCount", 0) or 0) + 1):
                path = work / ("%s_%d.comp" % (uuid.uuid4().hex, index))
                if call(item, "ExportFusionComp", False, str(path), index) and path.exists():
                    files.append((path, setting_tools(path.read_text(encoding="utf-8", errors="replace"))))
            comp_files[key] = files
        markers = plain(call(timeline, "GetMarkers", {}) or {})

        name = "%s (transitions) — %s" % (str(original_identity.get("name") or "Timeline")[:120], uuid.uuid4().hex[:8])
        pool = self._media_pool()
        # importSourceClips must be True: with False, Resolve 21.2 Free imports clips with NO media pool item (found
        # live), so nothing on the new timeline can be transcribed, relinked or carried. True adds a media pool item
        # for the same file (a second entry, not a copy of the media).
        media_before = self._media_pool_ids(pool)
        imported = call(pool, "ImportTimelineFromFile", None, str(edited), {"timelineName": name, "importSourceClips": True})
        if imported is None or isinstance(imported, bool):
            imported = next((t for t in (call(project, "GetTimelineByIndex", None, i)
                                         for i in range(1, int(call(project, "GetTimelineCount", 0) or 0) + 1))
                             if call(t, "GetName", "") == name), None)
        if imported is None:
            self._activate_timeline(project, timeline)
            raise OperationError("Resolve did not import the edited FCPXML (%s). The original is untouched and open." % edited)
        identity = self._timeline_identity(imported)
        identity["bin"] = self._file_into_bin(imported, getattr(self, "_bin", self.AGENT_BIN))
        media_entries = self._file_new_media(pool, media_before, imported, getattr(self, "_bin", self.AGENT_BIN))
        if not self._activate_timeline(project, imported):
            raise OperationError("Imported '%s' but Resolve would not open it; the original is untouched." % name)
        stats, warnings, mismatches, transitions_found = self._carry_stats(), [], [], []
        if int(float(call(imported, "GetStartFrame", tl_start) or tl_start)) != tl_start:
            mismatches.append("the new timeline starts at frame %s, the original at %d"
                              % (call(imported, "GetStartFrame", None), tl_start))
        spans = {(p["start_frame"], p["end_frame"]) for p in placed}
        matched = set()
        transitions_found = [t for t in self._timeline_transitions(imported)
                             if t["track"] == "V1" and (t["start_frame"], t["end_frame"]) in spans]
        for item, info in self._timeline_items(imported):
            key = (info["track_type"], int(float(info["start"] or 0)), int(float(info["end"] or 0)))
            original = originals.get(key)
            if original is None or key in matched:
                mismatches.append("%s at %d-%d has no matching clip in the original" % (info["label"], key[1], key[2]))
                continue
            matched.add(key)
            was, other = call(original[0], "GetLeftOffset", None), call(item, "GetLeftOffset", None)
            piece_source = call(item, "GetSourceStartFrame", None)
            piece_source = other if piece_source is None else piece_source
            # A still has no source timeline: Resolve 21.2 reports its left offset as the timeline start (86400) on
            # the original and 0 after an FCPXML import (found live), so source in is not compared for stills.
            if was is not None and other is not None and int(float(was)) != int(float(other)) \
                    and not self._is_still_item(original[0]):
                mismatches.append("%s source in %s differs from the original's %s" % (info["label"], other, was))
            o_item, o_info, snapshot, transform = original
            if transform is not None:
                self._carry_transform(item, transform, info["label"], warnings)
                if not call(o_item, "CopyGrades", False, [item]):
                    warnings.append("The grade of %s was not copied." % info["label"])
            if o_info.get("enabled") is False:
                call(item, "SetClipEnabled", None, False)
            if o_info.get("color"):
                call(item, "SetClipColor", None, o_info["color"])
            for comp_file, declared in comp_files.get(key, []):
                if not self._import_comp(item, comp_file, declared, info["label"], stats):
                    warnings.append("Could not re-import the Fusion comp on %s." % info["label"])
            self._carry_extras(snapshot, item, info["label"], piece_source, key[2] - key[1], stats)
        missing = sorted(set(originals) - matched)
        mismatches += ["the %s1 clip at %d-%d did not come back" % (k[0][0].upper(), k[1], k[2]) for k in missing]
        for files in comp_files.values():
            for path, _ in files:
                try:
                    path.unlink()
                except OSError:
                    pass
        existing_markers = plain(call(imported, "GetMarkers", {}) or {})
        timeline_markers = 0
        for offset, marker in markers.items():
            if offset in existing_markers or str(offset) in {str(k) for k in existing_markers}:
                continue
            if isinstance(marker, dict) and call(imported, "AddMarker", False, float(offset), marker.get("color", "Blue"),
                                                 marker.get("name", ""), marker.get("note", ""), marker.get("duration", 1),
                                                 marker.get("customData", "")):
                timeline_markers += 1
        report = self._carry_report(stats, ("static transform, crop, composite mode and opacity (re-applied from the "
                                            "original)", "current grade layer via CopyGrades", "enabled state", "clip color",
                                            "%d timeline marker(s)" % timeline_markers))
        report["not_carried"] = [line for line in report["not_carried"] if not line.startswith("transitions")] + [
            "audio crossfades: the audio stays a hard cut under each dissolve. Resolve 21.2 Free drops audio transitions "
            "on FCPXML import (live-tested: Final Cut's 'Audio Crossfade' and Resolve's 'Cross Fade +3 dB' both vanished); "
            "add them in Resolve with the audio edit points selected (Timeline > Add Audio Only Transition)",
            "other transitions and fades the original had, beyond what Resolve's own FCPXML export carries"]
        if fades:
            report["not_carried"].append("the audio fades you asked for are in the FCPXML but did not survive a re-export "
                                         "in the live probe: check each clip's fade handles")
        verified = not mismatches and len(transitions_found) == len(placed)
        result = {"original": original_identity, "result_timeline": identity, "media_pool_entries": media_entries,
                  "transitions": placed,
                  "transitions_found": transitions_found, "skipped": skipped, "duration_frames": frames,
                  "audio_fades": {"requested": fades, "frames": fade_frames if fades else None, "verified": None,
                                  "notes": fade_notes[:20]} if fades else None,
                  "verified": verified, "mismatches": mismatches[:50], "warnings": sorted(set(warnings + stats["warnings"]))[:30],
                  "fcpxml": {"export": str(source), "edited": str(edited), "export_type": export_type}, **report}
        result["note"] = ("Built '%s' with %d Cross Dissolve(s) through an FCPXML round trip and opened it; the original "
                          "timeline is untouched. Look at each dissolve (render_frames on its frames) before calling it done."
                          % (name, len(placed)))
        if not verified:
            result["note"] += (" VERIFICATION FAILED: the new timeline does not match the original layout or a dissolve is "
                               "missing; see mismatches and transitions_found before using it.")
        return result

    def handlers(self):
        return {
            "status": self._op_status,
            "preview_timeline": self._op_preview_timeline,
            "checkpoint_timeline": self._op_checkpoint_timeline,
            "restore_checkpoint": self._op_restore_checkpoint,
            "list_checkpoints": self._op_list_checkpoints,
            "trim_in_place": self._op_trim_in_place,
            "edit_points": self._op_edit_points,
            "get_preferences": self._op_get_preferences,
            "set_preferences": self._op_set_preferences,
            "insert_clip": self._op_insert_clip,
            "diff_since": self._op_diff_since,
            "copy_grade": self._op_copy_grade,
            "set_clip_properties": self._op_set_clip_properties,
            "set_clip_length": self._op_set_clip_length,
            "set_clip_volume": self._op_set_clip_volume,
            "read_clip_volumes": self._op_read_clip_volumes,
            "add_transitions": self._op_add_transitions,
            "compare_timelines": self._op_compare_timelines,
            "project_health": self._op_project_health,
            "bridge_capabilities": self._op_bridge_capabilities,
            "review_silence": self._op_review_silence,
            "apply_silence_cuts": self._op_apply_silence_cuts,
            "transcription_source": self._op_transcription_source,
            "review_transcript_cuts": self._op_review_transcript_cuts,
            "import_subtitles": self._op_import_subtitles,
            "media_sources": self._op_media_sources,
            "add_markers": self._op_add_markers,
            "remove_ranges": self._op_remove_ranges,
            "project_info": self._op_project_info,
            "list_timelines": self._op_list_timelines,
            "open_timeline": self._op_open_timeline,
            "timeline_overview": self._op_timeline_overview,
            "list_media": self._op_list_media,
            "import_media": self._op_import_media,
            "append_media": self._op_append_media,
            "add_image": self._op_add_image,
            "insert_title": self._op_insert_title,
            "set_clip_transform": self._op_set_clip_transform,
            "get_clip_transform": self._op_get_clip_transform,
            "split_clip": self._op_split_clip,
            "animate_zoom": self._op_animate_zoom,
            "add_track": self._op_add_track,
            "set_track_name": self._op_set_track_name,
            "create_timeline": self._op_create_timeline,
            "set_playhead": self._op_set_playhead,
            "open_page": self._op_open_page,
            "add_marker": self._op_add_marker,
            "delete_marker": self._op_delete_marker,
            "set_clip_property": self._op_set_clip_property,
            "set_clip_enabled": self._op_set_clip_enabled,
            "set_clip_color": self._op_set_clip_color,
            "get_clip_grade": self._op_get_clip_grade,
            "set_clip_grade": self._op_set_clip_grade,
            "keyframe_clip_saturation": self._op_keyframe_clip_saturation,
            "animate_color_fx": self._op_animate_color_fx,
            "apply_blur_effect": self._op_apply_blur_effect,
            "apply_spotlight_mask": self._op_apply_spotlight_mask,
            "apply_mask": self._op_apply_spotlight_mask,
            "inspect_fusion": self._op_inspect_fusion,
            "fusion_inspect_tool": self._op_fusion_inspect_tool,
            "fusion_build": self._op_fusion_build,
            "fusion_keyframe_input": self._op_fusion_keyframe_input,
            "fusion_apply_setting": self._op_fusion_apply_setting,
            "fusion_run_lua": self._op_fusion_run_lua,
            "fusion_graph": self._op_fusion_graph,
            "fusion_copy_comp": self._op_fusion_copy_comp,
            "current_context": self._op_current_context,
            "apply_shot_match": self._op_apply_shot_match,
            "shot_match_sources": self._op_shot_match_sources,
            "project_overview": self._op_project_overview,
            "visual_diff": self._op_visual_diff,
            "styled_title": self._op_styled_title,
            "resolve_api": self._op_resolve_api,
            "timeline_fingerprint": self._op_timeline_fingerprint,
            "timeline_notes": self._op_timeline_notes,
            "resolve_timeline_note": self._op_resolve_timeline_note,
            "delete_clips": self._op_delete_clips,
            "list_ai_timelines": self._op_list_ai_timelines,
            "cleanup_ai_timelines": self._op_cleanup_ai_timelines,
            "save_project": self._op_save_project,
            "trim_edit": self._op_trim_edit,
            "slip_clip": self._op_slip_clip,
            "slide_clip": self._op_slide_clip,
            "roll_edit": self._op_roll_edit,
            "list_render_presets": self._op_list_render_presets,
            "render_current_timeline": self._op_render_current_timeline,
            "render_status": self._op_render_status,
            "render_preview": self._op_render_preview,
            "render_preview_cleanup": self._op_render_preview_cleanup,
            "timeline_frame": self._op_timeline_frame,
            "timeline_audio": self._op_timeline_audio,
            "create_compound_clip": self._op_create_compound_clip,
            "change_clip_speed": self._op_change_clip_speed,
        }

    def dispatch(self, operation, params=None):
        handlers = self.handlers()
        handler = handlers.get(str(operation))
        if handler is None:
            raise OperationError(
                "Unknown operation '%s'. Available: %s" % (operation, ", ".join(sorted(handlers)))
            )
        params = params or {}
        # Every timeline the bridge creates is filed into this bin (default "Agent Work", or the project's
        # agent_bin preference); "" leaves it where Resolve put it. Read by _duplicate_current, add_transitions
        # and create_timeline.
        requested = params.get("bin")
        if requested is None:
            requested = self._preference("agent_bin", self.AGENT_BIN) if operation in self.CREATES_TIMELINES else self.AGENT_BIN
        self._bin = str(requested).strip() if requested is not None else ""
        return plain(handler(params))

    def queue_context(self):
        project = self.resolve.GetProjectManager().GetCurrentProject()
        timeline = project.GetCurrentTimeline() if project is not None else None
        def identity(obj):
            if obj is None:
                return None
            value = call(obj, "GetUniqueId", None)
            if not value:
                raise OperationError("Stable project/timeline identity unavailable; queued edits refused.")
            return str(value)
        return {"project_id": identity(project), "timeline_id": identity(timeline)}

    def heartbeat_payload(self, extra=None):
        payload = self._op_status({})
        payload.update({"pid": os.getpid(), "time": time.time()})
        if extra:
            payload.update(extra)
        return plain(payload)

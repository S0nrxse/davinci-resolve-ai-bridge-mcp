"""Recognise the timelines Resolve AI Bridge creates, so they can be listed and tidied away.

Pure name logic only: standard library, no Resolve objects, safe to import anywhere
(including Resolve's embedded interpreter through ``bridge/operations.py``).

Every bridge-made timeline comes from ``ResolveOperations._duplicate_current``, which
names the copy ``"<name> — <8 lowercase hex>"`` (an em dash, then ``uuid4().hex[:8]``).
The ``<name>`` part depends on the tool that made it:

==============================================  =============  ====================================
Name before `` — <hex>``                        kind           made by
==============================================  =============  ====================================
``Checkpoint · <timeline> · <label>``           checkpoint     checkpoint_timeline (chat panel)
``<timeline> preview``                          preview        preview_timeline (default name)
``<timeline> (tightened)``                      tightened      remove_ranges / remove_pauses / gaming cleanup
``Dialogue cleanup``                            tightened      apply_silence_cuts (default name)
``<timeline> (rewound)``                        rewound        restore_checkpoint (chat panel Rewind)
``Before split``                                before_split   split_clip's safety checkpoint
anything else                                   other_ai       a bridge tool given a custom ``name``
==============================================  =============  ====================================

A name without the `` — <hex>`` suffix is never treated as bridge-made, even if it
contains "(tightened)" or "Checkpoint": the user may have typed it, and
``create_timeline`` uses the user's exact name with no suffix.
"""

import re

CHECKPOINT_PREFIX = "Checkpoint · "  # Mirrors ResolveOperations.CHECKPOINT_PREFIX.
ARCHIVE_BIN = "Agent Work"  # Same bin every bridge-made timeline is filed into (ResolveOperations.AGENT_BIN).
KINDS = ("checkpoint", "preview", "tightened", "rewound", "before_split", "other_ai")

_SUFFIX = re.compile(r"^(?P<body>.+) — (?P<tag>[0-9a-f]{8})$", re.DOTALL)

_MADE_BY = {
    "checkpoint": "checkpoint_timeline (chat panel restore point)",
    "preview": "preview_timeline",
    "tightened": "remove_ranges / remove_pauses",
    "rewound": "restore_checkpoint (chat panel Rewind)",
    "before_split": "split_clip (pre-split checkpoint)",
    "other_ai": "a bridge tool given a custom name",
}


def classify(name):
    """Return ``{"kind", "made_by", "tag", "source_candidates"}`` for a bridge-made name, else None.

    ``source_candidates`` lists possible names of the timeline it was copied from,
    most likely first. It is empty when the name carries no hint (for example
    "Before split" or a custom name).
    """
    match = _SUFFIX.match(str(name or ""))
    if not match:
        return None
    body, tag = match.group("body"), match.group("tag")
    made_by = None
    if body.startswith(CHECKPOINT_PREFIX):
        kind = "checkpoint"
        rest = body[len(CHECKPOINT_PREFIX):]
        parts = rest.split(" · ")
        # "<timeline> · <label>": both parts may contain " · ", so offer every split,
        # longest timeline name first (the same rsplit restore_checkpoint uses).
        candidates = [" · ".join(parts[:cut]) for cut in range(len(parts) - 1, 0, -1)]
        if not candidates and rest:
            candidates = [rest]
    elif body.endswith(" (tightened)"):
        kind, candidates = "tightened", [body[:-len(" (tightened)")]]
    elif body == "Dialogue cleanup":
        kind, candidates, made_by = "tightened", [], "apply_silence_cuts"
    elif body.endswith(" (rewound)"):
        kind, candidates = "rewound", [body[:-len(" (rewound)")]]
    elif body == "Before split":
        kind, candidates = "before_split", []
    elif body.endswith(" preview"):
        kind, candidates = "preview", [body[:-len(" preview")]]
    else:
        kind, candidates = "other_ai", []
    return {"kind": kind, "made_by": made_by or _MADE_BY[kind], "tag": tag,
            "source_candidates": [c for c in candidates if c]}


def likely_original(info, timelines, own_id=None):
    """Best-effort guess at the timeline a bridge copy came from, by exact name.

    ``timelines`` is a list of ``{"id", "name"}``. Returns ``(source_name, match, candidates)``:
    ``match`` is ``{"id", "name"}`` when exactly one other timeline carries the
    first candidate name that exists; ``candidates`` lists every id with that name
    when more than one does (names are not unique in Resolve).
    """
    if not info or not info.get("source_candidates"):
        return None, None, []
    by_name = {}
    for entry in timelines:
        if own_id is not None and str(entry.get("id")) == str(own_id):
            continue
        by_name.setdefault(entry.get("name"), []).append(entry)
    for candidate in info["source_candidates"]:
        found = by_name.get(candidate)
        if found:
            if len(found) == 1:
                return candidate, {"id": found[0].get("id"), "name": candidate}, []
            return candidate, None, [entry.get("id") for entry in found]
    return info["source_candidates"][0], None, []


def refusal(entry, include_checkpoints):
    """Why a listed timeline may not be cleaned up, or None when it may."""
    if entry is None:
        return "No timeline with this unique ID exists in the open project."
    if not entry.get("kind"):
        return ("Not a bridge-made timeline (its name lacks the bridge's ' — <8 hex>' suffix); "
                "user timelines are never removable here.")
    if entry.get("current"):
        return "This is the open timeline. Open a different timeline in Resolve first."
    if entry.get("kind") == "checkpoint" and not include_checkpoints:
        return ("Chat panel restore point: removing it breaks Rewind's 'restore the timeline' for that message. "
                "Excluded unless include_checkpoints=true.")
    return None

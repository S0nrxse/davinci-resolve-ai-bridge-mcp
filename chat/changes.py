"""What changed in Resolve between two timeline fingerprints, in plain sentences.

The chat panel snapshots the timeline when a turn ends and again when the next message is sent, so the
agent hears about edits made by hand in between ("Moved interview.mov from V1 to V2"). Standard library
only; the fingerprints come from the bridge's read-only ``timeline_fingerprint`` operation.
"""

MAX_LINES = 25


def _tc(frame, fps, start_frame=0):
    """Absolute timeline frame -> non-drop HH:MM:SS:FF, counting from 01:00:00:00 at the start frame."""
    if frame is None:
        return "?"
    rate = max(1, int(round(float(fps or 24))))
    total = int(frame)
    ff = total % rate
    seconds = total // rate
    return "%02d:%02d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60, ff)


def timecode(frame, fps, drop_frame=False):
    """Absolute timeline frame -> the timecode Resolve expects (drop-frame for 29.97/59.94 DF timelines)."""
    rate = float(fps or 24)
    nominal = max(1, int(round(rate)))
    frame = max(0, int(frame))
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


def find_clip(fingerprint, query):
    """Best timeline clip for a name or unique id: exact id, exact name, then name containing the query.
    Video tracks win over audio; earliest first. Returns (item, number_of_matches)."""
    q = str(query or "").strip().lower()
    items = [i for i in fingerprint.get("items") or [] if i.get("start") is not None]
    if not q:
        return None, 0
    for test in (lambda i: str(i.get("id") or "").lower() == q, lambda i: str(i.get("name") or "").lower() == q,
                 lambda i: q in str(i.get("name") or "").lower()):
        found = [i for i in items if test(i)]
        if found:
            found.sort(key=lambda i: (not str(i.get("track", "")).startswith("V"), i.get("start") or 0))
            return found[0], len(found)
    return None, 0


def _frames(n):
    return "%+d frame%s" % (n, "" if abs(n) == 1 else "s")


def _name(item):
    return "“%s”" % (item.get("name") or "clip")


def describe(before, after):
    """Sentences describing how ``after`` differs from ``before``. Empty when nothing changed."""
    if not before or not after:
        return []
    lines = []
    if before.get("project") != after.get("project"):
        return ["Switched project from “%s” to “%s”." % (before.get("project"), after.get("project"))]
    old_tls = {t["id"]: t["name"] for t in before.get("timelines") or [] if t.get("id")}
    new_tls = {t["id"]: t["name"] for t in after.get("timelines") or [] if t.get("id")}
    for tid in new_tls.keys() - old_tls.keys():
        lines.append("New timeline “%s”." % new_tls[tid])
    for tid in old_tls.keys() - new_tls.keys():
        lines.append("Deleted timeline “%s”." % old_tls[tid])
    for tid in new_tls.keys() & old_tls.keys():
        if new_tls[tid] != old_tls[tid]:
            lines.append("Renamed timeline “%s” to “%s”." % (old_tls[tid], new_tls[tid]))
    old_tl, new_tl = before.get("timeline") or {}, after.get("timeline") or {}
    if old_tl.get("id") != new_tl.get("id"):
        lines.append("Opened timeline “%s” (was “%s”)." % (new_tl.get("name"), old_tl.get("name")))
        return lines[:MAX_LINES]
    fps = after.get("fps") or 24
    tc = lambda f: _tc(f, fps)
    old_items = {i["id"]: i for i in before.get("items") or [] if i.get("id")}
    new_items = {i["id"]: i for i in after.get("items") or [] if i.get("id")}
    clip_lines, shifted = [], {}
    for iid in sorted(old_items.keys() - new_items.keys(), key=lambda k: (old_items[k].get("start") or 0)):
        o = old_items[iid]
        clip_lines.append("Removed %s from %s at %s." % (_name(o), o.get("track"), tc(o.get("start"))))
    for iid in sorted(new_items.keys() - old_items.keys(), key=lambda k: (new_items[k].get("start") or 0)):
        n = new_items[iid]
        clip_lines.append("Added %s on %s at %s." % (_name(n), n.get("track"), tc(n.get("start"))))
    for iid in sorted(new_items.keys() & old_items.keys(), key=lambda k: (new_items[k].get("start") or 0)):
        o, n = old_items[iid], new_items[iid]
        if (o.get("start"), o.get("end"), o.get("offset"), o.get("track")) == \
                (n.get("start"), n.get("end"), n.get("offset"), n.get("track")):
            continue
        ds = (n.get("start") or 0) - (o.get("start") or 0)
        de = (n.get("end") or 0) - (o.get("end") or 0)
        do = (n.get("offset") or 0) - (o.get("offset") or 0)
        if o.get("track") != n.get("track"):
            clip_lines.append("Moved %s from %s to %s (now at %s)." % (_name(n), o.get("track"), n.get("track"),
                                                                       tc(n.get("start"))))
        elif ds == de and do == 0:
            shifted.setdefault(ds, []).append(n)  # Whole clip moved: often many at once (a ripple).
        elif ds == 0 and de == 0 and do:
            clip_lines.append("Slipped %s by %s." % (_name(n), _frames(do)))
        else:
            parts = []
            if ds:
                parts.append("head %s" % _frames(ds))
            if de:
                parts.append("tail %s" % _frames(de))
            clip_lines.append("Trimmed %s on %s (%s)." % (_name(n), n.get("track"), ", ".join(parts)))
    for delta, items in sorted(shifted.items(), key=lambda kv: -len(kv[1])):
        if len(items) >= 3:
            first = min(items, key=lambda i: i.get("start") or 0)
            clip_lines.append("%d clips from %s on moved by %s (a ripple)." % (len(items), tc(first.get("start")),
                                                                              _frames(delta)))
        else:
            for n in items:
                clip_lines.append("Moved %s on %s by %s (now at %s)." % (_name(n), n.get("track"), _frames(delta),
                                                                         tc(n.get("start"))))
    lines += clip_lines
    old_markers, new_markers = before.get("markers") or {}, after.get("markers") or {}
    base = after.get("start_frame") or 0
    for key in sorted(new_markers.keys() - old_markers.keys(), key=lambda k: int(float(k))):
        m = new_markers[key]
        lines.append("Added %s marker “%s” at %s." % ((m.get("color") or "").lower() or "a", m.get("name") or "",
                                                      tc(base + int(float(key)))))
    for key in sorted(old_markers.keys() - new_markers.keys(), key=lambda k: int(float(k))):
        m = old_markers[key]
        lines.append("Removed marker “%s” at %s." % (m.get("name") or "", tc(base + int(float(key)))))
    for key in sorted(old_markers.keys() & new_markers.keys(), key=lambda k: int(float(k))):
        o, n = old_markers[key], new_markers[key]
        if (o.get("name"), o.get("note"), o.get("color")) != (n.get("name"), n.get("note"), n.get("color")):
            lines.append("Edited marker at %s (now “%s”)." % (tc(base + int(float(key))), n.get("name") or ""))
    if after.get("truncated") or before.get("truncated"):
        lines.append("(Only the first clips were compared; the timeline is very long.)")
    if len(lines) > MAX_LINES:
        extra = len(lines) - MAX_LINES + 1
        lines = lines[:MAX_LINES - 1] + ["…and %d more changes." % extra]
    return lines

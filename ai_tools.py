"""Daily Scheduler — AI tool execution.

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of mainwindow.py in v4.3.0.

`_ai_execute` is the dispatcher the AI panel calls for every tool the model
invokes; it and its two tool-only helpers live here as a mixin so they stay
next to each other and out of the window class. MainWindow inherits this, so
every `self.` reference (schedule state, calendar cache, `_refresh_view`) still
resolves exactly as before — this is a move, not a rewrite.

The tools (search for `name == "<tool>"` to jump to one)

    Single block     add_block, delete_block, move_block, split_block
    Whole day        clear_day, copy_day, replace_day, shift_blocks,
                     add_recurring, clear_range
    Planners         schedule_tasks (fit UNORDERED tasks into real free time),
                     plan_day (build an ORDERED day around fixed anchors),
                     make_room (insert an appointment, ripple the rest),
                     reflow_from_now (running late),
                     plan_for_deadline (spread work across days)
    Read-only        find_free_time, list_blocks, week_summary
                     — these never mutate, and core.AI_READONLY_TOOLS keeps
                       them from creating an undo snapshot

Two invariants every mutating tool upholds:

  * **Never overlap.** Placement goes through core.sequentialize() /
    find_free_placement() with `blocked=self._cal_intervals(ds)`, so editable
    blocks are pushed off read-only Google Calendar events instead of landing
    on a meeting.
  * **Never silently destroy.** Only replace_day and clear_* remove blocks, and
    every mutation is preceded by `_ai_snapshot_before()` so ↶ Undo can restore
    the whole turn.

Each branch returns a human-readable string that is BOTH shown in chat and fed
back to the model as the tool result — so the wording doubles as the model's
feedback signal. Keep it factual and specific (what moved, to when, what was
dropped); vague results make the model narrate instead of verifying.
"""

import json
from datetime import datetime, date, timedelta
from typing import Dict, List

import core
from core import (
    ACTIVITY_TYPES,
    _WEEKDAYS,
    _earliest_fit,
    _free_slots,
    allday_cal_events,
    coerce_end_min,
    find_free_placement,
    fmt_dur,
    fmt_time,
    new_id,
    norm_title,
    parse_hhmm,
    resolve_date,
    save_all_activities,
    sequentialize,
    timed_cal_events,
)


class AIToolsMixin:
    """AI tool execution for MainWindow. Not standalone — expects the host to
    provide `_all_acts`, `_cur_date`, `_cal_by_date`, `_settings`, and the
    `_day_acts` / `_cal_intervals` / `_ai_snapshot_before` / `_refresh_view`
    methods."""

    def _free_gaps(self, ds: str, after=core.DAY_START, before=core.DAY_END):
        """Open intervals on `ds` not occupied by editable blocks OR timed calendar
        events, within [after, before]. All-day events do not consume free time.
        Returns [(start, end)] in minutes."""
        occ = [(a["startMin"], a["endMin"]) for a in self._all_acts if a.get("date") == ds] + \
              [(e["startMin"], e["endMin"])
               for e in timed_cal_events(self._cal_by_date.get(ds, []))]
        return [(s, e) for s, e in _free_slots(occ, after, before) if e > s]

    def _select_acts(self, ds: str, title=None, at=None) -> List[Dict]:
        """Select user blocks on date `ds` by fuzzy title and/or start time `at`
        (24h HH:MM). With `at`, matches the block starting at that time, or — if none
        starts exactly then — the block that covers that minute. Combining title+at
        narrows to blocks that satisfy both. Raises ValueError on a bad time."""
        pool = [a for a in self._all_acts if a.get("date") == ds]
        q = norm_title(title) if title else None
        if q is not None:
            pool = [a for a in pool
                    if q in norm_title(a.get("title", ""))
                    or norm_title(a.get("title", "")) in q]
        if at:
            tm = parse_hhmm(str(at))
            exact = [a for a in pool if a["startMin"] == tm]
            pool = exact if exact else [a for a in pool
                                        if a["startMin"] <= tm < a["endMin"]]
        return pool

    def _ai_execute(self, name: str, args: Dict) -> str:
        """Run one AI tool call against the schedule. Returns a result string
        that is shown in chat AND fed back to the model."""
        try:
            self._ai_snapshot_before(name)   # capture undo point before a change
            ds = resolve_date(args.get("date"), self._cur_date)
            if ds is None:
                return (f"Error: couldn't understand the date "
                        f"'{args.get('date')}'. Use Month/Day like 6/14, or 'tomorrow'.")

            # ── SINGLE BLOCK — add / delete / move ──────────────────────────
            if name == "add_block":
                sm = parse_hhmm(str(args["start"]))
                em = coerce_end_min(sm, parse_hhmm(str(args["end"])))
                if em <= sm:
                    return "Error: end must be after start (use 24:00 for end of day)."
                tid = str(args.get("type", "study"))
                at  = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                title = str(args.get("title") or f"{at['icon']} {at['label']}")
                day_blocks = [b for b in self._all_acts if b.get("date") == ds] + \
                             timed_cal_events(self._cal_by_date.get(ds, []))
                dur    = em - sm
                placed = find_free_placement(day_blocks, sm, dur)
                if placed is None:
                    return (f"Error: no free {fmt_dur(dur)} slot left on {ds} — the day "
                            f"is full. Rebuild it with replace_day, or use a shorter block.")
                note = ""
                if placed != sm:
                    note = (f" ({fmt_time(sm)} was taken — placed at the nearest free "
                            f"slot instead.)")
                sm, em = placed, placed + dur
                self._all_acts.append({
                    "id": new_id(), "date": ds, "startMin": sm, "endMin": em,
                    "type": at["id"], "color": at["color"], "title": title,
                })
                save_all_activities(self._all_acts)
                self._refresh_view()
                return f"Added '{title}' on {ds}, {fmt_time(sm)}–{fmt_time(em)}.{note}"

            if name == "delete_block":
                title = args.get("title")
                at    = args.get("at")
                if not (title and str(title).strip()) and not at:
                    return ("Error: give a title and/or a time ('at'). To remove every "
                            "block on a date, call clear_day instead.")
                try:
                    hits = self._select_acts(ds, title, at)
                except ValueError as ex:
                    return f"Error: {ex}"
                if not hits:
                    avail = ", ".join(f"'{a['title']}' {fmt_time(a['startMin'])}"
                                      for a in sorted(self._day_acts(),
                                                      key=lambda x: x["startMin"])) or "none"
                    sel = (f"title '{title}'" if title else "") + \
                          (f" at {at}" if at else "")
                    return (f"No editable block matching {sel.strip()} on {ds}. "
                            f"Blocks that day: {avail}.")
                for a in hits:
                    self._all_acts.remove(a)
                save_all_activities(self._all_acts)
                self._refresh_view()
                return "Deleted: " + ", ".join(
                    f"'{a['title']}' {fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}"
                    for a in hits)

            if name == "move_block":
                title = args.get("title")
                at    = args.get("at")
                if not (title and str(title).strip()) and not at:
                    return "Error: identify the block by 'title' and/or its time ('at')."
                try:
                    hits = self._select_acts(ds, title, at)
                except ValueError as ex:
                    return f"Error: {ex}"
                if not hits:
                    avail = ", ".join(f"'{a['title']}' {fmt_time(a['startMin'])}"
                                      for a in sorted(self._day_acts(),
                                                      key=lambda x: x["startMin"])) or "none"
                    return (f"No editable block matching that on {ds}. "
                            f"Blocks that day: {avail}.")
                if len(hits) > 1:
                    listing = "; ".join(f"'{a['title']}' at {fmt_time(a['startMin'])}"
                                        for a in sorted(hits, key=lambda x: x["startMin"])[:5])
                    return (f"Ambiguous — {len(hits)} blocks match: {listing}. "
                            f"Add 'at' with the exact start time to pick one.")
                a = hits[0]
                orig = (a["startMin"], a["endMin"], a.get("date"), a.get("title"))
                old_dur = a["endMin"] - a["startMin"]
                if args.get("start"):
                    a["startMin"] = parse_hhmm(str(args["start"]))
                    if not args.get("end"):   # only start given → keep the duration
                        a["endMin"] = min(a["startMin"] + old_dur, core.DAY_END)
                if args.get("end"):
                    a["endMin"] = coerce_end_min(a["startMin"], parse_hhmm(str(args["end"])))
                if args.get("new_date"):
                    nd = resolve_date(args["new_date"], self._cur_date)
                    if nd is None:
                        return f"Error: couldn't understand new_date '{args['new_date']}'."
                    a["date"] = nd
                if args.get("new_title"):
                    a["title"] = str(args["new_title"]).strip()
                if a["endMin"] <= a["startMin"]:
                    a["endMin"] = min(a["startMin"] + 60, core.DAY_END)
                # Keep it conflict-free: if the requested slot overlaps another block or a
                # meeting, relocate to the nearest free slot (like add_block) rather than
                # leaving an overlap. Revert cleanly if the day has no room at all.
                dur = a["endMin"] - a["startMin"]
                day_blocks = [b for b in self._all_acts
                              if b is not a and b.get("date") == a["date"]] + \
                             timed_cal_events(self._cal_by_date.get(a["date"], []))
                placed = find_free_placement(day_blocks, a["startMin"], dur)
                if placed is None:
                    a["startMin"], a["endMin"], a["date"], a["title"] = orig
                    return (f"Error: no free {fmt_dur(dur)} slot on {a['date']} to move "
                            f"'{a['title']}' into — that day is full. Free something up first, "
                            f"or use replace_day to rebuild it.")
                note = ""
                if placed != a["startMin"]:
                    note = (f" ({fmt_time(a['startMin'])} was taken — placed at the nearest "
                            f"free slot instead.)")
                a["startMin"], a["endMin"] = placed, placed + dur
                save_all_activities(self._all_acts)
                self._refresh_view()
                return (f"Moved '{a['title']}' to {a['date']}, "
                        f"{fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}.{note}")

            # ── WHOLE DAY — clear / copy / shift / replace / recur ──────────
            if name == "clear_day":
                n = sum(1 for a in self._all_acts if a.get("date") == ds)
                if not n:
                    return f"Nothing editable on {ds} to clear."
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds]
                save_all_activities(self._all_acts)
                self._refresh_view()
                return f"Cleared {n} block(s) from {ds}."

            if name == "copy_day":
                src = resolve_date(args.get("from_date"), self._cur_date)
                dst = resolve_date(args.get("to_date"), self._cur_date)
                if src is None or dst is None:
                    return ("Error: couldn't understand the date(s). Use Month/Day "
                            "like 6/14, or 'tomorrow'.")
                if src == dst:
                    return "Error: source and target dates are the same."
                source = [a for a in self._all_acts if a.get("date") == src]
                if not source:
                    return f"Nothing editable on {src} to copy."
                merge = bool(args.get("merge"))
                copies = [{
                    "id": new_id(), "date": dst,
                    "startMin": a["startMin"], "endMin": a["endMin"],
                    "type": a["type"], "color": a["color"], "title": a["title"],
                } for a in source]
                # Either way, push the copies off the target day's read-only calendar
                # events so they never land on a meeting (merge also keeps existing blocks).
                if merge:
                    kept = [a for a in self._all_acts if a.get("date") == dst]
                    laid, n_adj, n_drop = sequentialize(kept + copies, blocked=self._cal_intervals(dst))
                    adj_note = "shifted to avoid overlaps"   # could be a meeting OR a kept block
                else:
                    laid, n_adj, n_drop = sequentialize(copies, blocked=self._cal_intervals(dst))
                    adj_note = "shifted to clear a meeting"  # copies-only, so only a meeting shifts them
                self._all_acts = [a for a in self._all_acts if a.get("date") != dst] + laid
                note = (f" ({n_adj} {adj_note}.)" if n_adj else "")
                if n_drop:
                    note += f" ({n_drop} didn't fit the day and were dropped.)"
                save_all_activities(self._all_acts)
                self._refresh_view()
                return f"Copied {len(copies)} block(s) from {src} to {dst}.{note}"

            if name == "shift_blocks":
                mins = 0
                try:
                    if args.get("minutes") not in (None, ""):
                        mins += int(float(args["minutes"]))
                    if args.get("hours") not in (None, ""):
                        mins += 60 * int(float(args["hours"]))
                except (TypeError, ValueError):
                    return "Error: 'minutes' must be a number (positive = later, negative = earlier)."
                if not mins:
                    return "Error: give 'minutes' — positive = later, negative = earlier (120 = 2h later)."
                acts = [a for a in self._all_acts if a.get("date") == ds]
                if not acts:
                    return f"No editable blocks on {ds} to shift."
                for a in acts:
                    dur = a["endMin"] - a["startMin"]
                    ns  = max(core.DAY_START, min(a["startMin"] + mins, core.DAY_END - dur))
                    a["startMin"], a["endMin"] = ns, ns + dur
                # clamping at the day edges can pile blocks up — de-overlap the result,
                # and keep blocks off any calendar meetings
                fixed, n_adj, n_drop = sequentialize(acts, blocked=self._cal_intervals(ds))
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + fixed
                save_all_activities(self._all_acts)
                self._refresh_view()
                direction = "later" if mins > 0 else "earlier"
                out = f"Shifted {len(fixed)} block(s) on {ds} {abs(mins)} minutes {direction}."
                if n_adj:
                    out += f" ({n_adj} adjusted at the day edges.)"
                if n_drop:
                    out += f" ({n_drop} dropped — no longer fit in the day.)"
                return out

            if name == "replace_day":
                raw = args.get("blocks")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        return "Error: 'blocks' must be a list of {start, end, title, type}."
                if not isinstance(raw, list) or not raw:
                    return "Error: 'blocks' must be a non-empty list of {start, end, title, type}."
                new_acts, skipped = [], 0
                for b in raw:
                    try:
                        sm = parse_hhmm(str(b["start"]))
                        em = coerce_end_min(sm, parse_hhmm(str(b["end"])))
                        if em <= sm:
                            raise ValueError("end before start")
                        tid = str(b.get("type", "study"))
                        at  = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                        new_acts.append({
                            "id": new_id(), "date": ds, "startMin": sm, "endMin": em,
                            "type": at["id"], "color": at["color"],
                            "title": str(b.get("title") or at["label"]),
                        })
                    except Exception:
                        skipped += 1
                if not new_acts:
                    return "Error: none of the blocks were valid (need start, end as 24h HH:MM, title)."
                new_acts, n_adj, n_drop = sequentialize(new_acts, blocked=self._cal_intervals(ds))
                if not new_acts:
                    return "Error: the blocks don't fit within the day (00:00–24:00)."
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + new_acts
                save_all_activities(self._all_acts)
                self._refresh_view()
                lines = ", ".join(f"'{a['title']}' {fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}"
                                  for a in new_acts)
                out = f"Replaced {ds} with {len(new_acts)} blocks: {lines}."
                if n_adj:
                    out += f" ({n_adj} shifted to remove overlaps.)"
                if n_drop:
                    out += f" ({n_drop} dropped — didn't fit before 24:00.)"
                if skipped:
                    out += f" ({skipped} invalid block(s) skipped.)"
                return out

            if name == "add_recurring":
                sm = parse_hhmm(str(args["start"]))
                em = coerce_end_min(sm, parse_hhmm(str(args["end"])))
                if em <= sm:
                    return "Error: end must be after start (use 24:00 for end of day)."
                tid = str(args.get("type", "study"))
                at_t = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                title = str(args.get("title") or at_t["label"])
                targets = []
                if args.get("dates"):
                    for d in args["dates"]:
                        rd = resolve_date(d, self._cur_date)
                        if rd:
                            targets.append(rd)
                elif args.get("weekdays"):
                    wanted = set()
                    for w in args["weekdays"]:
                        wl = str(w).strip().lower()
                        if wl in ("weekday", "weekdays"):
                            wanted |= {0, 1, 2, 3, 4}
                        elif wl in ("weekend", "weekends"):
                            wanted |= {5, 6}
                        elif wl in ("daily", "everyday", "every day", "all"):
                            wanted |= set(range(7))
                        elif wl in _WEEKDAYS:
                            wanted.add(_WEEKDAYS[wl])
                    if not wanted:
                        return "Error: couldn't read 'weekdays'."
                    try:
                        weeks = max(1, min(8, int(args.get("weeks", 1))))
                    except (TypeError, ValueError):
                        weeks = 1
                    for i in range(7 * weeks):
                        d = self._cur_date + timedelta(days=i)
                        if d.weekday() in wanted:
                            targets.append(d.isoformat())
                else:
                    return "Error: give 'weekdays' (e.g. ['monday']) or a 'dates' list."
                targets = sorted(set(targets))[:60]
                if not targets:
                    return "Error: no matching dates."
                conflicts = []
                for tds in targets:
                    if any(b["startMin"] < em and b["endMin"] > sm
                           for b in self._all_acts if b.get("date") == tds):
                        conflicts.append(tds)
                    self._all_acts.append({
                        "id": new_id(), "date": tds, "startMin": sm, "endMin": em,
                        "type": at_t["id"], "color": at_t["color"], "title": title,
                    })
                save_all_activities(self._all_acts)
                self._refresh_view()
                out = (f"Added '{title}' {fmt_time(sm)}–{fmt_time(em)} on {len(targets)} "
                       f"day(s): {', '.join(targets)}.")
                if conflicts:
                    out += f" Note: overlaps existing blocks on {', '.join(conflicts)}."
                return out

            if name == "clear_range":
                rs = parse_hhmm(str(args["start"]))
                re_ = parse_hhmm(str(args["end"]))
                if re_ <= rs:
                    return "Error: end must be after start."
                hits = [a for a in self._all_acts if a.get("date") == ds
                        and a["startMin"] < re_ and a["endMin"] > rs]
                if not hits:
                    return f"Nothing editable between {fmt_time(rs)}–{fmt_time(re_)} on {ds}."
                for a in hits:
                    self._all_acts.remove(a)
                save_all_activities(self._all_acts)
                self._refresh_view()
                return (f"Cleared {len(hits)} block(s) in {fmt_time(rs)}–{fmt_time(re_)} on "
                        f"{ds}: " + ", ".join(f"'{a['title']}'" for a in hits))

            # ── READ-ONLY — free time ───────────────────────────────────────
            if name == "find_free_time":
                after  = parse_hhmm(str(args["after"]))  if args.get("after")  else core.DAY_START
                before = parse_hhmm(str(args["before"])) if args.get("before") else core.DAY_END
                dur = 0
                if args.get("duration") not in (None, ""):
                    try:
                        dur = int(float(args["duration"]))
                    except (TypeError, ValueError):
                        return "Error: 'duration' must be a number of minutes."
                gaps = self._free_gaps(ds, after, before)
                if dur:
                    gaps = [(s, e) for s, e in gaps if e - s >= dur]
                if not gaps:
                    return (f"No free {('≥ ' + fmt_dur(dur) + ' ') if dur else ''}slots on "
                            f"{ds}{(' between ' + fmt_time(after) + '–' + fmt_time(before)) if (args.get('after') or args.get('before')) else ''}.")
                return (f"Free time on {ds}: " +
                        ", ".join(f"{fmt_time(s)}–{fmt_time(e)} ({fmt_dur(e - s)})"
                                  for s, e in gaps))

            # ── SINGLE BLOCK — pomodoro split ───────────────────────────────
            if name == "split_block":
                hits = self._select_acts(ds, args.get("title"), args.get("at"))
                if not hits:
                    avail = ", ".join(f"'{a['title']}' {fmt_time(a['startMin'])}"
                                      for a in sorted(self._day_acts(),
                                                      key=lambda x: x["startMin"])) or "none"
                    return f"No block matching that on {ds}. Blocks: {avail}."
                if len(hits) > 1:
                    listing = "; ".join(f"'{a['title']}' at {fmt_time(a['startMin'])}"
                                        for a in sorted(hits, key=lambda x: x["startMin"])[:5])
                    return f"Ambiguous — {len(hits)} match: {listing}. Add 'at' to pick one."
                a = hits[0]
                try:
                    chunk = max(5, int(args.get("chunk", 30)))
                except (TypeError, ValueError):
                    chunk = 30
                try:
                    brk = max(0, int(args.get("break", 5)))
                except (TypeError, ValueError):
                    brk = 5
                # Breaks are downtime, not a continuation of the work block — give them their
                # own category (default free = rest) instead of inheriting the type.
                btid = str(args.get("break_type") or "free")
                b_at = next((t for t in ACTIVITY_TYPES if t["id"] == btid), None)
                if b_at is None:
                    b_at = next((t for t in ACTIVITY_TYPES if t["id"] == "free"), ACTIVITY_TYPES[0])
                s0, e0 = a["startMin"], a["endMin"]
                segs, cur = [], s0
                while cur < e0:
                    cend = min(cur + chunk, e0)
                    segs.append(("chunk", cur, cend)); cur = cend
                    if cur < e0 and brk > 0:
                        bend = min(cur + brk, e0)
                        segs.append(("break", cur, bend)); cur = bend
                while segs and segs[-1][0] == "break":   # no trailing break
                    segs.pop()
                n_chunks = sum(1 for k, _, _ in segs if k == "chunk")
                if n_chunks < 2:
                    return (f"'{a['title']}' ({fmt_dur(e0 - s0)}) is too short to split into "
                            f"{chunk}-min chunks.")
                self._all_acts.remove(a)
                ci = 0
                for kind, ss, ee in segs:
                    if kind == "chunk":
                        ci += 1
                        self._all_acts.append({
                            "id": new_id(), "date": ds, "startMin": ss, "endMin": ee,
                            "type": a["type"], "color": a["color"],
                            "title": f"{a['title']} ({ci})"})
                    else:   # breaks are downtime — their own category, not the work block's
                        self._all_acts.append({
                            "id": new_id(), "date": ds, "startMin": ss, "endMin": ee,
                            "type": b_at["id"], "color": b_at["color"], "title": "Break"})
                save_all_activities(self._all_acts)
                self._refresh_view()
                return (f"Split '{a['title']}' into {n_chunks} × {chunk}-min chunks"
                        f"{f' with {brk}-min breaks' if brk else ''}.")

            # ── PLANNERS — deterministic layout, never overlaps ─────────────
            if name == "schedule_tasks":
                raw = args.get("tasks")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        return "Error: 'tasks' must be a list of {title, minutes, ...}."
                if not isinstance(raw, list) or not raw:
                    return "Error: give a non-empty 'tasks' list."
                ws = (parse_hhmm(str(args["day_start"])) if args.get("day_start")
                      else parse_hhmm(self._settings.get("plan_day_start", "08:00")))
                we = (parse_hhmm(str(args["day_end"]))   if args.get("day_end")
                      else parse_hhmm(self._settings.get("plan_day_end", "22:00")))
                # Planning today with no explicit start → don't place tasks in the past.
                if ds == date.today().isoformat() and not args.get("day_start"):
                    ws = max(ws, datetime.now().hour * 60 + datetime.now().minute)
                if we <= ws:
                    we = core.DAY_END
                windows = {"morning": (8*60, 12*60), "afternoon": (12*60, 17*60),
                           "evening": (17*60, 22*60), "night": (20*60, 24*60)}
                prio = {"high": 0, "urgent": 0, "important": 0, "normal": 1,
                        "medium": 1, "low": 2}
                tasks = []
                for i, t in enumerate(raw[:20]):
                    if not isinstance(t, dict):
                        continue
                    try:
                        mins = int(float(t.get("minutes") or t.get("duration") or 60))
                    except (TypeError, ValueError):
                        mins = 60
                    want = mins
                    mins = max(15, min(mins, we - ws))
                    tid = str(t.get("type", "study"))
                    at_t = next((x for x in ACTIVITY_TYPES if x["id"] == tid), ACTIVITY_TYPES[0])
                    tasks.append({
                        "title": str(t.get("title") or at_t["label"]), "mins": mins,
                        "type": at_t["id"], "color": at_t["color"],
                        "pr": prio.get(str(t.get("priority", "normal")).lower(), 1),
                        "prefer": str(t.get("prefer", "")).strip().lower(), "i": i,
                        "clamped": mins < want,
                    })
                if not tasks:
                    return "Error: no valid tasks."
                tasks.sort(key=lambda x: (x["pr"], x["i"]))
                occ = [(a["startMin"], a["endMin"]) for a in self._all_acts if a.get("date") == ds] + \
                      [(e["startMin"], e["endMin"])
                       for e in timed_cal_events(self._cal_by_date.get(ds, []))]
                # idempotent: don't re-add a task already on the day (repeat calls are safe)
                have = {norm_title(a["title"]) for a in self._all_acts if a.get("date") == ds}
                placed, unplaced, already, shortened = [], [], [], []
                for t in tasks:
                    if norm_title(t["title"]) in have:
                        already.append(t["title"]); continue
                    ranges = []
                    if t["prefer"] in windows:
                        pw = windows[t["prefer"]]
                        ranges.append((max(ws, pw[0]), min(we, pw[1])))
                    elif t["prefer"]:
                        try:
                            ps = parse_hhmm(t["prefer"]); ranges.append((max(ws, ps), we))
                        except ValueError:
                            pass
                    ranges.append((ws, we))   # fallback: whole waking window
                    slot = None
                    for a0, b0 in ranges:
                        if b0 - a0 < t["mins"]:
                            continue
                        for gs, ge in _free_slots(occ, a0, b0):
                            if ge - gs >= t["mins"]:
                                slot = (gs, gs + t["mins"]); break
                        if slot:
                            break
                    if slot:
                        occ.append(slot)
                        have.add(norm_title(t["title"]))
                        self._all_acts.append({
                            "id": new_id(), "date": ds, "startMin": slot[0], "endMin": slot[1],
                            "type": t["type"], "color": t["color"], "title": t["title"]})
                        placed.append((t["title"], slot))
                        if t.get("clamped"):
                            shortened.append(t["title"])
                    else:
                        unplaced.append(t["title"])
                if not placed:
                    if already and not unplaced:
                        return ("Those are already on {}'s schedule — nothing to add."
                                .format(ds))
                    return ("Couldn't fit any task in the free time on {} ({}–{}). Try a wider "
                            "window or shorter tasks.".format(ds, fmt_time(ws), fmt_time(we)))
                save_all_activities(self._all_acts)
                self._refresh_view()
                placed.sort(key=lambda x: x[1][0])
                out = "Scheduled on {}: ".format(ds) + ", ".join(
                    f"'{ti}' {fmt_time(s)}–{fmt_time(e)}" for ti, (s, e) in placed)
                if already:
                    out += " | Already there: " + ", ".join(already)
                if unplaced:
                    out += " | Couldn't fit (no free slot): " + ", ".join(unplaced)
                if shortened:
                    out += (f" | Shortened to fit the {fmt_time(ws)}–{fmt_time(we)} "
                            f"window: " + ", ".join(shortened))
                return out

            if name == "plan_day":
                raw_tasks = args.get("tasks")
                if isinstance(raw_tasks, str):
                    try: raw_tasks = json.loads(raw_tasks)
                    except Exception: return "Error: 'tasks' must be a list of {title, minutes, …}."
                if not isinstance(raw_tasks, list) or not raw_tasks:
                    return "Error: give a non-empty ordered 'tasks' list."
                raw_fixed = args.get("fixed") or []
                if isinstance(raw_fixed, str):
                    try: raw_fixed = json.loads(raw_fixed)
                    except Exception: raw_fixed = []

                def _atype(tid, default):
                    return next((t for t in ACTIVITY_TYPES if t["id"] == str(tid)),
                                next(t for t in ACTIVITY_TYPES if t["id"] == default))

                ws = (parse_hhmm(str(args["start"])) if args.get("start")
                      else parse_hhmm(self._settings.get("plan_day_start", "08:00")))
                if ds == date.today().isoformat() and not args.get("start"):
                    ws = max(ws, datetime.now().hour * 60 + datetime.now().minute)

                # Fixed anchors first; they (and calendar events) are obstacles tasks flow around.
                new_blocks, occ = [], list(self._cal_intervals(ds))
                for f in (raw_fixed if isinstance(raw_fixed, list) else []):
                    if not isinstance(f, dict) or not f.get("start"):
                        continue
                    try:
                        fs = parse_hhmm(str(f["start"]))
                    except ValueError:
                        continue
                    try:
                        fe = parse_hhmm(str(f["end"])) if f.get("end") else fs + max(5, int(f.get("minutes", 60)))
                    except (TypeError, ValueError):
                        fe = fs + 60
                    fe = min(fe, core.DAY_END)
                    if fe <= fs:
                        continue
                    at_f = _atype(f.get("type", "extra"), "extra")
                    new_blocks.append({"id": new_id(), "date": ds, "startMin": fs, "endMin": fe,
                                       "type": at_f["id"], "color": at_f["color"],
                                       "title": str(f.get("title") or at_f["label"])})
                    occ.append((fs, fe))

                # Ordered tasks: each gets its full focus time, split into chunks with breaks,
                # flowing past anchors/meetings. Breaks do NOT count toward a task's minutes.
                brk_t = _atype("free", "free")
                cursor, unplaced = ws, []
                for t in raw_tasks[:12]:
                    if not isinstance(t, dict):
                        continue
                    try: total = max(5, int(float(t.get("minutes") or 60)))
                    except (TypeError, ValueError): total = 60
                    at_t = _atype(t.get("type", "study"), "study")
                    try: chunk = max(5, int(t["chunk"])) if t.get("chunk") else total
                    except (TypeError, ValueError): chunk = total
                    chunk = min(chunk, total)
                    try: brk = max(0, int(t.get("break", 15 if chunk < total else 0)))
                    except (TypeError, ValueError): brk = 15 if chunk < total else 0
                    n_chunks = -(-total // chunk)
                    left = total
                    idx = 0
                    while left > 0:
                        clen = min(chunk, left)
                        slot = _earliest_fit(occ, cursor, clen)
                        if slot is None:
                            unplaced.append(str(t.get("title") or at_t["label"])); break
                        idx += 1
                        ttl = (f"{t.get('title') or at_t['label']} ({idx})"
                               if n_chunks > 1 else str(t.get("title") or at_t["label"]))
                        new_blocks.append({"id": new_id(), "date": ds, "startMin": slot,
                                           "endMin": slot + clen, "type": at_t["id"],
                                           "color": at_t["color"], "title": ttl})
                        occ.append((slot, slot + clen)); cursor = slot + clen; left -= clen
                        if left > 0 and brk > 0:
                            bslot = _earliest_fit(occ, cursor, brk)
                            if bslot == cursor:   # only a contiguous break (skip if an anchor butts up)
                                new_blocks.append({"id": new_id(), "date": ds, "startMin": bslot,
                                                   "endMin": bslot + brk, "type": brk_t["id"],
                                                   "color": brk_t["color"], "title": "Break"})
                                occ.append((bslot, bslot + brk)); cursor = bslot + brk

                if not new_blocks:
                    return "Error: couldn't place anything — check the start time and task minutes."
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + new_blocks
                save_all_activities(self._all_acts)
                self._refresh_view()
                lines = ", ".join(f"'{b['title']}' {fmt_time(b['startMin'])}–{fmt_time(b['endMin'])}"
                                  for b in sorted(new_blocks, key=lambda x: x["startMin"]))
                out = f"Planned {ds}: {lines}."
                if unplaced:
                    out += " | Couldn't fully fit: " + ", ".join(dict.fromkeys(unplaced))
                return out

            if name == "make_room":
                t = args.get("title")
                if not (t and str(t).strip()):
                    return "Error: give the appointment a 'title'."
                try:
                    es = parse_hhmm(str(args["start"])); ee = parse_hhmm(str(args["end"]))
                except (KeyError, ValueError):
                    return "Error: give the appointment 'start' and 'end' as 24h HH:MM."
                if ee <= es:
                    return "Error: the appointment's end must be after its start."
                try: bb = max(0, int(args.get("buffer_before", 0) or 0))
                except (TypeError, ValueError): bb = 0
                try: ba = max(0, int(args.get("buffer_after", 0) or 0))
                except (TypeError, ValueError): ba = 0
                tid  = str(args.get("type", "extra"))
                at_e = next((x for x in ACTIVITY_TYPES if x["id"] == tid),
                            next(x for x in ACTIVITY_TYPES if x["id"] == "extra"))
                brk_t = next((x for x in ACTIVITY_TYPES if x["id"] == "free"), ACTIVITY_TYPES[0])
                # Resolve any pinned blocks (kept exactly where they are).
                pin_args = args.get("pin") or []
                if isinstance(pin_args, str):
                    pin_args = [pin_args]
                pinned, pinned_ids = [], set()
                for p in (pin_args if isinstance(pin_args, list) else []):
                    ptitle = p.get("title") if isinstance(p, dict) else p
                    pat    = p.get("at") if isinstance(p, dict) else None
                    pn = norm_title(ptitle) if ptitle else None
                    try:
                        atm = parse_hhmm(str(pat)) if pat else None
                    except ValueError:
                        atm = None
                    for a in self._all_acts:
                        if a.get("date") != ds or a["id"] in pinned_ids:
                            continue
                        # EXACT title match for pinning, so e.g. pinning 'Workout/Break'
                        # doesn't also catch the plain 'Break' blocks (fuzzy would).
                        if pn is not None and norm_title(a.get("title", "")) != pn:
                            continue
                        if atm is not None and a["startMin"] != atm:
                            continue
                        pinned.append(a); pinned_ids.add(a["id"])
                # Fixed set = appointment (+ buffer Breaks) + pinned + calendar; everything else
                # keeps its order/duration and is shifted to flow around it.
                appt = {"id": new_id(), "date": ds, "startMin": es, "endMin": ee,
                        "type": at_e["id"], "color": at_e["color"], "title": str(t).strip()}
                new_fixed, win_s, win_e = [appt], es, ee
                if bb > 0:
                    win_s = max(core.DAY_START, es - bb)
                    new_fixed.append({"id": new_id(), "date": ds, "startMin": win_s, "endMin": es,
                                      "type": brk_t["id"], "color": brk_t["color"], "title": "Break"})
                if ba > 0:
                    win_e = min(core.DAY_END, ee + ba)
                    new_fixed.append({"id": new_id(), "date": ds, "startMin": ee, "endMin": win_e,
                                      "type": brk_t["id"], "color": brk_t["color"], "title": "Break"})
                # Reflow: blocks entirely before the appointment stay put; one straddling its
                # start is shrunk to end there; everything from the appointment onward ripples
                # after it, flowing past pinned blocks + meetings. Overflow shrinks the tail
                # rather than dropping it, so nothing is lost.
                all_obs = [(win_s, win_e)] + self._cal_intervals(ds) + \
                          [(p["startMin"], p["endMin"]) for p in pinned]
                movers = sorted([a for a in self._all_acts
                                 if a.get("date") == ds and a["id"] not in pinned_ids],
                                key=lambda x: x["startMin"])
                kept_movers, after, n_shrunk, n_drop = [], [], 0, 0
                for b in movers:
                    if b["endMin"] <= win_s:
                        kept_movers.append(b)                                  # before — unchanged
                    elif b["startMin"] < win_s and win_s - b["startMin"] >= 5:
                        kept_movers.append({**b, "endMin": win_s}); n_shrunk += 1  # straddler — trim
                    else:
                        after.append(b)                                        # ripple after the appt
                cursor = win_e
                for b in after:
                    dur = b["endMin"] - b["startMin"]
                    slot = _earliest_fit(all_obs, cursor, dur)
                    if slot is not None:
                        kept_movers.append({**b, "startMin": slot, "endMin": slot + dur})
                        cursor = slot + dur
                        continue
                    gap = next(((gs, ge) for gs, ge in _free_slots(all_obs, cursor, core.DAY_END)
                                if ge - gs >= 5), None)
                    if gap:
                        gs, ge = gap
                        clen = min(dur, ge - gs)
                        kept_movers.append({**b, "startMin": gs, "endMin": gs + clen}); n_shrunk += 1
                        cursor = gs + clen
                    else:
                        n_drop += 1
                kept = new_fixed + pinned + kept_movers
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + kept
                save_all_activities(self._all_acts)
                self._refresh_view()
                lines = ", ".join(f"'{b['title']}' {fmt_time(b['startMin'])}–{fmt_time(b['endMin'])}"
                                  for b in sorted(kept, key=lambda x: x["startMin"]))
                out = (f"Added '{appt['title']}' {fmt_time(es)}–{fmt_time(ee)} on {ds} and shifted "
                       f"the rest around it: {lines}.")
                if pinned:
                    out += " Kept fixed: " + ", ".join(dict.fromkeys(p["title"] for p in pinned)) + "."
                if n_shrunk:
                    out += f" ({n_shrunk} block(s) shrunk to fit.)"
                if n_drop:
                    out += f" ({n_drop} couldn't fit even shrunk — remove or shorten something.)"
                return out

            # ── READ-ONLY — day listing + CONFLICTS (the verify signal) ─────
            if name == "list_blocks":
                cal_all = self._cal_by_date.get(ds, [])
                cal_ad  = allday_cal_events(cal_all)
                cal     = sorted(timed_cal_events(cal_all), key=lambda x: x["startMin"])
                day_acts = sorted([x for x in self._all_acts if x.get("date") == ds],
                                  key=lambda x: x["startMin"])
                lines = [f"[calendar all-day] {ev['title']}" for ev in cal_ad]
                lines += [f"[calendar] {ev['title']}: {fmt_time(ev['startMin'])}–{fmt_time(ev['endMin'])}"
                          for ev in cal]
                lines += [f"[{a['type']}] {a['title']}: {fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}"
                          for a in day_acts]
                if not lines:
                    return f"Nothing scheduled on {ds}."
                # Conflict scan (timed cal only — all-day never occupies minutes).
                conflicts = []
                for i, a in enumerate(day_acts):
                    for ev in cal:
                        if a["startMin"] < ev["endMin"] and a["endMin"] > ev["startMin"]:
                            conflicts.append(
                                f"'{a['title']}' ({fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}) "
                                f"overlaps calendar event '{ev['title']}' "
                                f"({fmt_time(ev['startMin'])}–{fmt_time(ev['endMin'])})")
                    for b in day_acts[i + 1:]:
                        if a["startMin"] < b["endMin"] and a["endMin"] > b["startMin"]:
                            conflicts.append(
                                f"'{a['title']}' and '{b['title']}' overlap "
                                f"near {fmt_time(max(a['startMin'], b['startMin']))}")
                out = f"Schedule for {ds}:\n" + "\n".join(lines)
                if conflicts:
                    out += ("\nCONFLICTS — fix these, then re-check:\n"
                            + "\n".join(f"  - {c}" for c in conflicts))
                else:
                    out += "\nNo conflicts: nothing overlaps and no block sits on a meeting."
                return out

            # ── PLANNERS — recover a slipped day / spread to a deadline ─────
            if name == "reflow_from_now":
                try:
                    delay = int(float(args.get("minutes")))
                except (TypeError, ValueError):
                    return ("Error: 'minutes' must be a number (how far to push upcoming "
                            "blocks; positive = later, negative = earlier).")
                if delay == 0:
                    return "Error: give a non-zero 'minutes' (positive = later, negative = earlier)."
                if args.get("from"):
                    try:
                        cutoff = parse_hhmm(str(args["from"]))
                    except ValueError:
                        return "Error: couldn't read 'from' — use 24h HH:MM."
                elif ds == date.today().isoformat():
                    cutoff = datetime.now().hour * 60 + datetime.now().minute
                else:
                    cutoff = core.DAY_START
                movers = [a for a in self._all_acts
                          if a.get("date") == ds and a["startMin"] >= cutoff]
                if not movers:
                    return f"No blocks starting at or after {fmt_time(cutoff)} on {ds} to reflow."
                for a in movers:
                    dur = a["endMin"] - a["startMin"]
                    ns  = max(core.DAY_START, min(a["startMin"] + delay, core.DAY_END - dur))
                    a["startMin"], a["endMin"] = ns, ns + dur
                day = [a for a in self._all_acts if a.get("date") == ds]
                fixed, n_adj, n_drop = sequentialize(day, blocked=self._cal_intervals(ds))
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + fixed
                save_all_activities(self._all_acts)
                self._refresh_view()
                direction = "later" if delay > 0 else "earlier"
                out = (f"Reflowed {len(movers)} upcoming block(s) on {ds} {abs(delay)} min "
                       f"{direction} (from {fmt_time(cutoff)}).")
                if n_drop:
                    out += f" ({n_drop} no longer fit and were dropped.)"
                return out

            if name == "plan_for_deadline":
                title = str(args.get("title") or "").strip()
                if not title:
                    return "Error: give a 'title' for the work."
                dd = resolve_date(args.get("deadline"), self._cur_date)
                if dd is None:
                    return ("Error: couldn't understand 'deadline' — use a date like 6/20 "
                            "or a weekday like 'friday'.")
                try:
                    total = int(float(args.get("minutes") or args.get("total_minutes") or 0))
                except (TypeError, ValueError):
                    total = 0
                if total <= 0:
                    return "Error: give 'minutes' = the total time the whole job needs."
                try:
                    sess = max(15, int(float(args.get("session", 60))))
                except (TypeError, ValueError):
                    sess = 60
                tid  = str(args.get("type", "study"))
                at_t = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                start_iso = resolve_date(args.get("start_date"), self._cur_date) or date.today().isoformat()
                start    = max(date.fromisoformat(start_iso), date.today())
                deadline = date.fromisoformat(dd)
                days, d = [], start
                while d < deadline:               # days strictly before the deadline
                    days.append(d); d += timedelta(days=1)
                if not days and deadline >= date.today():
                    days = [deadline]             # deadline is today → use the day itself
                if not days:
                    return f"Error: the deadline {dd} has already passed."
                full, rem = divmod(total, sess)   # split total into daily sessions
                sizes = [sess] * full
                if rem >= 15:
                    sizes.append(rem)
                elif rem and sizes:
                    sizes[-1] += rem
                if not sizes:
                    sizes = [total]
                ws = parse_hhmm(self._settings.get("plan_day_start", "08:00"))
                we = parse_hhmm(self._settings.get("plan_day_end", "22:00"))
                placed, skipped, already, di = [], [], [], 0
                for k, length in enumerate(sizes, 1):
                    stitle, done = f"{title} ({k}/{len(sizes)})", False
                    for _ in range(len(days)):
                        day_d = days[di % len(days)]; di += 1
                        dstr  = day_d.isoformat()
                        have  = {norm_title(a["title"]) for a in self._all_acts if a.get("date") == dstr}
                        if norm_title(stitle) in have:
                            already.append(stitle); done = True; break
                        lo = ws
                        if dstr == date.today().isoformat():
                            lo = max(ws, datetime.now().hour * 60 + datetime.now().minute)
                        occ = [(a["startMin"], a["endMin"]) for a in self._all_acts if a.get("date") == dstr] + \
                              [(e["startMin"], e["endMin"])
                               for e in timed_cal_events(self._cal_by_date.get(dstr, []))]
                        slot = None
                        for gs, ge in _free_slots(occ, lo, we):
                            if ge - gs >= length:
                                slot = (gs, gs + length); break
                        if slot:
                            self._all_acts.append({
                                "id": new_id(), "date": dstr, "startMin": slot[0], "endMin": slot[1],
                                "type": at_t["id"], "color": at_t["color"], "title": stitle})
                            placed.append((dstr, slot)); done = True; break
                    if not done:
                        skipped.append(stitle)
                if not placed and already:
                    return f"All {len(already)} session(s) for '{title}' are already planned before {dd}."
                if not placed:
                    return (f"Couldn't fit any session for '{title}' before {dd} within "
                            f"{fmt_time(ws)}–{fmt_time(we)}. Try shorter sessions or a wider window.")
                save_all_activities(self._all_acts)
                self._refresh_view()
                placed.sort(key=lambda x: (x[0], x[1][0]))
                out = (f"Planned '{title}' for {dd}: {len(placed)} session(s) — " +
                       ", ".join(f"{dstr} {fmt_time(s)}–{fmt_time(e)}" for dstr, (s, e) in placed))
                if already:
                    out += f" | {len(already)} already there"
                if skipped:
                    out += f" | couldn't fit {len(skipped)} (no free slot before the deadline)"
                return out

            # ── READ-ONLY — weekly totals ───────────────────────────────────
            if name == "week_summary":
                if args.get("start") or args.get("end"):
                    s = resolve_date(args.get("start"), self._cur_date) or self._cur_date.isoformat()
                    e = resolve_date(args.get("end"), self._cur_date) or s
                else:
                    monday = self._cur_date - timedelta(days=self._cur_date.weekday())
                    s = monday.isoformat()
                    e = (monday + timedelta(days=6)).isoformat()
                if e < s:
                    s, e = e, s
                ndays = (date.fromisoformat(e) - date.fromisoformat(s)).days + 1
                totals = {}
                for a in self._all_acts:
                    if s <= a.get("date", "") <= e:
                        totals[a["type"]] = totals.get(a["type"], 0) + (a["endMin"] - a["startMin"])
                for dstr, evs in self._cal_by_date.items():
                    if s <= dstr <= e:
                        for ev in evs:
                            totals["calendar"] = totals.get("calendar", 0) + (ev["endMin"] - ev["startMin"])
                if not totals:
                    return f"Nothing scheduled between {s} and {e}."
                labels = {t["id"]: t["label"] for t in ACTIVITY_TYPES}
                labels["calendar"] = "Calendar"
                parts = [f"{labels.get(k, k)} {fmt_dur(v)} (~{fmt_dur(v // ndays)}/day)"
                         for k, v in sorted(totals.items(), key=lambda x: -x[1])]
                return f"{s} → {e} ({ndays} days): " + "; ".join(parts)

            return f"Unknown tool '{name}'."
        except KeyError as ex:
            return f"Error: missing argument {ex}."
        except ValueError as ex:
            return f"Error: {ex}"
        except Exception as ex:
            return f"Error: {ex}"

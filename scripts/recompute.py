#!/usr/bin/env python3
"""Recompute every number of the page "Does AI drain your Mac battery?" from the public data set alone.

    /usr/bin/python3 scripts/recompute.py [section ...]
    sections: minutes minutes-abba window-vs-answer window-vs-answer-reading window-vs-textedit
              window-vs-textedit-reading agents cursor window terminals threads browsers ollama nights leftovers
              idle ollama-idle k wattmate
    (no argument: all of them)

Reads only ../data (relative to this file); Python 3.9 standard library, nothing else. Every number is
formatted exactly as the results file ../results/ai-battery-usage-mac.csv prints it, and
scripts/check.py compares the two row by row. The rows of the results file that data/ cannot give (most of the
comparison of WattMate with the lab's instruments) are named, with the reason, by NOT_RECOMPUTED and
not_recomputed() below.

Words. A *series* is one lab session on one Mac (a folder in data/runs/). A *run* is one stretch of it
under one condition (a row of arms.csv): `control` (screen on, nothing else running), `chatgptstream-front`
(ChatGPT answering in front) and so on. Each run has `arm_start` (the condition starts), `measure_start`
(after a warm-up), `measure_end` and `arm_stop`, all Unix seconds, UTC.

Instruments and the rules applied to them (the same rules as the lab's own scripts):

- chip (chip.csv, powermetrics, one snapshot per ~5 s): the "chip" figure is CPU + GPU energy (the Neural
  Engine is reported separately and is not part of it). A snapshot belongs to a time window when its
  midpoint, t_end - seconds / 2, lies inside the window. Watts = energy of the selected snapshots divided
  by their summed duration. "Above control" subtracts the mean chip watts of all `control` runs of the
  same series (energy-weighted: total control energy / total control seconds).
- charge counter (battery.csv, `charge_counter_mah`, the battery's own AppleRawCurrentCapacity, one tick
  per ~10 s): forward-filled over ticks where it is missing and read at the last tick at or before each
  window bound. As a sensitivity check it is also read at the first tick at or after each bound ("one tick
  later"). Mean voltage = mean of the forward-filled voltage over the ticks inside the window.
- whole Mac (battery.csv): SystemPowerIn - pack voltage x pack current, per tick; a tick counts only when
  all three readings are present in it and the result lies within [1, 180] W. The window value is the mean
  over the ticks inside the window. `whole_mac_w` is the same per-tick value rounded to 1 mW; this script
  recomputes it from the three integer columns so that no rounding enters the mean.
- process energy (subject.csv, processes.csv): kernel per-process energy of a named group of processes
  (the measured app or agent, ChatGPT's renderer, WattMate itself), as an increment per tick. For a window,
  a0 is the first tick at or after its start and a1 the last tick at or before its end; the increments of
  the ticks in (a0, a1] are summed and divided by a1 - a0.
- coalition energy (coalitions.csv): the kernel's energy per app coalition, as an increment per tick. For a
  window, a0 / a1 are the last ticks at or before its bounds; the increments in (a0, a1] over a1 - a0.
- answer audit: chip watts over the 140 s after a prompt was sent, rounded to 0.01 W:
  >= 0.25 W answered, 0.15-0.25 W unsure, < 0.15 W no answer. From 13 October the bytes ChatGPT received
  after each prompt are logged instead (app-answers.csv; the first prompt of a run is not judged).
- nights (data/nights/*/events.csv, from `pmset -g log`): asleep = sum of the Sleep durations (each cut at
  the end of the night) over the length of the night; dark wakes = DarkWake lines.
- day (the results file's `day` column): the UTC day of the series' start, or the span of UTC days its runs
  cover, as each section says. A night is dated by its span: a night in data/nights/ from its start to its
  end, and a lab night split into consecutive series (`ai-night-1`, `ai-night-2` on one Mac) from the start
  of the first part to the end of the last, on each part.

Runs the lab dropped are listed in each series' meta.json (`dropped_runs`, with the reason; an entry with
`messages` drops only those messages of the runs whose name starts with `arm`); they stay in the tables,
flagged, so the reader sees what was dropped and why. Prompts without an answer in the browser runs and
Cursor tasks that left the file untouched are flagged by this script from the data itself. The rules that
the lab wrote down before the data of a series (thresholds, reference series) are constants below.

Besides the tables, some sections print summary lines (lines starting with "- " or a sentence under the
table); they are derived from the same numbers and are not in the results file.
"""
import csv
import json
import os
import sys
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
RUNS = os.path.join(DATA, "runs")
NIGHTS = os.path.join(DATA, "nights")

BATTERY_MINUTES_INSTRUMENT = "charge counter (AppleRawCurrentCapacity), V x I, powermetrics chip, process energy"
COUNTER_INSTRUMENT = "charge counter (AppleRawCurrentCapacity)"
# Section key -> (section name and instrument as the results file spells them).
SECTIONS = OrderedDict([
    ("minutes", ("battery minutes: how much faster the battery drains than with the screen alone",
                 BATTERY_MINUTES_INSTRUMENT)),
    ("minutes-abba", ("battery minutes in ABBA order: the ChatGPT window in front and hidden, two repeats each",
                      "charge counter (AppleRawCurrentCapacity), V x I, powermetrics chip where present, process "
                      "energy, bytes received by ChatGPT (single nettop snapshots)")),
    ("window-vs-answer", ("the ChatGPT window in front: a finished answer on screen against answers streaming in it",
                          "charge counter (AppleRawCurrentCapacity), V x I, powermetrics chip, process energy, "
                          "bytes received by ChatGPT (single nettop snapshots)")),
    ("window-vs-answer-reading", ("the ChatGPT window in front without answers: the reading by the rule written "
                                  "before the data (LAB_DESIGN 17.29)", "")),
    ("window-vs-textedit", ("the ChatGPT window in front against a TextEdit window of the same size, both showing a "
                            "finished essay", BATTERY_MINUTES_INSTRUMENT)),
    ("window-vs-textedit-reading", ("the ChatGPT window against a TextEdit window: the reading by the rule written "
                                    "before the data (LAB_DESIGN 17.30)", "")),
    ("agents", ("agents on a build task (C project, the agent runs make test itself)",
                "powermetrics chip CPU+GPU over the whole run minus control, per task")),
    ("cursor", ("Cursor agent vs Claude Code on the same small edit",
                "powermetrics chip CPU+GPU above control, -10 to +60 s around sending")),
    ("window", ("app window while an answer streams: front, behind another window, hidden, minimised",
                "powermetrics chip CPU+GPU above control, 8-min runs, ABBA, mean of two repeats")),
    ("terminals", ("terminal running Claude Code (Sonnet)", "powermetrics chip CPU+GPU above control")),
    ("threads", ("long thread: 12 essay prompts in one chat, window in front",
                 "powermetrics chip CPU+GPU, 140 s after each message")),
    ("browsers", ("browser tab vs app, window in front, 3 prompts in 8 min", "powermetrics chip CPU+GPU")),
    ("ollama", ("local model: Ollama 0.40.0, llama3.2:3b and llama3.1:8b, answer after answer",
                "powermetrics chip and V x I whole machine")),
    ("nights", ("night with an AI app open vs a clean night, the Mac falls asleep by itself", "pmset -g log")),
    ("leftovers", ("after the agent is closed mid-answer, 30 min", "powermetrics chip CPU+GPU above control")),
    ("idle", ("app open and idle, 10 min", "powermetrics chip CPU+GPU above control")),
    ("ollama-idle", ("model loaded and waiting (Ollama llama3.1:8b, keep_alive -1) and the server without a model",
                     "V x I whole machine (SystemPowerIn), one snapshot every 60 s")),
    ("k", ("WattMate's rate k: battery joules per joule of the per-process counter, a known load switched on and off",
           "V x I whole machine against the kernel's coalition energy counters (ri_energy, with exited children)")),
    ("wattmate", ("WattMate against the lab instruments: the runs where it ran next to them",
                  "WattMate's own database (90-second windows) against the lab: V x I and the charge counter for the "
                  "whole Mac, the per-process sampler (kernel energy counter) for apps")),
])

# The comparison of WattMate with the lab's instruments (section "wattmate") ran in three places: the last run of the
# M3 Max's ABBA series, with WattMate open (series, run, the run's label in the results file); the M1's series
# 20261013T170744Z-agents15-recon-battery, which is not in data/; and the Release check of 15 October (LAB_DESIGN
# 17.31), two series in data/ whose meta.json names the section. The first two series' meta.json do not name it.
WATTMATE_RUNS = [("20261013T214438Z-m3-minutes-m3", "chatgptstream-front-wattmate", "front, WattMate open")]
WATTMATE_UNPUBLISHED_SERIES = "20261013T170744Z-agents15-recon-battery"
WATTMATE_SCREEN = "screen alone (the series' control runs), W"
# Of that comparison's numbers, those that need only data/. The rest are read over WattMate's own 90-second
# windows (their bounds and contents are in its database) or from the collector's full per-process log; data/
# carries neither.
WATTMATE_FROM_DATA = {WATTMATE_SCREEN}

# The Release check of 15 October (LAB_DESIGN 17.31, rules written before the data): a Release build of WattMate open
# in every run, controls included; 30-minute runs on battery in the order control, front, hidden, hidden, front,
# control. Its run labels, and the label of its difference rows (the mean of the valid front runs minus the mean of
# the valid hidden runs).
RELEASE_NAMES = {"control": "control", "chatgptstream-front": "front", "chatgptstream-hidden": "hidden"}
RELEASE_DIFF = "in front minus hidden: the mean of the two runs each (Release build, LAB_DESIGN 17.31)"
RELEASE_ANSWERS = "answers by bytes received: yes/unsure/no"
RELEASE_RENDER = "ChatGPT's renderer (Codex (Renderer)), kernel counter (our sampler), W"
RELEASE_VALID = "valid by the answer rule and the renderer rule written before the data"
RELEASE_DVXI = "whole Mac by V x I, W"
RELEASE_DCW = "whole Mac by the charge counter, W"
# Of the Release check's numbers, those that need only data/ (the lab instruments). The rest are WattMate's own
# records over its 90-second windows: its whole Mac against V x I in each run, its ChatGPT row, Screen and system and
# the other app rows, V x I over those same windows, and the shares built on them.
RELEASE_FROM_DATA = {RELEASE_ANSWERS, RELEASE_RENDER, RELEASE_VALID, RELEASE_DVXI, RELEASE_DCW}
MARK_RELEASE_COUNTER = ("a run left out of this difference: its two charge-counter readings differ by more than 10 "
                        "points (LAB_DESIGN 17.26)")

# Rows of the results file this script does not recompute, by reason; check.py counts them apart. Which rows each
# reason covers: not_recomputed() below.
NOT_RECOMPUTED = OrderedDict([
    ("wattmate-database",
     "WattMate against the lab instruments, the M3 Max run: computed from WattMate's own database (it records every "
     "app on the Mac; most rows are read over its 90-second windows) or from the collector's full per-process log; "
     "neither is published"),
    ("wattmate-series",
     "WattMate against the lab instruments, the M1 run: computed from WattMate's own database and from a series not in "
     "data/ (%s); neither is published" % WATTMATE_UNPUBLISHED_SERIES),
    ("wattmate-release",
     "WattMate against the lab instruments, the Release check of 15 October: WattMate's own records over its 90-second "
     "windows (its whole Mac against V x I in each run, its ChatGPT row, Screen and system and the other app rows, V x I "
     "over the same windows, and the shares built on them) need WattMate's own database, which records every app on "
     "the Mac and is not published"),
])


# How many rows of the results file each reason covers; check.py fails when it finds more or fewer.
NOT_RECOMPUTED_ROWS = OrderedDict([("wattmate-database", 26), ("wattmate-series", 28), ("wattmate-release", 28)])
# The same rows pinned as they stand: SHA-256 of their series, stand, power, day, run, metric, value and note (tab
# between columns, one row per line, in file order). data/ cannot give their values; the digest only makes check.py
# fail when one of them is changed, renamed, moved to another Mac or swapped for another.
NOT_RECOMPUTED_COLUMNS = ("series", "stand", "power", "day", "run", "metric", "value", "note")
NOT_RECOMPUTED_SHA256 = OrderedDict([
    ("wattmate-database", "991f7caede2f899e2b123e64d995e5bc916a6e44d27275d36a214c95f8559fdb"),
    ("wattmate-series", "23db685464f972bea142b820e486ba8b195d4b4e084aeebaeff4bc71fab92a8f"),
    ("wattmate-release", "430e0db4858e89344d61b637faad3be712e766a124ebb6c690aa6e4d5a3e273c"),
])

_RELEASE_IDS = []  # the series of the Release check, read once from data/ (meta.json names the section)


def release_series_ids() -> set:
    if not _RELEASE_IDS:
        _RELEASE_IDS.append({s.id for s in section_series("wattmate")})
    return _RELEASE_IDS[0]


def not_recomputed(row: Dict[str, str]) -> str:
    """The NOT_RECOMPUTED reason of a results-file row this script does not recompute, or "" for one it does."""
    if row["section"] != SECTIONS["wattmate"][0]:
        return ""
    if row["series"] == WATTMATE_UNPUBLISHED_SERIES:
        return "wattmate-series"
    if any(row["series"] == sid and row["run"] == label for sid, _name, label in WATTMATE_RUNS) \
            and row["metric"] not in WATTMATE_FROM_DATA:
        return "wattmate-database"
    if row["series"] in release_series_ids() and row["metric"] not in RELEASE_FROM_DATA:
        return "wattmate-release"
    return ""


CURSOR_UNTOUCHED = "file untouched after the task, probably a Cursor usage limit; not checked by eye"
# Claude on the 60 Hz M1 answers cheaply (0.16-0.22 W per prompt in the app and in a tab alike), so on that
# machine an "unsure" Claude prompt (0.15-0.25 W) is an answer. Used by the browsers note and the window summary.
CHEAP_CLAUDE_MACHINE = "M1, macOS 26"
# The author's working Mac (ABBA series of 13 and 14 October only); the results file names it so.
AUTHORS_MAC = "M3 Max, macOS 27"

# Rules the lab wrote down before the data (LAB_DESIGN 17.26, 17.28 to 17.30).
# Bytes ChatGPT received after a prompt: answered / no answer. On the author's Mac ChatGPT receives about
# 68,664 bytes in 145 s with nothing asked, hence the higher bounds there.
BYTES_YES, BYTES_NO_BELOW = 20000, 5000          # test Macs: >= yes, < no
BYTES_YES_M3, BYTES_NO_AT_MOST_M3 = 110000, 80000  # author's Mac: >= yes, <= no
# The evening repeat on the M1 of 14 October was judged also by ChatGPT's renderer and the GPU (17.29 lesson, 17.30).
WVA_STRICT = {"20261014T145413Z-window-vs-answer"}
# Reference of the reading: "front minus control" of the valid answering runs in front, 11 to 14 October, same Mac.
WVA_REF = {"M1 Pro, macOS 27": ["20261011T102709Z-minutes-battery", "20261012T100629Z-minutes-chatgpt",
                                "20261013T131301Z-minutes-abba"],
           "M1, macOS 26": ["20261011T141657Z-minutes-battery"]}
# Ollama series whose runs are long enough (30 min) for the charge counter.
OLLAMA_COUNTER = {"20261013T130028Z-ollama-counter"}
ARM_LABELS = {"yes2": "two yes processes: plain CPU load, no window"}

MARK_SHAKY = ("the two charge-counter readings (last tick before the boundaries, first tick after) differ by more "
              "than 10 points: read this run by V x I (rule written before the data)")
MARK_ABBA_ANSWERS = ("failed the answer rule: fewer than 10 of the 11 judged prompts answered by bytes received (rule "
                     "written before the data); kept in the file, left out of the page's ranges")
MARK_WVA_ANSWERS = ("failed the answer rule: fewer than 11 of the 13 judged prompts answered by bytes received (rule "
                    "written before the data, LAB_DESIGN 17.29); kept in the file, left out of the reading")
MARK_STATIC_SENT = ("failed the static rule: not exactly one prompt sent, before the measured window, with the window "
                    "in front (rule written before the data, LAB_DESIGN 17.29); kept in the file, left out of the "
                    "reading")
MARK_STATIC_RENDER = ("failed the static rule: ChatGPT's renderer used 0.05 W or less in the warm-up, so the answer was "
                      "not drawn (rule written before the data, LAB_DESIGN 17.29 lesson and 17.30); kept in the file, "
                      "left out of the reading")
MARK_STATIC_GPU = ("failed the static rule: the GPU drew 0.2 W or more in the measured window, a sign of a constant "
                   "animation (rule written before the data, LAB_DESIGN 17.30); kept in the file, left out of the "
                   "reading")
MARK_ANSWER_RENDER = ("failed the answer rule: ChatGPT's renderer used under 0.1 W, so the window was not drawing the "
                      "answers (rule written before the data, LAB_DESIGN 17.29 lesson); kept in the file, left out of "
                      "the reading")
MARK_TEXTEDIT = ("failed the TextEdit rule: the window frame was not read from ChatGPT's window, or TextEdit was not "
                 "in front at the end (rule written before the data, LAB_DESIGN 17.30); kept in the file, left out of "
                 "the reading")


# ---------------------------------------------------------------- reading the data set

def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def num(s: str) -> Optional[float]:
    return float(s) if s not in ("", None) else None


def inum(s: str) -> Optional[int]:
    return int(s) if s not in ("", None) else None


def stand_label(machine: str) -> str:
    """The machine as the results file's `stand` column names it."""
    return machine + " (the author's working Mac)" if machine == AUTHORS_MAC else machine


class Run:
    """One row of arms.csv: name, index and the event times that are present."""

    def __init__(self, row: Dict[str, str]):
        self.name = row["arm"]
        self.index = int(row["index"])
        self.ev = {k: float(row[k]) for k in ("arm_start", "measure_start", "measure_end", "arm_stop")
                   if row.get(k, "") != ""}

    def __getitem__(self, k: str) -> float:
        return self.ev[k]


class Chip:
    """CPU+GPU from powermetrics snapshots: energy (J) and mean power (W) over a window."""

    def __init__(self, rows: List[Dict[str, str]]):
        self.pm = [(float(r["t_end"]), float(r["seconds"]), float(r["cpu_mj"]), float(r["gpu_mj"]),
                    float(r["ane_mj"])) for r in rows]

    def _sel(self, t0: float, t1: float):
        return [x for x in self.pm if t0 <= x[0] - x[1] / 2 <= t1]

    def energy(self, t0: float, t1: float) -> Tuple[float, float]:
        sel = self._sel(t0, t1)
        return sum(x[2] + x[3] for x in sel) / 1000.0, sum(x[1] for x in sel)

    def watts(self, t0: float, t1: float) -> Optional[float]:
        e, d = self.energy(t0, t1)
        return e / d if d else None

    def split(self, t0: float, t1: float) -> Tuple[float, float]:
        sel = self._sel(t0, t1)
        d = sum(x[1] for x in sel) or 1
        return sum(x[2] for x in sel) / 1000.0 / d, sum(x[3] for x in sel) / 1000.0 / d


class Tick:
    __slots__ = ("t", "src", "counter", "percent", "volt", "amp", "spi")

    def __init__(self, r: Dict[str, str]):
        self.t = float(r["t"])
        self.src = r["power_source"]
        self.counter = inum(r["charge_counter_mah"])
        self.percent = inum(r["percent"])
        self.volt = inum(r["voltage_mv"])
        self.amp = inum(r["pack_current_ma"])
        self.spi = inum(r["system_power_in_mw"])
        # whole_mac_w is the same per-tick value rounded to 1 mW; the mean below is taken over the unrounded
        # value, and the column is only checked against it.
        w, col = whole_mac_watts(self), r.get("whole_mac_w", "")
        if (col == "") != (w is None) or (w is not None and abs(w - float(col)) > 0.0005 + 1e-9):
            raise SystemExit("battery.csv, tick %s: whole_mac_w %r disagrees with SystemPowerIn - V x I (%r)"
                             % (r["t"], col, w))


class Series:
    """One folder of data/runs/."""

    def __init__(self, sid: str):
        self.id = sid
        self.dir = os.path.join(RUNS, sid)
        with open(os.path.join(self.dir, "meta.json")) as f:
            self.meta = json.load(f)
        self.machine = self.meta["machine"]
        self.stand = stand_label(self.machine)
        self.day = "%s-%s-%s" % (sid[0:4], sid[4:6], sid[6:8])
        self.dropped = {}  # run name -> reason
        self.dropped_messages = []  # (run-name prefix, first message, last message, reason)
        for d in self.meta.get("dropped_runs", []):
            if d.get("messages"):
                first, _, last = d["messages"].partition("-")
                self.dropped_messages.append((d["arm"], int(first), int(last or first), d["reason"]))
            else:
                self.dropped[d["arm"]] = d["reason"]
        self.runs = [Run(r) for r in read_csv(os.path.join(self.dir, "arms.csv"))]
        self._chip = self._ticks = self._subject = self._processes = self._coalitions = self._journal = None

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def log(self, name: str) -> List[List[str]]:
        """A run log (app-stream, ollama, ...) as rows of cells, like the lab reads its tab-separated logs.
        The exporter pads short rows with empty cells up to the header width; they are trimmed back off."""
        p = self.path(name)
        if not os.path.exists(p):
            return []
        with open(p, newline="") as f:
            rows = list(csv.reader(f))[1:]
        out = []
        for r in rows:
            while r and r[-1] == "":
                r = r[:-1]
            if r:
                out.append(r)
        return out

    def has(self, name: str) -> bool:
        return os.path.exists(self.path(name))

    @property
    def chip(self) -> Optional[Chip]:
        """None for a series without powermetrics (the author's Mac)."""
        if self._chip is None and self.has("chip.csv"):
            self._chip = Chip(read_csv(self.path("chip.csv")))
        return self._chip

    @property
    def ticks(self) -> List[Tick]:
        if self._ticks is None:
            self._ticks = [Tick(r) for r in read_csv(self.path("battery.csv"))]
        return self._ticks

    @property
    def journal(self) -> List[Dict[str, str]]:
        if self._journal is None:
            self._journal = read_csv(self.path("journal.csv"))
        return self._journal

    def capacities(self) -> List:
        """The full-charge capacity the battery reports in each journal row (fcc_mah, else raw_max_mah)."""
        out = []
        for j in self.journal:
            c = j["fcc_mah"] if num(j["fcc_mah"]) else j["raw_max_mah"]
            if num(c):
                out.append(num_text(c))
        return out

    @property
    def subject(self) -> Dict[Tuple[str, int], Dict[float, Tuple[str, float]]]:
        """(run name, index) -> {tick time: (subject, mJ since the previous tick)}."""
        if self._subject is None:
            self._subject = {}
            if self.has("subject.csv"):
                for r in read_csv(self.path("subject.csv")):
                    self._subject.setdefault((r["arm"], int(r["index"])), {})[float(r["t"])] = (
                        r["subject"], float(r["energy_mj_since_previous_tick"]))
        return self._subject

    @property
    def processes(self) -> Dict[Tuple[str, int, str], Dict[float, float]]:
        """(run name, index, process group) -> {tick time: mJ since the previous tick}."""
        if self._processes is None:
            self._processes = {}
            if self.has("processes.csv"):
                for r in read_csv(self.path("processes.csv")):
                    self._processes.setdefault((r["arm"], int(r["index"]), r["process"]), {})[float(r["t"])] = \
                        float(r["energy_mj_since_previous_tick"])
        return self._processes

    @property
    def coalitions(self) -> List[Tuple[float, str, float]]:
        """(tick time, coalition, mJ since the previous tick), only the ticks where it grew."""
        if self._coalitions is None:
            self._coalitions = [(float(r["t"]), r["coalition"], float(r["energy_mj_since_previous_tick"]))
                                for r in read_csv(self.path("coalitions.csv"))]
        return self._coalitions

    def message_note(self, run_name: str, message: int) -> str:
        """The drop reason of message `message` (1-based) of this run, or ""."""
        for prefix, first, last, reason in self.dropped_messages:
            if (run_name == prefix or run_name.startswith(prefix + "-")) and first <= message <= last:
                return "not used: " + reason
        return ""

    def measured(self, name: str) -> List[Run]:
        return [r for r in self.runs if r.name == name and "measure_end" in r.ev]

    def power(self, runs: Optional[List[Run]] = None) -> str:
        """cable / battery from the power source of the ticks inside the measured windows."""
        runs = [r for r in (runs if runs is not None else self.runs) if "measure_end" in r.ev]
        srcs = {tk.src for tk in self.ticks for r in runs if r["measure_start"] <= tk.t <= r["measure_end"]}
        if srcs == {"AC"}:
            return "cable"
        if srcs == {"Battery"}:
            return "battery"
        return "mixed" if srcs else ""

    def span_day(self) -> str:
        """The UTC day, or days, of the series' runs."""
        return day_span(min(r["arm_start"] for r in self.runs if "arm_start" in r.ev),
                        max(r.ev.get("arm_stop", r.ev.get("measure_end", 0)) for r in self.runs))


_SERIES = {}


def series(sid: str) -> Series:
    if sid not in _SERIES:
        _SERIES[sid] = Series(sid)
    return _SERIES[sid]


def section_series(key: str) -> List[Series]:
    """Series whose meta.json says the page uses them in this section, in time order."""
    out = []
    for sid in sorted(os.listdir(RUNS)):
        p = os.path.join(RUNS, sid, "meta.json")
        if os.path.exists(p):
            with open(p) as f:
                if key in json.load(f).get("used_by_page_sections", []):
                    out.append(series(sid))
    return out


# ---------------------------------------------------------------- instruments

def control_watts(chip: Chip, s: Series) -> float:
    vals = [chip.energy(r["measure_start"], r["measure_end"]) for r in s.measured("control")]
    return sum(e for e, _ in vals) / sum(d for _, d in vals)


def gauge(ticks: List[Tick]):
    """(t, charge counter, voltage, percent), each forward-filled over ticks where it is missing."""
    cur = volt = soc = None
    out = []
    for tk in ticks:
        cur = tk.counter if tk.counter is not None else cur
        volt = tk.volt if tk.volt is not None else volt
        soc = tk.percent if tk.percent is not None else soc
        out.append((tk.t, cur, volt, soc))
    return out


def at(seq, t: float):
    """The last element whose time is at or before t."""
    r = None
    for x in seq:
        if x[0] <= t:
            r = x
        else:
            break
    return r


def after(seq, t: float):
    """The first element whose time is at or after t (the last one if there is none)."""
    for x in seq:
        if x[0] >= t:
            return x
    return seq[-1]


def mean_volts(g, t0: float, t1: float) -> Optional[float]:
    """Mean forward-filled voltage over the ticks inside [t0, t1], V."""
    vs = [x[2] for x in g if t0 <= x[0] <= t1 and x[2]]
    return sum(vs) / len(vs) / 1000 if vs else None


def num_text(s: str):
    """A journal value as the lab wrote it: an integer stays an integer."""
    f = float(s)
    return int(f) if f == int(f) else f


def whole_mac_watts(tk: Tick) -> Optional[float]:
    """SystemPowerIn - pack V x I (W), None outside [1, 180] W or when a reading is missing in this tick."""
    if tk.spi is None or tk.amp is None or tk.volt is None:
        return None
    load = tk.spi / 1000.0 - tk.amp * tk.volt / 1e6
    return load if 1.0 <= load <= 180.0 else None


def machine_mean(ticks: List[Tick], t0: float, t1: float) -> Optional[float]:
    vals = [w for w in (whole_mac_watts(tk) for tk in ticks if t0 <= tk.t <= t1) if w is not None]
    return sum(vals) / len(vals) if vals else None


def increments_watts(s: Series, inc: Dict[float, float], t0: float, t1: float, what: str) -> float:
    """Mean power of per-tick increments over [t0, t1]: increments of the ticks in (a0, a1] over a1 - a0,
    a0 = the first tick at or after t0, a1 = the last tick at or before t1."""
    times = [tk.t for tk in s.ticks]
    a0 = next((t for t in times if t >= t0), times[-1])
    a1 = max(t for t in times if t <= t1)
    need = [t for t in times if a0 < t <= a1]
    missing = [t for t in need if t not in inc]
    if missing:
        raise SystemExit("%s %s: lacks %d of the %d ticks in the window" % (s.id, what, len(missing), len(need)))
    return sum(inc[t] for t in need) / 1000.0 / (a1 - a0)


def subject_watts(s: Series, run: Run, t0: float, t1: float, subject: str) -> float:
    """Mean power of the processes of the app or agent the run measures (subject.csv)."""
    rows = s.subject.get((run.name, run.index), {})
    wrong = {v[0] for v in rows.values()} - {subject}
    if wrong:
        raise SystemExit("%s %s #%d: subject.csv measures %s, expected %s" % (s.id, run.name, run.index, wrong, subject))
    return increments_watts(s, {t: v[1] for t, v in rows.items()}, t0, t1,
                            "%s #%d, subject.csv" % (run.name, run.index))


def process_watts(s: Series, run: Run, t0: float, t1: float, process: str) -> float:
    """Mean power of a named process group of this run (processes.csv)."""
    return increments_watts(s, s.processes.get((run.name, run.index, process), {}), t0, t1,
                            "%s #%d, processes.csv %s" % (run.name, run.index, process))


def coalition_watts(s: Series, t0: float, t1: float, name: Optional[str] = None) -> Optional[float]:
    """Mean power of one coalition (or of all, name None) over [t0, t1]; None when it did not grow."""
    times = [tk.t for tk in s.ticks]
    a0 = max(t for t in times if t <= t0)
    a1 = max(t for t in times if t <= t1)
    e = sum(mj for t, c, mj in s.coalitions if a0 < t <= a1 and (name is None or c == name))
    return e / 1000.0 / (a1 - a0) if e > 0 else None


def prompt_watts(chip: Chip, sent: int) -> float:
    return round(chip.watts(sent, sent + 140) or 0, 2)


def answer_bands(vals: List[float]) -> str:
    yes = sum(1 for x in vals if x >= 0.25)
    unsure = sum(1 for x in vals if 0.15 <= x < 0.25)
    return "%d/%d/%d" % (yes, unsure, len(vals) - yes - unsure)


def byte_bands(s: Series, rows: List[List[str]]) -> Tuple[int, int, int]:
    """Answered / unsure / no answer by the bytes ChatGPT received after each judged prompt."""
    if s.machine == AUTHORS_MAC:
        yes = sum(1 for r in rows if int(r[3]) >= BYTES_YES_M3)
        no = sum(1 for r in rows if int(r[3]) <= BYTES_NO_AT_MOST_M3)
    else:
        yes = sum(1 for r in rows if int(r[3]) >= BYTES_YES)
        no = sum(1 for r in rows if int(r[3]) < BYTES_NO_BELOW)
    return yes, len(rows) - yes - no, no


def fmt(x: Optional[float], nd: int) -> Optional[str]:
    return None if x is None else "%.*f" % (nd, x)


def iso_day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def day_span(t0: float, t1: float) -> str:
    a, b = iso_day(t0), iso_day(t1)
    return a if a == b else "%s to %s" % (a, b)


def upward_steps(g, t_from: float = -1.0) -> List[Tuple[float, int]]:
    """(time, size) of every rise of the forward-filled charge counter, from the tick at t_from on."""
    return [(y[0], y[1] - x[1]) for x, y in zip(g, g[1:]) if x[1] is not None and y[1] is not None and y[1] > x[1]
            and x[0] >= t_from]


def list_text(xs) -> str:
    """A list of integers the way the lab prints one inside brackets."""
    return ", ".join(str(x) for x in xs)


# ---------------------------------------------------------------- output

class Table:
    def __init__(self, title: Optional[str] = None):
        self.title = title
        # (series, stand, day, run, OrderedDict(metric -> value or None), note, overrides)
        self.rows = []

    def add(self, s_id: str, stand: str, day: str, run: str, values, note: str = "", **overrides) -> None:
        """overrides: instrument or power of these numbers, when they differ from the section's."""
        self.rows.append((s_id, stand, day, run, OrderedDict(values), note, overrides))


class Section:
    def __init__(self, key: str, text: str):
        self.key = key
        self.name, self.instrument = SECTIONS[key]
        self.text = text
        self.tables = []  # type: List[Table]
        self.lines = []  # summary lines printed after the tables
        self.power = {}  # series id -> cable / battery

    def table(self, title: Optional[str] = None) -> Table:
        t = Table(title)
        self.tables.append(t)
        return t

    def records(self) -> List[Dict[str, str]]:
        """Long format, the columns of the results file: one row per number."""
        out = []
        for t in self.tables:
            for sid, stand, day, run, values, note, over in t.rows:
                for metric, value in values.items():
                    if value is None:
                        continue
                    own_note = note
                    if isinstance(value, tuple):  # (value, a note of this number only)
                        value, own_note = value[0], value[1] or note
                    out.append(OrderedDict([
                        ("section", self.name), ("instrument", over.get("instrument", self.instrument)),
                        ("power", over.get("power", self.power.get(sid, ""))),
                        ("series", sid), ("stand", stand), ("day", day), ("run", run), ("metric", metric),
                        ("value", value), ("note", own_note)]))
        return out

    def render(self) -> str:
        lines = ["## " + self.name, "", self.text, ""]
        for t in self.tables:
            if t.title:
                lines += [t.title, ""]
            # Columns in the order the rows give them: a metric a row adds goes after that row's previous one.
            metrics = []
            for row in t.rows:
                prev = None
                for m in row[4]:
                    if m not in metrics:
                        metrics.insert(metrics.index(prev) + 1 if prev in metrics else len(metrics), m)
                    prev = m
            by_instrument = any("instrument" in row[6] for row in t.rows)
            head = ["machine", "day", "run"] + metrics + ["series"] + (["instrument"] if by_instrument else [])
            lines.append("| " + " | ".join(head) + " |")
            lines.append("|" + "---|" * len(head))
            notes = []
            for sid, stand, day, run, values, note, over in t.rows:
                cells = []
                for m in metrics:
                    v = values.get(m)
                    if isinstance(v, tuple):
                        v, own = v
                        if own:
                            if own not in notes:
                                notes.append(own)
                            v += " [%d]" % (notes.index(own) + 1)
                    cells.append("-" if v is None else (v if v != "" else "dropped"))
                label = run or "-"
                if note:
                    if note not in notes:
                        notes.append(note)
                    label += " [%d]" % (notes.index(note) + 1)
                extra = [over.get("instrument", self.instrument)] if by_instrument else []
                lines.append("| " + " | ".join([stand, day or "-", label] + cells + [sid or "-"] + extra) + " |")
            lines.append("")
            for k, n in enumerate(notes):
                lines.append("[%d] %s" % (k + 1, n))
            if notes:
                lines.append("")
        if self.lines:
            lines += self.lines + [""]
        return "\n".join(lines)


def dropped_note(s: Series, run: str) -> str:
    why = s.dropped.get(run)
    return "dropped: " + why if why else ""


def pct(x: float, nd: int = 1, sign: bool = True) -> str:
    return ("%+.*f %%" if sign else "%.*f %%") % (nd, x)


# ---------------------------------------------------------------- battery minutes

def section_minutes() -> Section:
    sec = Section("minutes", (
        "On battery, 30-minute runs. Chat: a new essay prompt every ~156 s (12 per run); Codex: a build task, "
        "one after another; the build series has both agents on build tasks, in ABBA order. 'faster than screen "
        "only' = charge-counter drain between the run's measurement bounds against the mean of the two control runs "
        "(screen on, nothing running); 'less time on battery' = 1 - control / run. '% of battery per hour above "
        "screen' and 'hours from 100%' use the full-charge capacity the battery reports in the series journal (mean "
        "of fcc_mah, or raw_max_mah where fcc_mah is empty); the percent-gauge slope is printed for comparison. 'W "
        "above screen, charge counter' = drain x mean voltage / window length, minus the controls' mean; 'V x I' = "
        "whole Mac, minus the controls' mean (absolute value in its own column). 'by V x I' columns: the same "
        "comparison by V x I (faster: V x I of the run / of the controls - 1; less time: 1 - controls / run; hours: "
        "capacity x mean voltage of the run / mean V x I of the run). 'app processes' = kernel energy of the "
        "ChatGPT.app / Claude.app processes (agent runs on a task have none: the agent is a CLI). 'answers' = the "
        "per-prompt audit, yes/unsure/no. 'build tasks' = tasks that started at or after the run's start and ended "
        "by the end of its measured window. 'counter read one tick later' = the same comparison with the counter "
        "read at the first tick at or after each bound instead of the last tick at or before it: a sensitivity "
        "check, since the counter is not a clean integral of current and bounces up after a load drops."))
    main = sec.table()
    caps = sec.table("Battery capacity per series, mAh:")
    steps = sec.table("The charge counter's rises on battery, from the first measured window on:")
    for s in section_series("minutes"):
        sec.power[s.id] = s.power()
        g = gauge(s.ticks)
        chip = s.chip
        pts = [(x[1], x[3]) for x in g if x[1] is not None and x[3] is not None]
        slope = (pts[0][0] - pts[-1][0]) / (pts[0][1] - pts[-1][1]) * 100 if pts[0][1] != pts[-1][1] else None
        cap_list = s.capacities()
        full = sum(cap_list) / len(cap_list) if cap_list else slope
        prompts_all = [r for r in s.log("app-stream.csv") if "NOFRONT" not in r]
        tasks = s.log("agent-task.csv")
        rows = []
        for run in s.runs:
            if "measure_end" not in run.ev:
                continue
            t0, t1 = run["measure_start"], run["measure_end"]
            a, b = at(g, t0), at(g, t1)
            a2, b2 = after(g, t0), after(g, t1)
            volts = mean_volts(g, t0, t1)
            mah = a[1] - b[1]
            # Agent runs on a task (`*-task`) measure a CLI in a terminal, not an app: no app processes.
            subject = None if run.name.endswith("-task") else \
                "ChatGPT.app" if run.name.startswith("chatgpt") else \
                "Claude.app" if run.name.startswith("claude") else None
            appw = subject_watts(s, run, t0, t1, subject) if subject else None
            prompts = [r for r in prompts_all if run["arm_start"] - 1 <= int(r[-1]) <= t1]
            audit = answer_bands([prompt_watts(chip, int(r[-1])) for r in prompts]) if prompts else None
            done = len([x for x in tasks if run["arm_start"] - 1 <= int(x[2]) and int(x[3]) <= t1]) \
                if run.name.endswith("-loop-task") else None
            rows.append((run, mah, mah * volts / 1000 / ((t1 - t0) / 3600), machine_mean(s.ticks, t0, t1),
                         chip.split(t0, t1), appw, audit, a2[1] - b2[1], volts, done))
        ctrl = [r for r in rows if r[0].name == "control"]
        cm = sum(r[1] for r in ctrl) / len(ctrl)
        cm2 = sum(r[7] for r in ctrl) / len(ctrl)
        cw = sum(r[2] for r in ctrl) / len(ctrl)
        cv = sum(r[3] for r in ctrl) / len(ctrl)
        for run, mah, w, vxi, (cpu, gpu), appw, audit, mah2, volts, done in rows:
            ctl = run.name == "control"
            hours = full / (mah * 2) if full else None
            main.add(s.id, s.stand, s.day, run.name, [
                ("mAh drained in 30 min", "%d" % mah),
                ("faster than screen only, %", None if ctl else pct((mah / cm - 1) * 100)),
                ("faster than screen only, %, counter read one tick later (sensitivity check)",
                 None if ctl else pct((mah2 / cm2 - 1) * 100)),
                ("faster than screen only by V x I, %", None if ctl else pct((vxi / cv - 1) * 100, 2)),
                ("less time on battery than screen only, %", None if ctl else pct((1 - cm / mah) * 100, 1, False)),
                ("% of battery per hour above screen", None if ctl or not full else pct((mah - cm) * 2 / full * 100)),
                ("W above screen, charge counter (drain x V)", "%+.2f" % (w - cw)),
                ("W above screen, V x I", "%+.2f" % (vxi - cv)),
                ("whole machine V x I, W", "%.2f" % vxi),
                ("chip CPU, W", "%.2f" % cpu),
                ("chip GPU, W", "%.2f" % gpu),
                ("app processes, W (per-process kernel counter)", fmt(appw, 3)),
                ("answers yes/unsure/no", audit),
                ("hours from 100%", fmt(hours, 1)),
                ("less time on battery than screen only by V x I, %", None if ctl else pct((1 - cv / vxi) * 100, 2, False)),
                ("hours from 100% by V x I (battery-log capacity x mean voltage of the run, over mean V x I)",
                 fmt(full * volts / 1000 / vxi if full and vxi else None, 2)),
                ("build tasks the agent finished inside the run (agent task log)", None if done is None else "%d" % done),
            ], dropped_note(s, run.name))
        caps.add(s.id, s.stand, s.day, "", [
            ("battery capacity as the battery log reports it, mAh", fmt(full, 0)),
            ("battery capacity by the slope of the percent gauge, mAh", fmt(slope, 0)),
        ])
        first = min(r["measure_start"] for r in s.runs if "measure_start" in r.ev)
        ups = [d for _, d in upward_steps(g, first)]
        steps.add(s.id, s.stand, s.day, "", [
            ("upward steps of the charge counter on battery in the series", "%d" % len(ups)),
            ("largest upward step of the charge counter on battery in the series, mAh", "%d" % (max(ups) if ups else 0)),
        ], instrument=COUNTER_INSTRUMENT)
        sec.lines.append("- %s: the charge counter rose %d times, largest upward step %d mAh, total rise %d mAh."
                         % (s.id, len(ups), max(ups) if ups else 0, sum(ups)))
    sec.lines += [""] + battery_passport()
    return sec


def battery_passport() -> List[str]:
    """Per machine: battery cycles over every series of the page, and the capacity the journal reports in the
    battery-minutes series (the author's Mac, which has none: in its ABBA series)."""
    cyc, cap = OrderedDict(), OrderedDict()
    minutes = {s.id for s in section_series("minutes")} | {s.id for s in section_series("minutes-abba")
                                                          if s.machine == AUTHORS_MAC}
    for sid in sorted(os.listdir(RUNS)):
        if not os.path.exists(os.path.join(RUNS, sid, "journal.csv")):
            continue
        s = series(sid)
        for j in s.journal:
            if j["cycles"] != "":
                cyc.setdefault(s.machine, set()).add(num_text(j["cycles"]))
        if sid in minutes:
            cap.setdefault(s.machine, set()).update(s.capacities())
    out = []
    for machine in sorted(set(cyc) | set(cap)):
        c, k = sorted(cyc.get(machine, [])), sorted(cap.get(machine, []))
        out.append("- battery passport, %s: %s cycles across the page's series; capacity per the journal in the "
                   "battery-minutes series %s mAh." % (machine, "%s-%s" % (c[0], c[-1]) if c else "no data",
                                                       "%s-%s" % (k[0], k[-1]) if k else "no data"))
    return out


# ---------------------------------------------------------------- the ChatGPT window: ABBA, without answers, TextEdit

def arm_values(s: Series, parts: bool = False) -> List[dict]:
    """The runs of a battery series by the formulas of the minutes sections: drain at the bounds (last tick before),
    one tick later, minutes 5 to 30, V x I, chip, ChatGPT processes, prompts sent and judged; against the mean of
    the series' controls. parts: also ChatGPT's renderer, TextEdit and WattMate (processes.csv, 14 October)."""
    g = gauge(s.ticks)
    chip = s.chip
    sends = s.log("app-stream.csv")
    answers = s.log("app-answers.csv")
    rows = []
    for run in s.runs:
        if "measure_end" not in run.ev:
            continue
        name = run.name
        t0, t1 = run["measure_start"], run["measure_end"]
        a, b = at(g, t0), at(g, t1)
        a2, b2 = after(g, t0), after(g, t1)
        volts = mean_volts(g, t0, t1)
        mah = a[1] - b[1]
        late = at(g, t0 + 300)[1] - b[1]
        stop = run.ev.get("arm_stop", t1) + 1
        mine = [r for r in sends if run["arm_start"] - 1 <= int(r[-1]) <= stop]
        r = dict(
            name=name, run=run, s=t0, e=t1, mah=mah, mah2=a2[1] - b2[1], cw=mah * volts / 1000 / ((t1 - t0) / 3600),
            volts=volts, vxi=machine_mean(s.ticks, t0, t1), split=chip.split(t0, t1) if chip else None,
            appw=subject_watts(s, run, t0, t1, "ChatGPT.app") if name != "control" and name.startswith("chatgpt")
            else None,
            late=(late, late * volts / 1000 / ((t1 - t0 - 300) / 3600), machine_mean(s.ticks, t0 + 300, t1)),
            sent=[x for x in mine if x[3] not in ("NOFRONT", "WRONGSURFACE")],
            bad=[x for x in mine if x[3] in ("NOFRONT", "WRONGSURFACE")],
            judged=[x for x in answers if run["arm_start"] - 1 <= int(x[4]) <= stop and x[2] != "1"],
            render=None, render_warm=None, textedit=None, wattmate=None)
        if parts:
            if name.startswith("chatgpt"):
                r["render"] = process_watts(s, run, t0, t1, "Codex (Renderer)")
                # The warm-up is long enough to read only from 14 October on (240 s); under 30 s: no reading.
                if t0 - run["arm_start"] >= 30:
                    r["render_warm"] = process_watts(s, run, run["arm_start"], t0, "Codex (Renderer)")
            if name == "textedit-static":
                r["textedit"] = subject_watts(s, run, t0, t1, "TextEdit.app")
            r["wattmate"] = process_watts(s, run, t0, t1, "WattMate.app")
        rows.append(r)
    ctrl = [r for r in rows if r["name"] == "control"]
    c = {k: sum(r[k] for r in ctrl) / len(ctrl) for k in ("mah", "mah2", "cw", "vxi")}
    caps = s.capacities()
    full = sum(caps) / len(caps) if caps else None
    chip_c = [sum(r["split"]) for r in ctrl if r["split"]]
    chip_c = sum(chip_c) / len(chip_c) if chip_c else None
    cl = [sum(r["late"][k] for r in ctrl) / len(ctrl) for k in range(3)]
    for r in rows:
        r["pct"] = (r["mah"] / c["mah"] - 1) * 100
        r["pct2"] = (r["mah2"] / c["mah2"] - 1) * 100
        r["dcw"], r["dvxi"] = r["cw"] - c["cw"], r["vxi"] - c["vxi"]
        r["dlate"] = ((r["late"][0] / cl[0] - 1) * 100, r["late"][1] - cl[1], r["late"][2] - cl[2])
        r["shaky"] = r["name"] != "control" and abs(r["pct"] - r["pct2"]) > 10
        r["dchip"] = sum(r["split"]) - chip_c if r["split"] and chip_c is not None else None
        r["vpct"] = (r["vxi"] / c["vxi"] - 1) * 100
        r["less"] = (1 - c["mah"] / r["mah"]) * 100
        r["perh"] = (r["mah"] - c["mah"]) * 2 / full * 100 if full else None
        r["hours"] = full / (r["mah"] * 2) if full else None
        r["vless"] = (1 - c["vxi"] / r["vxi"]) * 100
        r["vhours"] = full * r["volts"] / 1000 / r["vxi"] if full and r["vxi"] else None
    return rows


def label_runs(rows: List[dict], names: Dict[str, str]) -> None:
    """front-1, front-2, ... in time order; a run named only once in names keeps its bare label."""
    seen = {}
    for r in rows:
        seen[r["name"]] = seen.get(r["name"], 0) + 1
        label = names.get(r["name"], r["name"])
        r["label"] = label if label.endswith("WattMate open") else "%s-%d" % (label, seen[r["name"]])


LATE = "minutes 5 to 30 of the run (sensitivity check, found after the data)"


def late_values(r: dict) -> list:
    if r["name"] == "control":
        return [("%s: mAh drained" % LATE, "%d" % r["late"][0])]
    return [("%s: faster than screen only, %%" % LATE, "%+.1f" % r["dlate"][0]),
            ("%s: W above screen, charge counter" % LATE, "%+.2f" % r["dlate"][1]),
            ("%s: W above screen, V x I" % LATE, "%+.2f" % r["dlate"][2])]


def chip_values(r: dict) -> list:
    if not r["split"]:
        return []
    return [("chip CPU, W", "%.2f" % r["split"][0]), ("chip GPU, W", "%.2f" % r["split"][1])]


def capacity_values(s: Series) -> list:
    caps = s.capacities()
    full = sum(caps) / len(caps)
    return [("battery capacity as the battery log reports it, mAh, mean over the series", "%.0f" % full),
            ("battery capacity as the battery log reports it, mAh, lowest and highest",
             "%s to %s" % (min(caps), max(caps)))]


ABBA_NAMES = {"control": "control", "chatgptstream-front": "front", "chatgptstream-hidden": "hidden",
              "chatgptstream-front-wattmate": "front, WattMate open"}


def section_minutes_abba() -> Section:
    sec = Section("minutes-abba", (
        "On battery, 30-minute runs in the order control, front, hidden, hidden, front, control; control = the "
        "screen with no app open. Columns as in the battery-minutes section. 'our runner's coalition' = the "
        "coalition the lab's runner works in (sampler, runs, nettop snapshots; the Terminal coalition of these "
        "series): what logging the bytes costs, against the controls. 'answers by bytes received' = the bytes ChatGPT "
        "received from sending a prompt to the next one, yes/unsure/no of the judged prompts (the first prompt of a "
        "run is not judged; test Macs: >= 20,000 yes, < 5,000 no; the author's Mac: >= 110,000 yes, <= 80,000 no); a "
        "run is valid with at least 10 answers. 'minutes 5 to 30' = drain and V x I without the first 5 minutes of "
        "each run, the controls likewise (found after the data: after a change of load the counter gives back the "
        "previous run's tail for 2 to 5 minutes). A run whose two counter readings differ by more than 10 points is "
        "read by V x I. Under the table: the counter's drain in each 5 minutes of the hidden runs."))
    t = sec.table()
    caps = sec.table("Battery capacity per series:")
    seg = sec.table("Charge counter drain in each 5 minutes of the measured hidden runs, mAh:")
    for s in section_series("minutes-abba"):
        sec.power[s.id] = s.power()
        day = s.span_day()
        g = gauge(s.ticks)
        rows = arm_values(s)
        label_runs(rows, ABBA_NAMES)
        runner = {}
        for r in rows:
            runner[id(r)] = coalition_watts(s, r["s"], r["e"], "Terminal")
        ctrl = [r for r in rows if r["name"] == "control"]
        cm = sum(r["mah"] for r in ctrl) / len(ctrl)
        cr = [runner[id(r)] for r in ctrl if runner[id(r)] is not None]
        cr = sum(cr) / len(cr) if cr else None
        arms = OrderedDict()
        for r in rows:
            ctl = r["name"] == "control"
            values = [("mAh drained in 30 min", "%d" % r["mah"])]
            if not ctl:
                values += [("faster than screen only, %", pct(r["pct"])),
                           ("faster than screen only, %, counter read one tick later (sensitivity check)", pct(r["pct2"]))]
            if not ctl and r["mah"] > cm:
                values.append(("less time on battery than screen only, %", pct(r["less"], 1, False)))
            values += [("W above screen, charge counter (drain x V)", "%+.2f" % r["dcw"]),
                       ("W above screen, V x I", "%+.2f" % r["dvxi"]),
                       ("whole machine V x I, W", "%.2f" % r["vxi"])]
            values += late_values(r) + chip_values(r)
            if r["appw"] is not None:
                values.append(("ChatGPT processes, W (per-process kernel counter)", "%.3f" % r["appw"]))
            run_w = runner[id(r)]
            if run_w is not None:
                values.append(("our runner's coalition (sampler, arms, nettop snapshots), W", "%.3f" % run_w))
                if not ctl and cr is not None:
                    values.append(("our runner's coalition, W above the controls", "%+.3f" % (run_w - cr)))
            marks = []
            if not ctl and r["judged"]:
                yes, unsure, no = byte_bands(s, r["judged"])
                ok = yes >= 10
                values += [("answers by bytes received: yes/unsure/no", "%d/%d/%d" % (yes, unsure, no)),
                           ("prompts judged (the first prompt of each run is not)", "%d" % len(r["judged"])),
                           ("valid by the answer rule", "yes" if ok else "no")]
                if r["shaky"]:
                    marks.append(MARK_SHAKY)
                if not ok:
                    marks.append(MARK_ABBA_ANSWERS)
            elif r["shaky"]:
                marks.append(MARK_SHAKY)
            if not ctl:
                values += [("faster than screen only by V x I, %", pct(r["vpct"], 2)),
                           ("% of battery per hour above screen", None if r["perh"] is None else pct(r["perh"]))]
            values.append(("hours from 100%", fmt(r["hours"], 1)))
            if not ctl and r["appw"]:
                values += [("whole Mac above screen over ChatGPT processes, charge counter, times",
                            "%.1f" % (r["dcw"] / r["appw"])),
                           ("whole Mac above screen over ChatGPT processes, V x I, times", "%.1f" % (r["dvxi"] / r["appw"]))]
            if not ctl:
                values.append(("less time on battery than screen only by V x I, %", pct(r["vless"], 2, False)))
            values.append(("hours from 100% by V x I (battery-log capacity x mean voltage of the run, over mean V x I)",
                           fmt(r["vhours"], 2)))
            t.add(s.id, s.stand, day, r["label"], values, "; ".join(marks) or dropped_note(s, r["name"]))
            if not ctl:
                arms.setdefault(r["name"], []).append((r["pct"], r["dvxi"]))
            if r["name"] == "chatgptstream-hidden":
                pts = [at(g, r["s"] + 300 * j) for j in range(round((r["e"] - r["s"]) / 300) + 1)]
                drains = [x[1] - y[1] for x, y in zip(pts, pts[1:])
                          if x and y and x[1] is not None and y[1] is not None]
                seg.add(s.id, s.stand, s.day, r["label"], [
                    ("charge counter drain in each 5 minutes of the measured run, mAh", list_text(drains))],
                    "; ".join(marks), instrument=COUNTER_INSTRUMENT)
        caps.add(s.id, s.stand, day, "", capacity_values(s))
        # The rule written before the data: if hidden-2 minus hidden-1 is larger than the spread of the two
        # front runs, it is the previous run's tail and hiding is read from hidden-2.
        front, hidden = arms.get("chatgptstream-front", []), arms.get("chatgptstream-hidden", [])
        if len(front) == 2 and len(hidden) == 2:
            spread, tail = abs(front[0][0] - front[1][0]), hidden[1][0] - hidden[0][0]
            sec.lines.append(
                "- %s, %s: front %+.1f / %+.1f %% (mean %+.1f), hidden %+.1f / %+.1f %% (mean %+.1f); %s."
                % (s.stand, s.id, front[0][0], front[1][0], (front[0][0] + front[1][0]) / 2, hidden[0][0],
                   hidden[1][0], (hidden[0][0] + hidden[1][0]) / 2,
                   ("order tail found (%.1f > %.1f points): hiding read from hidden-2, %+.1f %%, V x I %+.2f W"
                    % (tail, spread, hidden[1][0], hidden[1][1])) if tail > spread else
                   ("no order tail (%.1f <= %.1f points): hiding read from both repeats" % (tail, spread))))
    return sec


WVA_NAMES = {"control": "control", "chatgpt-front-static": "window static", "chatgptstream-front": "window answering"}
WVT_NAMES = {"control": "control", "chatgpt-front-static": "window static", "textedit-static": "TextEdit window"}
STATIC = "chatgpt-front-static"
STATIC_RULE = "valid by the static rule (exactly one prompt, sent before the measured window)"
STRICT_STATIC_RULE = ("valid by the static rule (exactly one prompt sent before the measured window, ChatGPT's "
                      "renderer above 0.05 W in the warm-up, GPU under 0.2 W in the measured window)")
STRICT_ANSWER_RULE = ("valid by the answer rule (at least 11 of 13 judged prompts answered by bytes received, "
                      "ChatGPT's renderer at least 0.1 W in the measured window)")


def strict_fail(r: dict) -> str:
    """The mark when a run fails the renderer and GPU rules (WVA_STRICT and the TextEdit series), else ""."""
    if r["name"] == STATIC:
        if (r["render_warm"] or 0) <= 0.05:
            return MARK_STATIC_RENDER
        if not r["split"] or r["split"][1] >= 0.2:
            return MARK_STATIC_GPU
    elif r["name"] == "chatgptstream-front" and (r["render"] or 0) < 0.1:
        return MARK_ANSWER_RENDER
    return ""


def static_sent_ok(r: dict) -> bool:
    return len(r["sent"]) == 1 and not r["bad"] and int(r["sent"][0][-1]) < r["s"]


_WVA = {}  # series id -> rows with "ok" set, shared by the table and the reading


def wva_rows(s: Series) -> List[dict]:
    """The runs of a series without answers vs with answers, judged by the rules of LAB_DESIGN 17.29."""
    if s.id in _WVA:
        return _WVA[s.id]
    rows = arm_values(s, parts=True)
    label_runs(rows, WVA_NAMES)
    for r in rows:
        name = r["name"]
        r["audit"] = None
        if name == "control":
            r["ok"], r["strict"] = "-", ""
            continue
        # The renderer and GPU rules of the evening repeat; its marks are printed even on a dropped run.
        strict = strict_fail(r) if s.id in WVA_STRICT else ""
        r["strict"] = strict
        if name in s.dropped:
            if name != STATIC:
                r["audit"] = byte_bands(s, r["judged"])
            r["ok"] = "dropped"
            continue
        if name == STATIC:
            r["ok"] = "yes" if static_sent_ok(r) and not strict else "no"
        else:
            r["audit"] = byte_bands(s, r["judged"])
            r["ok"] = "yes" if r["audit"][0] >= 11 and not r["bad"] and not strict else "no"
    _WVA[s.id] = rows
    return rows


def section_window_vs_answer() -> Section:
    sec = Section("window-vs-answer", (
        "On battery, 30-minute runs, a 240 s warm-up for every run, in the order control, static, answering, "
        "answering, static, control. Static = one prompt in the warm-up, its answer finished before the measured "
        "window, then 30 minutes of the window in front showing the finished essay; answering = 14 prompts, one every "
        "~156 s. Columns as in the ABBA section; 'prompts sent' = sends logged in the run (without NOFRONT or "
        "WRONGSURFACE); ChatGPT's renderer = its 'Codex (Renderer)' helper processes, part of ChatGPT.app; WattMate "
        "was open in every run of the M1 Pro's series. Rules written before the data (LAB_DESIGN 17.29): a static "
        "run is valid with exactly one prompt, sent before the measured window; an answering run with at least 11 "
        "of 13 judged prompts answered by bytes received; the evening repeat on the M1 also needs ChatGPT's renderer "
        "above 0.05 W in the static warm-up, the GPU under 0.2 W in the static window and the renderer at 0.1 W or "
        "more in the answering runs."))
    t = sec.table()
    for s in section_series("window-vs-answer"):
        sec.power[s.id] = s.power()
        for r in wva_rows(s):
            ctl, name = r["name"] == "control", r["name"]
            dropped = name in s.dropped
            values = [("mAh drained in 30 min", "%d" % r["mah"])]
            if not ctl:
                values += [("faster than screen only, %", pct(r["pct"])),
                           ("faster than screen only, %, counter read one tick later (sensitivity check)", pct(r["pct2"])),
                           ("faster than screen only by V x I, %", pct(r["vpct"], 2)),
                           ("less time on battery than screen only, %", pct(r["less"], 1, False)),
                           ("% of battery per hour above screen", None if r["perh"] is None else pct(r["perh"]))]
            values += [("W above screen, charge counter (drain x V)", "%+.2f" % r["dcw"]),
                       ("W above screen, V x I", "%+.2f" % r["dvxi"]),
                       ("whole machine V x I, W", "%.2f" % r["vxi"])]
            values += late_values(r) + chip_values(r)
            values += [("ChatGPT processes, W (per-process kernel counter)", fmt(r["appw"], 3)),
                       ("ChatGPT's renderer process (Codex (Renderer)), W, kernel counter", fmt(r["render"], 4)),
                       ("ChatGPT's renderer process in the warm-up, W, kernel counter",
                        fmt(r["render_warm"], 4) if name == STATIC else None),
                       ("WattMate itself, if open, mW, kernel counter", "%.2f" % (r["wattmate"] * 1000))]
            if not ctl:
                values.append(("prompts sent in the run", "%d" % len(r["sent"])))
            if r["audit"] is not None:
                values += [("answers by bytes received: yes/unsure/no", "%d/%d/%d" % r["audit"]),
                           ("prompts judged (the first prompt of each run is not)", "%d" % len(r["judged"]))]
            if not ctl:
                rule = STATIC_RULE if name == STATIC else "valid by the answer rule"
                if s.id in WVA_STRICT:
                    rule = STRICT_STATIC_RULE if name == STATIC else STRICT_ANSWER_RULE
                values.append((rule, {"yes": "yes", "no": "no", "dropped": "dropped after the data"}[r["ok"]]))
            values += [("hours from 100%", fmt(r["hours"], 1)),
                       ("less time on battery than screen only by V x I, %", None if ctl else pct(r["vless"], 2, False)),
                       ("hours from 100% by V x I (battery-log capacity x mean voltage of the run, over mean V x I)",
                        fmt(r["vhours"], 2))]
            strict = r["strict"]
            marks = [m for m in (MARK_SHAKY if r["shaky"] else "", strict,
                                 MARK_WVA_ANSWERS if r["ok"] == "no" and not strict and name != STATIC else "",
                                 MARK_STATIC_SENT if r["ok"] == "no" and not strict and name == STATIC else "") if m]
            t.add(s.id, s.stand, s.span_day(), r["label"], values, "; ".join(marks) or dropped_note(s, name))
    return sec


WVA_INSTRUMENTS = OrderedDict([
    ("dvxi", "V x I (SystemPowerIn on the cable, pack voltage x current on battery)"),
    ("dcw", "charge counter (drain in mAh x mean voltage of the run)"),
    ("dchip", "powermetrics chip CPU+GPU above control (for reference: the reading rule is for the battery instruments)")])
REF = "reference: window in front with answers minus control, valid runs of 11 to 14 October on this Mac"
WVA_READING = ("reading (share of at least 2/3: mostly the answer; at most 1/3: mostly the visible window; between: "
               "both share it)")


def wva_reference(s: Series) -> List[dict]:
    """The valid answering runs in front of the reference series of this Mac (WVA_REF)."""
    out = []
    for sid in WVA_REF.get(s.machine, []):
        ref = series(sid)
        for r in arm_values(ref):
            if r["name"] != "chatgptstream-front" or r["name"] in ref.dropped:
                continue
            if r["judged"] and byte_bands(ref, r["judged"])[0] < 10:
                continue
            out.append(r)
    return out


def section_window_vs_answer_reading() -> Section:
    sec = Section("window-vs-answer-reading", (
        "The rule written before the data (LAB_DESIGN 17.29). The main number is the answering runs above the static "
        "window, W, by each instrument. Static = the mean of the two static runs, or static run 1 when static run 2 "
        "is higher than static run 1 by more than the two answering runs differ (an order tail). Reference = 'front "
        "minus control' of the valid answering runs in front, 11 to 14 October, same Mac (mean, lowest, highest). "
        "Reading: a share of at least 2/3 = mostly the answer; at most 1/3 = mostly the visible window; between = "
        "both share it. A run marked 'read by V x I' is not read by the charge counter; invalid runs are left out. "
        "The chip is for reference only."))
    t = sec.table()
    for s in section_series("window-vs-answer"):
        sec.power[s.id] = s.power()
        rows = wva_rows(s)
        refs_all = wva_reference(s)
        st = [r for r in rows if r["name"] == STATIC]
        an = [r for r in rows if r["name"] == "chatgptstream-front"]
        for key, instrument in WVA_INSTRUMENTS.items():
            nd = 3 if key == "dchip" else 2

            def usable(r):
                return r["ok"] == "yes" and r[key] is not None and not (key == "dcw" and r["shaky"])
            stv = [r[key] if usable(r) else None for r in st]
            anv = [r[key] if usable(r) else None for r in an]
            refs = [r[key] for r in refs_all if r[key] is not None and not (key == "dcw" and r["shaky"])]
            if len(stv) != 2 or None in stv or len(anv) != 2 or None in anv or not refs:
                why = ("the runs were dropped after the data, see the note on them"
                       if any(r["name"] in s.dropped for r in st + an) else
                       "fewer than two valid runs of each kind, or no reference (for the charge counter the reference "
                       "run was marked: its two counter readings differ by more than 10 points)")
                t.add(s.id, s.stand, s.day, "", [("reading", "not readable: " + why)], instrument=instrument)
                continue
            spread, tail = abs(anv[0] - anv[1]), stv[1] - stv[0]
            static = stv[0] if tail > spread else (stv[0] + stv[1]) / 2
            ans = (anv[0] + anv[1]) / 2
            extra = ans - static
            ref = sum(refs) / len(refs)
            share = extra / ref

            def read(x):
                return "mostly the answer" if x >= 2 / 3 else "mostly the visible window" if x <= 1 / 3 else \
                    "both share it"
            edge = key != "dchip" and not (read(extra / max(refs)) == read(extra / min(refs)) == read(share))
            t.add(s.id, s.stand, s.day, "", [
                ("window static run 1, W above screen", "%+.*f" % (nd, stv[0])),
                ("window static run 2, W above screen", "%+.*f" % (nd, stv[1])),
                ("order tail: static run 2 minus static run 1, W", "%+.2f" % tail),
                ("order tail: spread of the two answering runs, W", "%.2f" % spread),
                ("order tail found (static read from run 1 if yes, from the mean of both if no)",
                 "yes" if tail > spread else "no"),
                ("window static, W above screen", "%+.2f" % static),
                ("window answering run 1, W above screen", "%+.*f" % (nd, anv[0])),
                ("window answering run 2, W above screen", "%+.*f" % (nd, anv[1])),
                ("window answering, mean of two, W above screen", "%+.2f" % ans),
                ("answers above the static window, W", "%+.2f" % extra),
                ("%s, mean, W" % REF, "%+.2f" % ref),
                ("%s, lowest, W" % REF, "%+.2f" % min(refs)),
                ("%s, highest, W" % REF, "%+.2f" % max(refs)),
                ("%s, runs" % REF, "%d" % len(refs)),
                ("answer share: answers above the static window over the reference mean", "%.2f" % share),
                ("answer share against the highest reference", "%.2f" % (extra / max(refs))),
                ("answer share against the lowest reference", "%.2f" % (extra / min(refs))),
                (WVA_READING, (read(share), "the reading differs at the edges of the reference range" if edge else "")),
            ], instrument=instrument)
    return sec


def textedit_frame(s: Series, r: dict) -> Tuple[Optional[str], bool]:
    """(the TextEdit window's frame, valid) from text-window.csv: the frame read from ChatGPT's window and
    TextEdit in front at the end of the run (LAB_DESIGN 17.30)."""
    run = r["run"]
    rows = [x for x in s.log("text-window.csv") if run["arm_start"] - 1 <= int(x[-1]) <= r["run"].ev.get(
        "arm_stop", r["e"]) + 5]
    frame = [x for x in rows if x[0] == "frame"]
    stop = [x for x in rows if x[0] == "stop_front"]
    if not frame or not stop:
        return None, False
    f = frame[-1]
    return ("%s %s,%s %sx%s, %s pt; front at the end: %s" % (f[1], f[2], f[3], f[4], f[5], f[6], stop[-1][1]),
            f[1] == "chatgpt" and stop[-1][1] == "TextEdit")


_WVT = {}


def wvt_rows(s: Series) -> List[dict]:
    """The runs of the ChatGPT window against TextEdit, judged by the rules of LAB_DESIGN 17.30."""
    if s.id in _WVT:
        return _WVT[s.id]
    rows = arm_values(s, parts=True)
    label_runs(rows, WVT_NAMES)
    for r in rows:
        r["frame"], r["marks"] = None, []
        if r["name"] == STATIC:
            why = strict_fail(r)
            sent_ok = static_sent_ok(r)
            r["marks"] = [m for m in (why, "" if sent_ok else MARK_STATIC_SENT) if m]
            r["ok"] = "yes" if sent_ok and not why else "no"
        elif r["name"] == "textedit-static":
            r["frame"], fok = textedit_frame(s, r)
            if not fok:
                r["marks"].append(MARK_TEXTEDIT)
            r["ok"] = "yes" if fok else "no"
        else:
            r["ok"] = "-"
        if r["shaky"]:
            r["marks"].insert(0, MARK_SHAKY)
    _WVT[s.id] = rows
    return rows


def section_window_vs_textedit() -> Section:
    sec = Section("window-vs-textedit", (
        "On battery, 30-minute runs, a 240 s warm-up, in the order control, ChatGPT static, TextEdit window, TextEdit "
        "window, ChatGPT static, control. ChatGPT static as in the section before; TextEdit window = in the warm-up "
        "the run reads the frame of ChatGPT's window, closes it and opens TextEdit in the same frame with a finished "
        "essay of the same length (1,427 words, 16 pt), dark system theme. Rules written before the data (LAB_DESIGN "
        "17.30): a static run is valid with one prompt sent before the measured window, ChatGPT's renderer above "
        "0.05 W in the warm-up and the GPU under 0.2 W in the measured window; a TextEdit run with the frame read "
        "from ChatGPT's window and TextEdit in front at the end. WattMate was open in every run."))
    t = sec.table()
    for s in section_series("window-vs-textedit"):
        sec.power[s.id] = s.power()
        for r in wvt_rows(s):
            ctl, name = r["name"] == "control", r["name"]
            chat = name == STATIC
            values = [("mAh drained in 30 min", "%d" % r["mah"])]
            if not ctl:
                values += [("faster than screen only, %", pct(r["pct"])),
                           ("faster than screen only, %, counter read one tick later (sensitivity check)", pct(r["pct2"])),
                           ("faster than screen only by V x I, %", pct(r["vpct"], 2))]
            values += [("W above screen, charge counter (drain x V)", "%+.2f" % r["dcw"]),
                       ("W above screen, V x I", "%+.2f" % r["dvxi"]),
                       ("whole machine V x I, W", "%.2f" % r["vxi"])]
            values += late_values(r) + chip_values(r)
            values += [("ChatGPT processes, W (per-process kernel counter)", fmt(r["appw"] if chat else None, 3)),
                       ("ChatGPT's renderer process (Codex (Renderer)), W, kernel counter",
                        fmt(r["render"] if chat else None, 4)),
                       ("ChatGPT's renderer process in the warm-up, W, kernel counter",
                        fmt(r["render_warm"] if chat else None, 4)),
                       ("TextEdit processes, W (per-process kernel counter)", fmt(r["textedit"], 4)),
                       ("window frame (source, x,y, width x height, font size; app in front at the end)", r["frame"]),
                       ("WattMate itself, if open, mW, kernel counter", "%.2f" % (r["wattmate"] * 1000))]
            if chat:
                values.append((STRICT_STATIC_RULE, r["ok"]))
            elif name == "textedit-static":
                values.append(("valid by the TextEdit rule (frame read from ChatGPT's window, TextEdit in front at "
                               "the end)", r["ok"]))
            t.add(s.id, s.stand, s.span_day(), r["label"], values, "; ".join(r["marks"]) or dropped_note(s, name))
    return sec


WVT_READING = OrderedDict([
    ("any", "any window with text of the same size costs the same: you pay for a window on screen, not for ChatGPT"),
    ("more", "the ChatGPT window costs more than an ordinary one"),
    ("part", "part is ChatGPT, part is any window")])


def section_window_vs_textedit_reading() -> Section:
    sec = Section("window-vs-textedit-reading", (
        "The rule written before the data (LAB_DESIGN 17.30). The main number is ChatGPT static minus the TextEdit "
        "window, W, pair by pair (static 1 - TextEdit 1, static 2 - TextEdit 2) and as the difference of the means. "
        "Reading: the TextEdit window above control at least 2/3 of ChatGPT static above control = any window with "
        "text of that size costs the same; at most 1/3 = the ChatGPT window costs more than an ordinary one; between "
        "= part each. V x I first, the charge counter second; a run marked 'read by V x I' is not read by the "
        "counter; invalid runs are left out. The chip is for reference only."))
    t = sec.table()
    for s in section_series("window-vs-textedit"):
        sec.power[s.id] = s.power()
        rows = wvt_rows(s)
        for key, instrument in WVA_INSTRUMENTS.items():
            nd = 3 if key == "dchip" else 2

            def val(r):
                ok = r["ok"] == "yes" and r[key] is not None and not (key == "dcw" and r["shaky"])
                return r[key] if ok else None
            st = [val(r) for r in rows if r["name"] == STATIC]
            te = [val(r) for r in rows if r["name"] == "textedit-static"]
            # An invalid run has no number: None, so its cell prints "-" and it gives no row in the results file.
            values = [("ChatGPT window static run %d, W above screen" % (k + 1), None if x is None else
                       "%+.*f" % (nd, x)) for k, x in enumerate(st)]
            values += [("TextEdit window run %d, W above screen" % (k + 1), None if x is None else
                        "%+.*f" % (nd, x)) for k, x in enumerate(te)]
            sv, tv = [x for x in st if x is not None], [x for x in te if x is not None]
            if not sv or not tv:
                values.append(("reading", "not readable: no valid run of one of the two kinds"))
                t.add(s.id, s.stand, s.day, "", values, instrument=instrument)
                continue
            pairs = [a - b for a, b in zip(st, te) if a is not None and b is not None]
            values += [("ChatGPT minus TextEdit, pair %d, W" % (k + 1), "%+.*f" % (nd, p)) for k, p in enumerate(pairs)]
            sm, tm = sum(sv) / len(sv), sum(tv) / len(tv)
            share = tm / sm if sm > 0 else None
            values += [("ChatGPT window static, mean, W above screen", "%+.*f" % (nd, sm)),
                       ("TextEdit window, mean, W above screen", "%+.*f" % (nd, tm)),
                       ("ChatGPT minus TextEdit, means, W", "%+.*f" % (nd, sm - tm)),
                       ("TextEdit window over ChatGPT window, above screen", fmt(share, 2))]
            if share is None:
                reading = "not readable: ChatGPT static is not above control"
            else:
                reading = WVT_READING["any" if share >= 2 / 3 else "more" if share <= 1 / 3 else "part"]
            few = "" if len(sv) == len(tv) == 2 else "fewer valid runs: ChatGPT static %d, TextEdit %d" % (len(sv), len(tv))
            values.append(("reading (TextEdit at least 2/3 of ChatGPT: any window costs the same; at most 1/3: the "
                           "ChatGPT window costs more; between: part each)", (reading, few)))
            t.add(s.id, s.stand, s.day, "", values, instrument=instrument)
    return sec


# ---------------------------------------------------------------- agents, Cursor

def section_agents() -> Section:
    sec = Section("agents", (
        "On cable. Chip CPU+GPU over the whole run from its start (agents start working at once, the measurement "
        "window opens after a 120 s warm-up), minus control, divided by the number of tasks started in the run. "
        "x4 = four copies at once, each in its own folder. 'minutes each task took' = end minus start of each task "
        "in the agent task log, in launch order."))
    t = sec.table()
    acc = OrderedDict()  # (agent, series) -> {"one": [J per task], "x4": [...]}, kept runs only, unrounded
    for s in section_series("agents"):
        sec.power[s.id] = s.power()
        chip = s.chip
        cw = control_watts(chip, s)
        tasks = s.log("agent-task.csv")
        for run in s.runs:
            name = run.name
            if not (name.endswith("build-task") or name.endswith("x4-task")) or "measure_end" not in run.ev:
                continue
            a, b = run["arm_start"], run["measure_end"]
            tk = [x for x in tasks if a - 1 <= int(x[2]) <= b]
            if not tk:
                continue
            e, d = chip.energy(a, b)
            per = (e - cw * d) / len(tk)
            span = (max(int(x[3]) for x in tk) - min(int(x[2]) for x in tk)) / 60
            values = [("tasks", "%d" % len(tk)), ("J per task", "%.0f" % per), ("Wh per task", "%.3f" % (per / 3600))]
            if "x4" in name:
                values.append(("all four copies done in, min", "%.1f" % span))
            else:
                values.append(("min per task", "%.1f" % (span / len(tk))))
            values.append(("minutes each task took, in launch order (agent task log)",
                           " / ".join("%.2f" % ((int(x[3]) - int(x[2])) / 60) for x in tk)))
            t.add(s.id, s.stand, s.day, name, values, dropped_note(s, name))
            if name not in s.dropped:
                kind = "x4" if "x4" in name else "one"
                acc.setdefault((name.split("-")[0], s.id), {}).setdefault(kind, []).append(per)
    # One task alone against four at once, over the series where both runs of the agent are kept.
    for agent in sorted({a for a, _ in acc}):
        pairs = [v for (a, _), v in acc.items() if a == agent and "one" in v and "x4" in v]
        one = [x for v in pairs for x in v["one"]]
        x4 = [x for v in pairs for x in v["x4"]]
        if one and x4:
            m1, m4 = sum(one) / len(one), sum(x4) / len(x4)
            sec.lines.append("- %s: one task %.1f J (n=%d), four at once %.1f J per task (n=%d), difference %+.1f %%; "
                             "series with both runs kept: %d." % (agent, m1, len(one), m4, len(x4),
                                                                  (m4 / m1 - 1) * 100, len(pairs)))
    return sec


def section_cursor() -> Section:
    sec = Section("cursor", (
        "On cable. Chip CPU+GPU above control in the window from 10 s before to 60 s after each task was sent, "
        "per task. Task: a Python file with 4 bugs, 'do not run commands'. Claude Code = `claude -p --model "
        "sonnet` without a UI, per task, two ways: chip over its whole run minus control, and over one window from 10 s "
        "before the first task's start to 60 s after the last task's start, minus control, divided by the number of "
        "tasks (the tasks run back to back, so per-task windows would overlap and count the same energy twice; the "
        "whole-run figure is within the spread of the two controls). "
        "'Cursor open and waiting' = the measurement window of the cursor-front run, minus control."))
    t = sec.table()
    ratios = []
    for s in section_series("cursor"):
        sec.power[s.id] = s.power()
        chip = s.chip
        base = control_watts(chip, s)
        log = [r for r in s.log("cursor-agent.csv") if "NOFRONT" not in r]
        res = OrderedDict()
        ok = 0
        for r in log:
            sent = int(r[2])
            e, d = chip.energy(sent - 10, sent + 60)
            done = r[4] == "0"
            ok += done
            res.setdefault(r[0], []).append(("%.0f" % (e - base * d), "" if done else CURSOR_UNTOUCHED))
        tasks = s.log("agent-task.csv")
        v = next(r for r in s.runs if r.name == "claude-task")
        e, d = chip.energy(v["arm_start"], v["measure_end"])
        claude = (e - base * d) / len(tasks)
        starts = [int(float(r[2])) for r in tasks]
        e2, d2 = chip.energy(min(starts) - 10, max(starts) + 60)
        claude_same = (e2 - base * d2) / len(tasks)
        # Ratios from the per-task values as printed (whole joules), untouched files left out.
        fr = [float(val) for val, bad in res.get("front", []) if not bad]
        hd = [float(val) for val, bad in res.get("hidden", []) if not bad]
        line = "- %s, %s: Cursor in front against Claude Code in one window %.2f-%.2f times" % (
            s.machine, s.day, min(fr) / claude_same, max(fr) / claude_same)
        if hd:
            line += "; hidden %.2f-%.2f" % (min(hd) / claude_same, max(hd) / claude_same)
        ratios.append(line + ".")
        vf = next(r for r in s.runs if r.name == "cursor-front")
        idle = chip.watts(vf["measure_start"], vf["measure_end"]) - base
        values = []
        for mode, label in (("front", "Cursor window in front, J"), ("hidden", "Cursor window hidden, J")):
            for k, cell in enumerate(res.get(mode, [])):
                values.append(("%s, task %d" % (label, k + 1), cell))
        values += [("solved by Cursor", "%d of %d" % (ok, len(log))),
                   ("Claude Code without a UI, J per task, whole run minus control", "%.1f" % claude),
                   ("Claude Code without a UI, J per task, one window from 10 s before the first task to 60 s after "
                    "the last", "%.1f" % claude_same),
                   ("Cursor open and waiting, W above control", "%+.3f" % idle)]
        t.add(s.id, s.stand, s.day, "", values)
    sec.lines += ratios
    return sec


# ---------------------------------------------------------------- the 8-minute window runs and the rest

def ane_share() -> Tuple[int, float, float, float, Tuple[float, str]]:
    """Neural Engine across the public series with chip data: (series, ANE kJ, CPU+GPU kJ, share %, peak). Every
    series behind the page counts, the two of the Release check of 15 October included (53 series)."""
    tot, n, peak = [0.0, 0.0, 0.0], 0, (0.0, "")
    for sid in sorted(os.listdir(RUNS)):
        p = os.path.join(RUNS, sid, "chip.csv")
        if not os.path.exists(p):
            continue
        n += 1
        for x in Chip(read_csv(p)).pm:
            tot[0] += x[2]
            tot[1] += x[3]
            tot[2] += x[4]
            if x[1] and x[4] / 1000 / x[1] > peak[0]:
                peak = (x[4] / 1000 / x[1], sid)
    share = tot[2] / (tot[0] + tot[1]) * 100 if tot[0] + tot[1] else 0
    return n, tot[2] / 1e6, (tot[0] + tot[1]) / 1e6, share, peak


def section_window() -> Section:
    n, ane, cpugpu, share, peak = ane_share()
    sec = Section("window", (
        "On battery, 8-minute runs, 2 prompts per run, two repeats in ABBA order. Chip CPU+GPU of the run minus "
        "the mean of the series' controls, mean of the two repeats. In brackets for 'front' and 'behind another "
        "window': the per-prompt answer audit, yes/unsure/no (a hidden or minimised window draws the answer "
        "cheaply, so the chip cannot tell an answer there).\n\n"
        "Neural Engine across the %d series of the page with chip data: %.3f kJ against %.1f kJ CPU+GPU (%.4f %%); "
        "the largest 5-second snapshot is %.2f W (%s). The page's chip figures are CPU+GPU without it."
        % (n, ane, cpugpu, share, peak[0], peak[1])))
    t = sec.table()
    removed = []  # (share of the front chip cost removed, all front prompts answered, same by the page rule)
    states = [("front", "front"), ("behind", "behind another window"), ("hidden", "hidden"),
              ("minimized", "minimised")]
    for s in section_series("window"):
        sec.power[s.id] = s.power()
        chip = s.chip
        cw = control_watts(chip, s)
        prompts = [r for r in s.log("app-stream.csv") if "NOFRONT" not in r]
        for app, label in (("chatgptstream", "ChatGPT"), ("claudestream", "Claude Desktop")):
            values = []
            for st, st_label in states:
                runs = s.measured("%s-%s" % (app, st))
                ws = [chip.watts(r["measure_start"], r["measure_end"]) for r in runs]
                values.append(("%s, chip W above control" % st_label,
                               "%+.3f" % (sum(ws) / len(ws) - cw) if ws else None))
                if st in ("front", "behind") and runs:
                    vals = [prompt_watts(chip, int(r[-1])) for r in prompts
                            if any(v["arm_start"] - 1 <= int(r[-1]) <= v["measure_end"] for v in runs)]
                    values.append(("%s, answers yes/unsure/no" % st_label, answer_bands(vals)))
            t.add(s.id, s.stand, s.day, label, values)
            fronts = s.measured(app + "-front")
            fw = [chip.watts(r["measure_start"], r["measure_end"]) for r in fronts]
            fv = [prompt_watts(chip, int(r[-1])) for r in prompts
                  if any(v["arm_start"] - 1 <= int(r[-1]) <= v["measure_end"] for v in fronts)]
            cheap = s.machine == CHEAP_CLAUDE_MACHINE and label == "Claude Desktop"
            for st in ("hidden", "minimized"):
                sw = [chip.watts(r["measure_start"], r["measure_end"]) for r in s.measured(app + "-" + st)]
                if fw and sw:
                    front, other = sum(fw) / len(fw) - cw, sum(sw) / len(sw) - cw
                    removed.append((1 - other / front, all(x >= 0.25 for x in fv),
                                    all(x >= 0.25 or (cheap and x >= 0.15) for x in fv)))
    ane_table = sec.table("Neural Engine against CPU+GPU, every series of the page with chip readings:")
    ane_table.add("", "all test Macs", "", "", [
        ("series behind the page with chip readings", "%d" % n),
        ("Neural Engine energy as a share of CPU and GPU energy, all those series, %", "%.4f" % share),
    ], instrument="powermetrics chip: Neural Engine energy against CPU+GPU energy, whole series", power="")
    if removed:
        allr = [r[0] for r in removed]
        strict = [r[0] for r in removed if r[1]]
        rule = [r[0] for r in removed if r[2]]

        def span(xs):
            return "%.1f-%.1f %%" % (min(xs) * 100, max(xs) * 100) if xs else "no pairs"
        sec.lines.append(
            "Hiding and minimising removed %s of the chip cost in front (mean %.1f %%, %d pairs); where every front "
            "prompt is an answer by the 0.25 W threshold: %s; by the page rule (for Claude on the 60 Hz M1 'unsure' "
            "is an answer): %s." % (span(allr), sum(allr) / len(allr) * 100, len(allr), span(strict), span(rule)))
    return sec


TERM_RUNS = ["shell-iterm", "shell-ghostty", "claude-idle-terminal", "claude-idle-iterm", "claude-idle-ghostty",
             "claude-stream-terminal", "claude-stream-iterm", "claude-stream-ghostty"]


def section_terminals() -> Section:
    sec = Section("terminals", (
        "On cable, chip CPU+GPU of the run minus control, W. shell = an empty terminal; idle = Claude Code waiting "
        "at its prompt; stream = Claude Code writing 3 essays in 8 min. Terminal.app, iTerm2 3.7.3, Ghostty 1.3.1. "
        "Dropped runs are not computed (their value is empty); the empty-terminal and idle-iTerm2 runs of those two "
        "series were repeated in the *-terminals-makeup series."))
    t = sec.table()
    for s in section_series("terminals"):
        sec.power[s.id] = s.power()
        chip = s.chip
        cw = control_watts(chip, s)
        for name in TERM_RUNS:
            if name in s.dropped:
                t.add(s.id, s.stand, s.day, name, [("chip W above control", "")], dropped_note(s, name))
                continue
            ws = [chip.watts(r["measure_start"], r["measure_end"]) for r in s.measured(name)]
            if ws:
                t.add(s.id, s.stand, s.day, name, [("chip W above control", "%+.3f" % (sum(ws) / len(ws) - cw))])
    return sec


def section_threads() -> Section:
    sec = Section("threads", (
        "On cable. Chip CPU+GPU over the 140 s after each message, W, in order 1 to 12."))
    t = sec.table()
    jumps = []
    for s in section_series("threads"):
        sec.power[s.id] = s.power()
        chip = s.chip
        rows = [r for r in s.log("app-stream.csv") if "NOFRONT" not in r]
        for run in s.runs:
            if "thread" not in run.name:
                continue
            end = run.ev.get("measure_end", 9e18)
            sent = [int(r[-1]) for r in rows if run["arm_start"] - 1 <= int(r[-1]) <= end]
            app = "ChatGPT" if run.name.startswith("chatgpt") else "Claude Desktop"
            values = [("chip W 140 s after message %d" % (k + 1),
                       (str(prompt_watts(chip, x)), s.message_note(run.name, k + 1))) for k, x in enumerate(sent)]
            t.add(s.id, s.stand, s.day, app, values)
            # Neighbour jumps from unrounded watts, dropped messages left out.
            raw = [(k + 1, chip.watts(x, x + 140) or 0) for k, x in enumerate(sent) if not s.message_note(run.name, k + 1)]
            jumps += [(max(x, y) / min(x, y), s.machine, s.day, app, i) for (i, x), (_, y) in zip(raw, raw[1:])
                      if min(x, y) > 0]
    if jumps:
        j = max(jumps)
        sec.lines.append("Largest jump between neighbouring messages: %.2f times (%s, %s, %s, message %d to %d; "
                         "unrounded watts)." % (j[0], j[1], j[2], j[3], j[4], j[4] + 1))
    return sec


def section_browsers() -> Section:
    sec = Section("browsers", (
        "On cable. Per-prompt answer audit: chip watts over the 140 s after sending; >= 0.25 W answered, "
        "0.15-0.25 W unsure, < 0.15 W no answer. 'whole run' = chip CPU+GPU of the run minus control. Claude on "
        "the 60 Hz M1 answers cheaply (0.16-0.22 W per prompt in the app and in the tab alike), so 'unsure' there "
        "is an answer. The page uses only the runs where every prompt got an answer."))
    t = sec.table()
    for s in section_series("browsers"):
        sec.power[s.id] = s.power()
        chip = s.chip
        cw = control_watts(chip, s)
        rows = [r for r in s.log("browser-stream.csv") + s.log("app-stream.csv") if "NOFRONT" not in r]
        for run in s.runs:
            n = run.name
            if "measure_end" not in run.ev or n == "control" or "thread" in n or "task" in n \
                    or "abandon" in n or "after" in n:
                continue
            vals = [prompt_watts(chip, int(r[-1])) for r in rows
                    if run["arm_start"] - 1 <= int(r[-1]) <= run["measure_end"]]
            yes = sum(1 for x in vals if x >= 0.25)
            unsure = sum(1 for x in vals if 0.15 <= x < 0.25)
            no = len(vals) - yes - unsure
            arm_w = chip.watts(run["measure_start"], run["measure_end"]) - cw
            note = "" if yes == len(vals) else (
                "page used only runs where every prompt was answered; audit here: %d answered, %d unsure, %d none "
                "of %d (for Claude on the 60 Hz M1 'unsure' counts as answered)" % (yes, unsure, no, len(vals)))
            values = [("chip W above control, whole run", "%+.3f" % arm_w),
                      ("answers yes/unsure/no", "%d/%d/%d" % (yes, unsure, no))]
            values += [("chip W 140 s after sending, prompt %d" % (k + 1), str(v)) for k, v in enumerate(vals)]
            t.add(s.id, s.stand, s.day, "%s (run %d)" % (n, run.index), values, note)
    return sec


def section_ollama() -> Section:
    sec = Section("ollama", (
        "Answer after answer (~1000 tokens, 10 s pause). J/token = (run - control) x window length / tokens "
        "generated in the window; answers that started before the window or ended after it count by the share of "
        "their time inside it. 'tokens per second while generating' = mean generation rate of the answers wholly "
        "inside the window; 'effective' = tokens in the window / window length. Chip = powermetrics; V x I = whole "
        "Mac (on cable it is one snapshot a minute and noisy; prefer the battery series). 'J per token, charge "
        "counter' (30-minute runs only) = counter drain of the run x its mean voltage, minus the controls' power by "
        "the same counter, over the run's tokens; also read one tick after the bounds. 'controls' = mean V x I of the "
        "series' controls; 'over the screen-only control' = the run's V x I over it; 'hours from 100% by V x I' (on "
        "battery only) = battery-log capacity x mean voltage of the run / its mean V x I. Under the table: the "
        "charge counter's largest rises in the 30-minute series, between and inside the measured windows."))
    t = sec.table()
    steps = sec.table("The charge counter's largest rises on battery, mAh:")
    for s in section_series("ollama"):
        chip = s.chip
        ticks = s.ticks
        g = gauge(ticks)
        cw = control_watts(chip, s)
        controls = s.measured("control")
        cms = [machine_mean(ticks, r["measure_start"], r["measure_end"]) for r in controls]
        cms = [x for x in cms if x is not None]
        cm = sum(cms) / len(cms) if cms else None
        log = s.log("ollama.csv")
        streams = [r for r in s.runs if r.name.endswith("-stream") and "measure_end" in r.ev]
        sec.power[s.id] = s.power(streams + controls)
        on_battery = sec.power[s.id] == "battery"
        caps = s.capacities()
        full = sum(caps) / len(caps) if caps else None

        def counter_j(run, read):
            s0, e0 = run["measure_start"], run["measure_end"]
            return (read(g, s0)[1] - read(g, e0)[1]) * mean_volts(g, s0, e0) * 3.6  # mAh x V -> J
        cpw = {read: sum(counter_j(r, read) for r in controls) / sum(r["measure_end"] - r["measure_start"]
                                                                       for r in controls)
               for read in (at, after)} if s.id in OLLAMA_COUNTER and controls else None
        for run in streams:
            t0, t1 = run["measure_start"], run["measure_end"]
            toks = 0.0
            for r in log:
                a, b, n = int(r[2]), int(r[3]), int(r[4])
                if b > a:
                    toks += n * max(0, min(b, t1) - max(a, t0)) / (b - a)
            inside = [r for r in log if t0 <= int(r[2]) and int(r[3]) <= t1]
            rate = sum(float(r[5]) for r in inside) / len(inside) if inside else None
            w = chip.watts(t0, t1)
            m = machine_mean(ticks, t0, t1)
            jt = (w - cw) * (t1 - t0) / toks if toks else None
            jm = (m - cm) * (t1 - t0) / toks if toks and m is not None and cm is not None else None
            volts = mean_volts(g, t0, t1)
            values = [
                ("tokens per second while generating", fmt(rate, 1)),
                ("tokens in the window (effective tokens per second)", "%.0f (%.1f)" % (toks, toks / (t1 - t0))),
                ("chip CPU+GPU, W", "%.2f" % w),
                ("J per token, chip", fmt(jt, 2)),
                ("whole machine V x I, W", fmt(m, 1)),
                ("J per token, V x I", fmt(jm, 2)),
                ("controls, whole machine V x I, W (mean)", fmt(cm, 3)),
                ("whole machine over the screen-only control, V x I, times", fmt(m / cm if m is not None and cm else None, 3)),
                ("hours from 100% by V x I (battery-log capacity x mean voltage of the run, over mean V x I)",
                 fmt(full * volts / 1000 / m if on_battery and full and volts and m else None, 2)),
            ]
            t.add(s.id, s.stand, s.day, run.name, values)
            if cpw and toks:
                counter = "charge counter (AppleRawCurrentCapacity) x mean voltage, minus the controls by the same counter"
                t.add(s.id, s.stand, s.day, run.name, [
                    ("J per token, charge counter", "%.2f" % ((counter_j(run, at) - cpw[at] * (t1 - t0)) / toks)),
                    ("J per token, charge counter, read one tick later (sensitivity check)",
                     "%.2f" % ((counter_j(run, after) - cpw[after] * (t1 - t0)) / toks)),
                ], instrument=counter)
        if s.id in OLLAMA_COUNTER:
            # Where the counter's rises after generation fell: inside a measured window or between runs.
            wins = [(r["measure_start"], r["measure_end"]) for r in s.runs if "measure_end" in r.ev]
            ups = [(d, any(a <= tt <= b for a, b in wins)) for tt, d in upward_steps(g)]
            out = sorted((d for d, inside in ups if not inside), reverse=True)
            ins = sorted((d for d, inside in ups if inside), reverse=True)
            steps.add(s.id, s.stand, s.day, "", [
                ("largest upward steps of the charge counter between the measured runs, mAh", list_text(out[:3])),
                ("largest upward steps of the charge counter inside the measured runs, mAh", list_text(ins[:3])),
            ], instrument=COUNTER_INSTRUMENT, power="battery")
            sec.lines.append("- %s: the charge counter rose %d times." % (s.id, len(ups)))
    return sec


def section_nights() -> Section:
    sec = Section("nights", (
        "On battery, the Mac falls asleep by itself. From `pmset -g log` between the start and the end of the "
        "night: asleep = sum of the Sleep durations (each cut at the end of the night) over the night's length; "
        "dark wakes = DarkWake lines, and how many of them name wifibt or rtc."))
    t = sec.table()
    for folder in sorted(os.listdir(NIGHTS)):
        d = os.path.join(NIGHTS, folder)
        if not os.path.exists(os.path.join(d, "events.csv")):
            continue
        with open(os.path.join(d, "meta.json")) as f:
            meta = json.load(f)
        t0, t1 = meta["start"], meta["end"]
        slept = dark = wifi = rtc = 0
        for r in read_csv(os.path.join(d, "events.csv")):
            ts = float(r["t_utc"])
            if not (t0 <= ts <= t1):
                continue
            if r["kind"] == "Sleep" and r["sleep_secs"] != "":
                slept += min(int(r["sleep_secs"]), t1 - ts)
            elif r["kind"] == "DarkWake":
                dark += 1
                wifi += int(r["wifibt"])
                rtc += int(r["rtc"])
        sid = "nights/" + folder
        sec.power[sid] = "battery"
        t.add(sid, meta["machine"], day_span(t0, t1), meta["left_open"], [
            ("hours", "%.1f" % ((t1 - t0) / 3600)),
            ("asleep, % of the night", "%.1f %%" % (slept / (t1 - t0) * 100)),
            ("dark wakes", "%d" % dark),
            ("dark wakes due to wifibt", "%d" % wifi),
            ("dark wakes due to rtc", "%d" % rtc),
        ])
    return sec


def section_leftovers() -> Section:
    sec = Section("leftovers", (
        "On cable. The agent is abandoned 90 s into its answer (terminal window closed, SIGHUP), then 30 minutes "
        "are measured (`*-afterhalf` runs). Chip CPU+GPU of the run minus control, W."))
    t = sec.table()
    for s in section_series("leftovers"):
        sec.power[s.id] = s.power()
        chip = s.chip
        cw = control_watts(chip, s)
        for agent, label in (("copilot", "after Copilot"), ("codex", "after Codex"), ("claude", "after Claude Code")):
            ws = [chip.watts(r["measure_start"], r["measure_end"]) for r in s.measured(agent + "-afterhalf")]
            if ws:
                t.add(s.id, s.stand, s.day, label, [("chip W above control", "%+.3f" % (ws[0] - cw))])
    return sec


IDLE_RUNS = ["chatgpt-front", "chatgpt-hidden", "claudeapp-front", "claudeapp-hidden", "cursor-front", "cursor-hidden"]


def section_idle() -> Section:
    sec = Section("idle", (
        "On cable, chip CPU+GPU of the run minus the mean of the series' controls, W. Cursor = the agents window "
        "with no folder open. The 2026-10-11 series on the M1 Pro with macOS 27 stops after 'ChatGPT in front' "
        "(the lid was closed)."))
    t = sec.table()
    for s in section_series("idle"):
        sec.power[s.id] = s.power()
        chip = s.chip
        cw = control_watts(chip, s)
        for name in IDLE_RUNS:
            ws = [chip.watts(r["measure_start"], r["measure_end"]) for r in s.measured(name)]
            if ws:
                t.add(s.id, s.stand, s.day, name, [("chip W above control", "%+.3f" % (ws[0] - cw))])
    return sec


def night_parts(s: Series, among: List[Series]) -> List[Series]:
    """The series of one lab night on this Mac: those of `among` on the same machine whose design (the name after
    the start time) differs only in a trailing part number (on the M1, 20261007T184417Z-ai-night-1 and
    20261007T233520Z-ai-night-2 are the night of 7 to 8 October)."""
    def stem(x: Series) -> str:
        design = x.id.split("-", 1)[1]
        return design.rsplit("-", 1)[0] if design.rsplit("-", 1)[-1].isdigit() else design
    return [x for x in among if x.machine == s.machine and stem(x) == stem(s)]


def section_ollama_idle() -> Section:
    sec = Section("ollama-idle", (
        "On cable, one instrument: V x I of the whole Mac (SystemPowerIn, one snapshot a minute; these series "
        "have no powermetrics). The load is flat, so ~60 snapshots an hour make a usable mean. In ai-night-1 "
        "`ollama8b-loaded` comes right after generation (10 min, cooling down), in ai-night-2 after a control "
        "(an hour). `ollama-serve` = the server without a model. ai-night-1 and ai-night-2 are the two parts of one "
        "night on each of the two Macs (the M1 and the M1 Pro with macOS 14); `day` is the night's span, from the first part's start to the last part's end."))
    t = sec.table()
    every = section_series("ollama-idle")
    for s in every:
        ticks = s.ticks
        ctrl = [machine_mean(ticks, r["measure_start"], r["measure_end"]) for r in s.measured("control")]
        ctrl = [x for x in ctrl if x is not None]
        cm = sum(ctrl) / len(ctrl)
        runs = [r for r in s.runs if r.name in ("ollama8b-loaded", "ollama-serve") and "measure_end" in r.ev]
        sec.power[s.id] = s.power(runs + s.measured("control"))
        night = night_parts(s, every)
        day = day_span(min(x.ticks[0].t for x in night), max(x.ticks[-1].t for x in night))
        for run in runs:
            m = machine_mean(ticks, run["measure_start"], run["measure_end"])
            values = [("minutes", "%.0f" % ((run["measure_end"] - run["measure_start"]) / 60)),
                      ("whole machine V x I, W", fmt(m, 3))]
            values += [("control %d, W" % (k + 1), "%.3f" % x) for k, x in enumerate(ctrl)]
            values.append(("W above control", "%+.3f" % (m - cm)))
            t.add(s.id, s.stand, day, run.name, values)
    return sec


def section_k() -> Section:
    sec = Section("k", (
        "On battery, two `yes` processes (CPU load without a window), 20-minute runs, ABAB. V x I of the whole Mac "
        "against the sum of the kernel's coalition energy counters (coalitions.csv, every coalition, 'other' "
        "included); k = (V x I of the load runs - of the controls) / (counters of the load runs - of the controls), "
        "means over the runs of each kind. WattMate uses one k on every Mac, 2.0."))
    t = sec.table()
    for s in section_series("k"):
        sec.power[s.id] = s.power()
        rows = [(r.name, machine_mean(s.ticks, r["measure_start"], r["measure_end"]),
                 coalition_watts(s, r["measure_start"], r["measure_end"]) or 0.0)
                for r in s.runs if "measure_end" in r.ev]
        ctl = [r for r in rows if r[0] == "control"]
        load = [r for r in rows if r[0] != "control"]
        dm = sum(r[1] for r in load) / len(load) - sum(r[1] for r in ctl) / len(ctl)
        dc = sum(r[2] for r in load) / len(load) - sum(r[2] for r in ctl) / len(ctl)
        t.add(s.id, s.stand, s.day, ARM_LABELS.get(load[0][0], load[0][0]), [
            ("runs", "%d" % len(load)),
            ("W above control, V x I", "%+.3f" % dm),
            ("W above control, process counters (coalitions)", "%+.3f" % dc),
            ("k: battery joules per counted joule, by V x I (the app uses 2.0 on every Mac)", "%.3f" % (dm / dc)),
        ])
    return sec


def release_values(s: Series):
    """The runs of a Release check series by the rules of LAB_DESIGN 17.31, with the formulas of arm_values, and the
    difference of the valid front and hidden runs: (rows, difference or None, summary line).
    A run is valid with at least 10 of its 11 judged prompts answered by bytes received and no prompt sent to the wrong
    window; a front run also needs ChatGPT's renderer at 0.1 W or more in the measured window. Order tail (17.26): if
    both hidden runs are valid and the second hidden run minus the first, by V x I above the controls, is larger than
    the spread of the two valid front runs, hiding is read from the second hidden run alone. The charge counter leaves
    out a run whose two readings differ by more than 10 points. The lab reads only the runs that hold WattMate's own
    90-second windows; every run of these series does (WattMate was open in all of them), which data/ cannot show."""
    rows = arm_values(s, parts=True)
    label_runs(rows, RELEASE_NAMES)
    for r in rows:
        r["valid"] = None
        if r["name"] == "control":
            continue
        r["audit"] = byte_bands(s, r["judged"])
        good = r["audit"][0] >= 10 and not r["bad"]
        if r["name"] == "chatgptstream-front" and (r["render"] or 0) < 0.1:
            good = False
        r["valid"] = good
    front = [r for r in rows if r["name"] == "chatgptstream-front" and r["valid"]]
    hidden_all = [r for r in rows if r["name"] == "chatgptstream-hidden"]
    hidden = [r for r in hidden_all if r["valid"]]
    if not front or not hidden:
        return rows, None, "- %s, %s: no valid run of one kind, no difference." % (s.stand, s.id)
    tail = "order tail not judged (not two valid runs of each kind)"
    if len(front) == 2 and len(hidden_all) == 2 and all(r["valid"] for r in hidden_all):
        spread = abs(front[0]["dvxi"] - front[1]["dvxi"])
        gap = hidden_all[1]["dvxi"] - hidden_all[0]["dvxi"]
        if gap > spread:
            hidden = [hidden_all[1]]
            tail = "order tail found by V x I (%.2f > %.2f W): hidden read from hidden-2" % (gap, spread)
        else:
            tail = "no order tail by V x I (%.2f <= %.2f W): hidden read from both runs" % (gap, spread)

    def mean(xs, k):
        return sum(x[k] for x in xs) / len(xs)
    diff = dict(dvxi=mean(front, "dvxi") - mean(hidden, "dvxi"), dcw=None, note="")
    f = [r for r in front if not r["shaky"]]
    h = [r for r in hidden if not r["shaky"]]
    if f and h:
        diff["dcw"] = mean(f, "dcw") - mean(h, "dcw")
        if (len(f), len(h)) != (2, 2):
            out = [r for r in front + hidden if r["shaky"]]
            nth = {1: "first", 2: "second", 3: "third"}
            diff["note"] = MARK_RELEASE_COUNTER + (
                "; here the %s on this Mac" % " and the ".join(
                    "%s %s run" % (nth.get(int(r["label"].rsplit("-", 1)[1]), r["label"]), r["label"].rsplit("-", 1)[0])
                    for r in out) if out else "")
    line = "- %s, %s: %s; whole Mac in front minus hidden %+.4f W by V x I, %s by the charge counter." % (
        s.stand, s.id, tail, diff["dvxi"], "-" if diff["dcw"] is None else "%+.4f W" % diff["dcw"])
    return rows, diff, line


def section_wattmate() -> Section:
    sec = Section("wattmate", (
        "On battery. Of the comparison of WattMate's own records with the lab's instruments, the numbers that need "
        "only data/. In the M3 Max run: the screen alone, the mean V x I of the series' control runs, which the "
        "comparison subtracts. In the Release check of 15 October (LAB_DESIGN 17.31; WattMate open in every run, "
        "controls included; control, front, hidden, hidden, front, control): for each ChatGPT run the answers by bytes "
        "received (the first prompt is not judged; >= 20,000 yes, < 5,000 no), ChatGPT's renderer and whether the run "
        "is valid (at least 10 of 11 judged prompts answered; a front run also needs the renderer at 0.1 W or more); "
        "then the whole Mac's difference between the window in front and hidden, the mean of the valid front runs "
        "minus the mean of the valid hidden runs, each above the series' controls, by V x I and by the charge counter "
        "(a run whose two counter readings differ by more than 10 points is left out of the counter's difference; the "
        "order tail rule of 17.26 decides whether hiding is read from both hidden runs). Every other number of the "
        "comparison needs WattMate's own database or the collector's full per-process log, and the M1's run of 13 "
        "October is in a series that is not in data/; see NOT_RECOMPUTED."))
    t = sec.table()
    for sid, name, label in WATTMATE_RUNS:
        s = series(sid)
        run = s.measured(name)
        sec.power[s.id] = s.power(run)
        ctrl = [machine_mean(s.ticks, r["measure_start"], r["measure_end"]) for r in s.measured("control")]
        t.add(s.id, s.stand, s.day, label, [(WATTMATE_SCREEN, "%.4f" % (sum(ctrl) / len(ctrl)))])
    rel = sec.table("The Release check of 15 October, the numbers of the lab's instruments:")
    for s in section_series("wattmate"):
        sec.power[s.id] = s.power()
        rows, diff, line = release_values(s)
        for r in rows:
            if r["name"] == "control":
                continue  # its one number, WattMate against V x I, needs WattMate's database
            rel.add(s.id, s.stand, s.day, r["label"], [
                (RELEASE_ANSWERS, "%d/%d/%d" % r["audit"]),
                (RELEASE_RENDER, "%.4f" % r["render"]),
                (RELEASE_VALID, "yes" if r["valid"] else "no")])
        if diff is not None:
            rel.add(s.id, s.stand, s.day, RELEASE_DIFF, [
                (RELEASE_DVXI, "%+.4f" % diff["dvxi"]),
                (RELEASE_DCW, None if diff["dcw"] is None else ("%+.4f" % diff["dcw"], diff["note"]))])
        sec.lines.append(line)
    return sec


SECTION_FUNCS = OrderedDict([
    ("minutes", section_minutes), ("minutes-abba", section_minutes_abba),
    ("window-vs-answer", section_window_vs_answer), ("window-vs-answer-reading", section_window_vs_answer_reading),
    ("window-vs-textedit", section_window_vs_textedit),
    ("window-vs-textedit-reading", section_window_vs_textedit_reading),
    ("agents", section_agents), ("cursor", section_cursor),
    ("window", section_window), ("terminals", section_terminals), ("threads", section_threads),
    ("browsers", section_browsers), ("ollama", section_ollama), ("nights", section_nights),
    ("leftovers", section_leftovers), ("idle", section_idle), ("ollama-idle", section_ollama_idle),
    ("k", section_k), ("wattmate", section_wattmate)])


def compute(keys: Optional[List[str]] = None) -> List[Section]:
    keys = keys or list(SECTION_FUNCS)
    bad = [k for k in keys if k not in SECTION_FUNCS]
    if bad:
        raise SystemExit("unknown section: %s (known: %s)" % (", ".join(bad), " ".join(SECTION_FUNCS)))
    return [SECTION_FUNCS[k]() for k in keys]


def main(argv: List[str]) -> int:
    for sec in compute(argv[1:]):
        print(sec.render())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

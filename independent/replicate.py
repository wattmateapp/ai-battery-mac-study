#!/usr/bin/env python3
"""Independent replication of the AI-battery study's published numbers.

Inputs (read only):
  ../data/runs/<series>/*.csv, meta.json      the public dataset
  ../data/nights/<night>/events.csv, meta.json
  ../results/ai-battery-usage-mac.csv         the published numbers, one per row

The method is taken from the page "Does AI drain your Mac battery?"
(web/ai-battery-usage-mac.html): its "How we measured" section and the table
captions. Where the page leaves a detail open, the reading used here is named
in a comment marked READING, and the alternatives that were tried are printed
in the "alternatives" part of the report.

Python 3.9, standard library only. Run:  python3 replicate.py [--rows]
  --rows   also print every compared row, not only the mismatches
"""
import csv
import json
import os
import re
import sys
from collections import OrderedDict, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNS = os.path.join(ROOT, "data", "runs")
NIGHTS = os.path.join(ROOT, "data", "nights")
RESULTS = os.path.join(ROOT, "results", "ai-battery-usage-mac.csv")

SHOW_ROWS = "--rows" in sys.argv


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def fnum(x):
    return float(x) if x not in ("", None) else None


class Series(object):
    """One session on one Mac: arms plus instrument logs."""

    _cache = {}

    @classmethod
    def get(cls, name):
        if name not in cls._cache:
            cls._cache[name] = cls(name)
        return cls._cache[name]

    def __init__(self, name):
        self.name = name
        d = os.path.join(RUNS, name)
        self.dir = d
        self.meta = json.load(open(os.path.join(d, "meta.json")))
        self.arms = []
        for a in read_csv(os.path.join(d, "arms.csv")):
            self.arms.append(dict(
                arm=a["arm"], index=int(a["index"]),
                arm_start=float(a["arm_start"]), measure_start=float(a["measure_start"]),
                measure_end=float(a["measure_end"]), arm_stop=float(a["arm_stop"])))
        self.journal = read_csv(os.path.join(d, "journal.csv"))
        self._chip = None
        self._bat = None
        self._subject = None

    def log(self, fname):
        return read_csv(os.path.join(self.dir, fname))

    @property
    def chip(self):
        if self._chip is None:
            self._chip = [dict(t=float(c["t_end"]), dt=float(c["seconds"]),
                               cpu=float(c["cpu_mj"]), gpu=float(c["gpu_mj"]),
                               ane=float(c["ane_mj"]))
                          for c in self.log("chip.csv")]
        return self._chip

    @property
    def bat(self):
        if self._bat is None:
            self._bat = [dict(t=float(b["t"]), src=b["power_source"],
                              cc=fnum(b["charge_counter_mah"]), pct=fnum(b["percent"]),
                              v=fnum(b["voltage_mv"]), i=fnum(b["pack_current_ma"]),
                              sp=fnum(b["system_power_in_mw"]), w=fnum(b["whole_mac_w"]))
                         for b in self.log("battery.csv")]
        return self._bat

    @property
    def subject(self):
        if self._subject is None:
            self._subject = [dict(t=float(s["t"]), arm=s["arm"], index=int(s["index"]),
                                  subject=s["subject"],
                                  e=float(s["energy_mj_since_previous_tick"]))
                             for s in self.log("subject.csv")]
        return self._subject

    def arms_named(self, name):
        return [a for a in self.arms if a["arm"] == name]

    def arm_index(self, idx):
        for a in self.arms:
            if a["index"] == idx:
                return a
        raise KeyError(idx)

    @property
    def controls(self):
        return self.arms_named("control")


# --------------------------------------------------------------------------
# Instrument primitives
# --------------------------------------------------------------------------

# READING (chip window): a 5-second powermetrics snapshot belongs to the
# interval that contains its midpoint (t_end - seconds/2), and the mean power is
# the snapshots' energy divided by the snapshots' own durations. The page does
# not say how a snapshot that straddles a run boundary is assigned. Other
# assignments (inside only, by end time, prorated by overlap) are compared in
# the "alternatives" section.
CHIP_RULE = "mid"


def chip_select(chip, a, b, rule=None):
    rule = rule or CHIP_RULE
    out = []
    for c in chip:
        s0, e0 = c["t"] - c["dt"], c["t"]
        if rule == "mid":
            w = 1.0 if a <= (s0 + e0) / 2 < b else 0.0
        elif rule == "inside":
            w = 1.0 if (s0 >= a and e0 <= b) else 0.0
        elif rule == "end":
            w = 1.0 if a < e0 <= b else 0.0
        elif rule == "overlap":
            w = max(0.0, min(b, e0) - max(a, s0)) / c["dt"]
        else:
            raise ValueError(rule)
        if w > 0:
            out.append((c, w))
    return out


def chip_energy(chip, a, b, keys=("cpu", "gpu"), rule=None):
    """(energy J, seconds covered) of the selected snapshots."""
    sel = chip_select(chip, a, b, rule)
    e = sum(sum(c[k] for k in keys) * w for c, w in sel) / 1000.0
    d = sum(c["dt"] * w for c, w in sel)
    return e, d


def chip_w(chip, a, b, keys=("cpu", "gpu"), rule=None):
    e, d = chip_energy(chip, a, b, keys, rule)
    return e / d if d else float("nan")


def arm_chip_w(series, arm, keys=("cpu", "gpu"), rule=None):
    return chip_w(series.chip, arm["measure_start"], arm["measure_end"], keys, rule)


# READING (control): "above control" = minus the mean of ALL control runs of the
# same series (each control's mean over its measure window). Alternatives
# (nearest preceding control, the two bracketing controls) are compared in the
# "alternatives" section.
def control_chip_w(series, keys=("cpu", "gpu"), rule=None):
    ws = [arm_chip_w(series, c, keys, rule) for c in series.controls]
    return sum(ws) / len(ws)


def vi_ticks(series, a, b):
    """Sampler ticks of whole_mac_w with a <= t <= b."""
    return [r for r in series.bat if a <= r["t"] <= b and r["w"] is not None]


def vi_w(series, a, b):
    t = vi_ticks(series, a, b)
    return sum(r["w"] for r in t) / len(t)


def arm_vi_w(series, arm):
    return vi_w(series, arm["measure_start"], arm["measure_end"])


def control_vi_w(series):
    ws = [arm_vi_w(series, c) for c in series.controls]
    return sum(ws) / len(ws)


# READING (charge counter at a run boundary): the last sampler tick at or before
# the boundary. The page says only "the drop between the boundaries of a run".
def cc_at(series, t, rule="before"):
    ticks = [r for r in series.bat if r["cc"] is not None]
    if rule == "before":
        x = [r for r in ticks if r["t"] <= t]
        return x[-1]["cc"]
    if rule == "after":
        x = [r for r in ticks if r["t"] >= t]
        return x[0]["cc"] if x else ticks[-1]["cc"]
    if rule == "nearest":
        return min(ticks, key=lambda r: abs(r["t"] - t))["cc"]
    raise ValueError(rule)


def drain_mah(series, arm, rule="before"):
    return cc_at(series, arm["measure_start"], rule) - cc_at(series, arm["measure_end"], rule)


# READING (answer audit): mean chip CPU+GPU power over the 140 s after sending,
# same snapshot rule as everywhere; >= 0.25 W answered, 0.15-0.25 unsure,
# < 0.15 none. AUDIT_ROUND = 2 classifies the value rounded to 0.01 W (what the
# results file turns out to do); AUDIT_ROUND = None is the page's literal text.
AUDIT_ROUND = 2


def audit_w(series, sent):
    return chip_w(series.chip, sent, sent + 140)


def audit_class(w, rnd=None):
    if rnd is not None:
        w = round(w, rnd)
    return "y" if w >= 0.25 else ("u" if w >= 0.15 else "n")


def audit_counts(ws, rnd=AUDIT_ROUND):
    c = [audit_class(w, rnd) for w in ws]
    return (c.count("y"), c.count("u"), c.count("n"))


def prompts_for_arm(series, arm):
    """Send times of the prompts that belong to an arm (between arm start and stop)."""
    a0, a1 = arm["arm_start"], arm["arm_stop"]
    n = arm["arm"]
    if n.startswith("safari-") or n.startswith("chrome-"):
        br, svc = n.split("-", 1)
        br = {"safari": "Safari", "chrome": "Chrome"}[br]
        rows = series.log("browser-stream.csv")
        return [float(p["sent_at"]) for p in rows
                if p["browser"] == br and p["service"] == svc and a0 <= float(p["sent_at"]) <= a1]
    app = "ChatGPT" if "chatgpt" in n else "Claude"
    mode = None
    for m in ("front", "hidden", "behind", "minimized"):
        if n.endswith("-" + m):
            mode = m
    rows = series.log("app-stream.csv")
    return [float(p["sent_at"]) for p in rows
            if p["app"] == app and (mode is None or p["mode"] == mode)
            and a0 <= float(p["sent_at"]) <= a1]


# --------------------------------------------------------------------------
# Results file and comparison
# --------------------------------------------------------------------------

RESULT_ROWS = read_csv(RESULTS)
for _i, _r in enumerate(RESULT_ROWS):
    _r["_i"] = _i

SECTION_KEYS = OrderedDict([
    ("minutes", "battery minutes"),
    ("ollama", "local model"),
    ("agents", "agents on a build task"),
    ("window", "app window while an answer streams"),
    ("nights", "night with an AI app open"),
    ("terminals", "terminal running Claude Code"),
    ("idle", "app open and idle"),
    ("leftovers", "after the agent is closed"),
    ("browser", "browser tab vs app"),
    ("threads", "long thread"),
    ("cursor", "Cursor agent vs Claude Code"),
    ("ollama-idle", "model loaded and waiting"),
])


def section_rows(key):
    p = SECTION_KEYS[key]
    return [r for r in RESULT_ROWS if r["section"].startswith(p)]


NUM = re.compile(r"[+-]?\d+(?:\.\d+)?")


def fmt_like(pub, mine):
    """Format `mine` the way the published string `pub` is formatted.

    mine may be a number, a tuple of numbers (formatted into the numeric tokens
    of pub in order) or a string (compared as is)."""
    if isinstance(mine, str):
        return mine
    vals = list(mine) if isinstance(mine, (tuple, list)) else [mine]
    toks = list(NUM.finditer(pub))
    if len(toks) != len(vals):
        return "?" + repr(vals)
    out, pos = [], 0
    for m, v in zip(toks, vals):
        out.append(pub[pos:m.start()])
        t = m.group(0)
        dec = len(t.split(".")[1]) if "." in t else 0
        s = "%.*f" % (dec, v)
        if t.startswith("+") and not s.startswith("-"):
            s = "+" + s
        out.append(s)
        pos = m.end()
    out.append(pub[pos:])
    return "".join(out)


def same(pub, mine_s):
    if pub == mine_s:
        return True
    # a zero printed with either sign (-0.00 / +0.00 / 0.00) is the same number
    norm = lambda s: re.sub(r"[+-](0(?:\.0+)?)(?!\d)", r"\1", s)
    return norm(pub) == norm(mine_s)


def numeric_diff(pub, mine):
    if isinstance(mine, (int, float)):
        m = NUM.search(pub)
        if m:
            return mine - float(m.group(0))
    return None


class Ledger(object):
    def __init__(self):
        self.rows = defaultdict(list)     # section -> [(row, mine_value, mine_str, ok, why)]
        self.skipped = defaultdict(list)  # section -> [(row, reason)]

    def put(self, sec, row, mine, why=""):
        pub = row["value"]
        if mine is None:
            self.skipped[sec].append((row, why or "not computable from the files"))
            return
        ms = fmt_like(pub, mine)
        self.rows[sec].append((row, mine, ms, same(pub, ms), why))

    def skip(self, sec, row, reason):
        self.skipped[sec].append((row, reason))


L = Ledger()


def short(s):
    return s[:16] if s else ""


def raw(v):
    if isinstance(v, float):
        return "%.6g" % v
    if isinstance(v, (tuple, list)):
        return "(" + ", ".join(raw(x) for x in v) + ")"
    return str(v)


# --------------------------------------------------------------------------
# 1. Battery minutes
# --------------------------------------------------------------------------

def capacity_log(series):
    """Battery-reported full charge capacity: mean over the journal rows of the
    series (FullChargeCapacity when present, else AppleRawMaxCapacity)."""
    j = series.journal
    key = "fcc_mah" if j[0]["fcc_mah"] else "raw_max_mah"
    v = [float(x[key]) for x in j if x[key]]
    return sum(v) / len(v)


def capacity_gauge(series):
    """Capacity by the slope of the percent gauge: counter drop over percent drop
    between the first and last sampler tick of the series, times 100."""
    t = [r for r in series.bat if r["cc"] is not None and r["pct"] is not None]
    return (t[0]["cc"] - t[-1]["cc"]) / (t[0]["pct"] - t[-1]["pct"]) * 100.0


def minutes_arm_numbers(s, arm, rule="before"):
    ms, me = arm["measure_start"], arm["measure_end"]
    hours = (me - ms) / 3600.0
    drain = drain_mah(s, arm, rule)
    ticks = vi_ticks(s, ms, me)
    vmean = sum(r["v"] for r in ticks) / len(ticks) / 1000.0
    return dict(
        drain=drain,
        w_cc=drain * vmean / hours / 1000.0,   # whole-Mac W by the counter: mAh x V / h
        vi=sum(r["w"] for r in ticks) / len(ticks),
        cpu=arm_chip_w(s, arm, ("cpu",)),
        gpu=arm_chip_w(s, arm, ("gpu",)),
        hours=hours)


def subject_w(s, arm):
    """Per-process energy of the arm's subject: ticks with measure_start < t <=
    measure_end (each tick holds the energy since the previous tick), divided by
    the measure window."""
    ms, me = arm["measure_start"], arm["measure_end"]
    e = sum(x["e"] for x in s.subject
            if x["arm"] == arm["arm"] and x["index"] == arm["index"] and ms < x["t"] <= me)
    return e / 1000.0 / (me - ms)


MINUTES = {}   # series -> dict(arm index -> numbers, ctrl means, capacity)


def compute_minutes(rule="before"):
    out = {}
    for name in sorted(os.listdir(RUNS)):
        if not (name.endswith("-minutes-battery") or name.endswith("-minutes-chatgpt")):
            continue
        s = Series.get(name)
        nums = OrderedDict((a["index"], minutes_arm_numbers(s, a, rule)) for a in s.arms)
        ctrl = [nums[a["index"]] for a in s.controls]
        cm = lambda k: sum(c[k] for c in ctrl) / len(ctrl)
        out[name] = dict(series=s, nums=nums, c_drain=cm("drain"), c_wcc=cm("w_cc"),
                         c_vi=cm("vi"), cap=capacity_log(s), cap_gauge=capacity_gauge(s))
    return out


def minutes_derived(M, idx):
    n = M["nums"][idx]
    cd, cap = M["c_drain"], M["cap"]
    per_hour = n["drain"] / n["hours"]
    return dict(
        faster=(n["drain"] / cd - 1) * 100,
        less=(1 - cd / n["drain"]) * 100,
        pph=(n["drain"] - cd) / n["hours"] / cap * 100,
        w_cc_above=n["w_cc"] - M["c_wcc"],
        w_vi_above=n["vi"] - M["c_vi"],
        hours_full=cap / per_hour,
        faster_vi=(n["vi"] / M["c_vi"] - 1) * 100)


MINUTES_AFTER = {}


def do_minutes():
    global MINUTES, MINUTES_AFTER
    MINUTES = compute_minutes()
    MINUTES_AFTER = compute_minutes("after")
    rows = section_rows("minutes")
    seen = defaultdict(int)
    last_series = None
    for r in rows:
        name = r["series"]
        if not name:
            # capacity rows: belong to the series printed just before them
            M = MINUTES[last_series]
            if r["metric"].startswith("battery capacity as the battery log"):
                L.put("minutes", r, M["cap"])
            elif r["metric"].startswith("battery capacity by the slope"):
                L.put("minutes", r, M["cap_gauge"])
            else:
                L.skip("minutes", r, "unknown metric")
            continue
        last_series = name
        M = MINUTES[name]
        s = M["series"]
        k = (name, r["run"])
        cands = s.arms_named(r["run"])
        # rows come arm by arm; the n-th block with a run label is the n-th arm of that name
        if r["metric"] == "mAh drained in 30 min":
            seen[k] += 1
        arm = cands[seen[k] - 1]
        n = M["nums"][arm["index"]]
        d = minutes_derived(M, arm["index"])
        m = r["metric"]
        if m == "mAh drained in 30 min":
            L.put("minutes", r, n["drain"])
        elif m == "faster than screen only, %":
            L.put("minutes", r, d["faster"])
        elif m.startswith("faster than screen only, %, counter read one tick later"):
            # READING: the first sampler tick at or after each boundary, for the
            # run and for both controls
            L.put("minutes", r, minutes_derived(MINUTES_AFTER[name], arm["index"])["faster"])
        elif m == "less time on battery than screen only, %":
            L.put("minutes", r, d["less"])
        elif m == "% of battery per hour above screen":
            L.put("minutes", r, d["pph"])
        elif m.startswith("W above screen, charge counter"):
            L.put("minutes", r, d["w_cc_above"])
        elif m == "W above screen, V x I":
            L.put("minutes", r, d["w_vi_above"])
        elif m == "whole machine V x I, W":
            L.put("minutes", r, n["vi"])
        elif m == "chip CPU, W":
            L.put("minutes", r, n["cpu"])
        elif m == "chip GPU, W":
            L.put("minutes", r, n["gpu"])
        elif m == "hours from 100%":
            L.put("minutes", r, d["hours_full"])
        elif m.startswith("app processes, W"):
            L.put("minutes", r, subject_w(s, arm))
        elif m == "answers yes/unsure/no":
            ws = [audit_w(s, t) for t in prompts_for_arm(s, arm)]
            L.put("minutes", r, "%d/%d/%d" % audit_counts(ws))
        else:
            L.skip("minutes", r, "unknown metric")


# --------------------------------------------------------------------------
# 2. Ollama (battery and cable)
# --------------------------------------------------------------------------

def ollama_numbers(s, arm):
    ms, me = arm["measure_start"], arm["measure_end"]
    dur = me - ms
    model = "llama3.1:8b" if "8b" in arm["arm"] else "llama3.2:3b"
    answers = [o for o in s.log("ollama.csv") if o["model"] == model and o["end"]
               and float(o["end"]) >= arm["arm_start"] - 1 and float(o["start"]) <= arm["arm_stop"] + 1]
    # tokens actually produced inside the window; an answer straddling a boundary
    # counts by its share of time (page: "counted by its share")
    tok = 0.0
    for o in answers:
        a0, a1, n = float(o["start"]), float(o["end"]), float(o["eval_count_tokens"])
        ov = max(0.0, min(me, a1) - max(ms, a0))
        tok += n * ov / (a1 - a0)
    rate = sum(float(o["eval_rate_tokens_per_s"]) for o in answers) / len(answers)
    cw = arm_chip_w(s, arm)
    vi = arm_vi_w(s, arm)
    return dict(rate=rate, tokens=tok, eff=tok / dur, chip=cw,
                # READING: chip J/token is the chip energy over the window (not
                # minus control: the page's column says "chip CPU+GPU"); minus
                # control changes no printed digit (control is ~0.01 W)
                jt_chip=cw * dur / tok,
                jt_chip_above=(cw - control_chip_w(s)) * dur / tok,
                vi=vi,
                jt_vi=(vi - control_vi_w(s)) * dur / tok,
                dur=dur)


OLLAMA = {}


def do_ollama():
    seen = defaultdict(int)
    for r in section_rows("ollama"):
        s = Series.get(r["series"])
        k = (r["series"], r["run"])
        if r["metric"] == "tokens per second while generating":
            seen[k] += 1
        arm = s.arms_named(r["run"])[seen[k] - 1]
        key = (r["series"], arm["index"])
        if key not in OLLAMA:
            OLLAMA[key] = ollama_numbers(s, arm)
        n = OLLAMA[key]
        m = r["metric"]
        if m == "tokens per second while generating":
            L.put("ollama", r, n["rate"])
        elif m.startswith("tokens in the window"):
            L.put("ollama", r, (n["tokens"], n["eff"]))
        elif m == "chip CPU+GPU, W":
            L.put("ollama", r, n["chip"])
        elif m == "J per token, chip":
            L.put("ollama", r, n["jt_chip"])
        elif m == "whole machine V x I, W":
            L.put("ollama", r, n["vi"])
        elif m == "J per token, V x I":
            L.put("ollama", r, n["jt_vi"])
        else:
            L.skip("ollama", r, "unknown metric")


# --------------------------------------------------------------------------
# 3. Agents on a build task
# --------------------------------------------------------------------------

def agent_tasks(s, arm):
    return [t for t in s.log("agent-task.csv")
            if arm["arm_start"] - 2 <= float(t["start"]) <= arm["arm_stop"]]


def agent_numbers(s, arm, control="all", window="arm"):
    tasks = agent_tasks(s, arm)
    # "Energy is the chip over the whole run, minus control, divided by the
    # number of tasks." READING: whole run = arm_start..arm_stop (the tasks start
    # at arm_start, before the measure window opens).
    if window == "arm":
        a0, a1 = arm["arm_start"], arm["arm_stop"]
    else:
        a0, a1 = arm["measure_start"], arm["measure_end"]
    e, d = chip_energy(s.chip, a0, a1)
    if control == "all":
        cw = control_chip_w(s)
    else:
        prev = [c for c in s.controls if c["measure_end"] <= arm["arm_start"]][-1]
        cw = arm_chip_w(s, prev)
    j = (e - cw * d) / len(tasks)
    durs = [float(t["end"]) - float(t["start"]) for t in tasks]
    starts = [float(t["start"]) for t in tasks]
    return dict(n=len(tasks), j=j, wh=j / 3600.0,
                min_per_task=sum(durs) / len(durs) / 60.0,
                all_done=(max(float(t["end"]) for t in tasks) - min(starts)) / 60.0)


AGENTS = {}


def do_agents():
    for r in section_rows("agents"):
        s = Series.get(r["series"])
        arm = s.arms_named(r["run"])
        assert len(arm) == 1
        arm = arm[0]
        key = (r["series"], r["run"])
        if key not in AGENTS:
            AGENTS[key] = agent_numbers(s, arm)
        n = AGENTS[key]
        m = r["metric"]
        if m == "tasks":
            L.put("agents", r, float(n["n"]))
        elif m == "J per task":
            L.put("agents", r, n["j"])
        elif m == "Wh per task":
            L.put("agents", r, n["wh"])
        elif m == "min per task":
            L.put("agents", r, n["min_per_task"])
        elif m.startswith("all four copies done in"):
            L.put("agents", r, n["all_done"])
        else:
            L.skip("agents", r, "unknown metric")


# --------------------------------------------------------------------------
# 4. Window states
# --------------------------------------------------------------------------

STATE = OrderedDict([("front", "front"), ("behind another window", "behind"),
                     ("hidden", "hidden"), ("minimised", "minimized")])
WINDOW = {}   # (series, app, state) -> dict


def window_numbers(s, app, state, control="all"):
    arms_ = s.arms_named("%s-%s" % (app, state))
    ws = [arm_chip_w(s, a) for a in arms_]
    if control == "all":
        above = [w - control_chip_w(s) for w in ws]
    else:
        def bracket(a):
            cs = s.controls
            p = [c for c in cs if c["measure_end"] <= a["arm_start"]][-1]
            n = [c for c in cs if c["measure_start"] >= a["arm_stop"]][0]
            return (arm_chip_w(s, p) + arm_chip_w(s, n)) / 2
        above = [w - bracket(a) for w, a in zip(ws, arms_)]
    prompts = []
    for a in arms_:
        prompts += [audit_w(s, t) for t in prompts_for_arm(s, a)]
    return dict(n=len(arms_), above=sum(above) / len(above), audit=audit_counts(prompts))


def do_window():
    for r in section_rows("window"):
        s = Series.get(r["series"])
        app = "chatgptstream" if r["run"] == "ChatGPT" else "claudestream"
        st = STATE[r["metric"].split(",")[0]]
        key = (r["series"], app, st)
        if key not in WINDOW:
            WINDOW[key] = window_numbers(s, app, st)
        n = WINDOW[key]
        if "chip W above control" in r["metric"]:
            L.put("window", r, n["above"])
        elif "answers" in r["metric"]:
            L.put("window", r, "%d/%d/%d" % n["audit"])
        else:
            L.skip("window", r, "unknown metric")


# --------------------------------------------------------------------------
# 5. Nights
# --------------------------------------------------------------------------

def night_numbers(folder):
    meta = json.load(open(os.path.join(NIGHTS, folder, "meta.json")))
    ev = read_csv(os.path.join(NIGHTS, folder, "events.csv"))
    st, en = float(meta["start"]), float(meta["end"])
    dur = en - st
    asleep = 0.0
    for e in ev:
        if e["kind"] == "Sleep":
            t0 = float(e["t_utc"])
            asleep += max(0.0, min(en, t0 + float(e["sleep_secs"])) - max(st, t0))
    dw = [e for e in ev if e["kind"] == "DarkWake"]
    return dict(meta=meta, hours=dur / 3600.0, asleep=asleep / dur * 100.0, dw=len(dw),
                wifibt=sum(1 for e in dw if e["wifibt"] == "1"),
                rtc=sum(1 for e in dw if e["rtc"] == "1"))


NIGHT = {}


def night_folder(series_label, stand):
    prefix = series_label.split("/", 1)[1]
    for f in sorted(os.listdir(NIGHTS)):
        if f.startswith(prefix):
            m = json.load(open(os.path.join(NIGHTS, f, "meta.json")))
            if m["machine"] == stand:
                return f
    return None


def do_nights():
    for r in section_rows("nights"):
        f = night_folder(r["series"], r["stand"])
        if f is None:
            L.skip("nights", r, "night folder not found")
            continue
        if f not in NIGHT:
            NIGHT[f] = night_numbers(f)
        n = NIGHT[f]
        m = r["metric"]
        val = {"hours": n["hours"], "asleep, % of the night": n["asleep"],
               "dark wakes": float(n["dw"]), "dark wakes due to wifibt": float(n["wifibt"]),
               "dark wakes due to rtc": float(n["rtc"])}.get(m)
        L.put("nights", r, val, "" if val is not None else "unknown metric")


# --------------------------------------------------------------------------
# 6. Terminals, idle apps, leftovers, browser, long thread, Cursor, Ollama idle
# --------------------------------------------------------------------------

def arm_above_control(s, arm):
    return arm_chip_w(s, arm) - control_chip_w(s)


def do_simple_above(key, runmap=None):
    for r in section_rows(key):
        if r["value"] == "":
            L.skip(key, r, "published value empty (dropped run)")
            continue
        s = Series.get(r["series"])
        name = runmap.get(r["run"], r["run"]) if runmap else r["run"]
        arm = s.arms_named(name)
        assert len(arm) == 1, (r["series"], name)
        L.put(key, r, arm_above_control(s, arm[0]))


RUN_N = re.compile(r"\(run (\d+)\)")
BROWSER = {}


def do_browser():
    for r in section_rows("browser"):
        s = Series.get(r["series"])
        arm = s.arm_index(int(RUN_N.search(r["run"]).group(1)))
        key = (r["series"], arm["index"])
        if key not in BROWSER:
            ps = prompts_for_arm(s, arm)
            ws = [audit_w(s, t) for t in ps]
            BROWSER[key] = dict(arm=arm, above=arm_above_control(s, arm), ws=ws,
                                audit=audit_counts(ws))
        b = BROWSER[key]
        m = r["metric"]
        if m == "chip W above control, whole run":
            L.put("browser", r, b["above"])
        elif m == "answers yes/unsure/no":
            L.put("browser", r, "%d/%d/%d" % b["audit"])
        elif m.startswith("chip W 140 s after sending, prompt"):
            k = int(m.split()[-1])
            L.put("browser", r, b["ws"][k - 1] if k <= len(b["ws"]) else None)
        else:
            L.skip("browser", r, "unknown metric")


THREADS = {}


def do_threads():
    for r in section_rows("threads"):
        s = Series.get(r["series"])
        name = "chatgptthread-front" if r["run"] == "ChatGPT" else "claudethread-front"
        arm = s.arms_named(name)[0]
        key = (r["series"], name)
        if key not in THREADS:
            THREADS[key] = [audit_w(s, t) for t in prompts_for_arm(s, arm)]
        k = int(r["metric"].split()[-1])
        ws = THREADS[key]
        L.put("threads", r, ws[k - 1] if k <= len(ws) else None)


CURSOR = {}


def cursor_numbers(s):
    cw = control_chip_w(s)
    per = {}
    for t in s.log("cursor-agent.csv"):
        x = float(t["sent_at"])
        # page: "chip energy from 10 seconds before to 60 seconds after sending"
        e, d = chip_energy(s.chip, x - 10, x + 60)
        per[(t["mode"], int(t["task"]))] = dict(j=e - cw * d, solved=(t["exit_code"] == "0"))
    # READING: "Claude Code without a UI, J per task" = the claude-task run
    # (measure window) minus control, divided by its tasks, like the agents rows
    ct = s.arms_named("claude-task")[0]
    e, d = chip_energy(s.chip, ct["measure_start"], ct["measure_end"])
    tasks = s.log("agent-task.csv")
    ntask = len(tasks)
    # "same -10 to +60 s window as Cursor": per task, from its start - 10 s to
    # start + 60 s, minus control; mean over the tasks. NOTE: the three Claude Code
    # tasks run back to back (9-13 s each), so these 70-s windows overlap and the
    # same energy is counted in up to three windows. "union" = one window from the
    # first start - 10 s to the last start + 60 s, divided by the tasks.
    win = []
    for t in tasks:
        x = float(t["start"])
        ew, dw = chip_energy(s.chip, x - 10, x + 60)
        win.append(ew - cw * dw)
    x0, x1 = float(tasks[0]["start"]) - 10, float(tasks[-1]["start"]) + 60
    eu, du = chip_energy(s.chip, x0, x1)
    cf = s.arms_named("cursor-front")[0]
    return dict(per=per, claude=(e - cw * d) / ntask, claude_win=sum(win) / len(win),
                claude_win_sum=sum(win), claude_run_sum=e - cw * d,
                claude_union=(eu - cw * du) / ntask, waiting=arm_above_control(s, cf))


def do_cursor():
    for r in section_rows("cursor"):
        s = Series.get(r["series"])
        if r["series"] not in CURSOR:
            CURSOR[r["series"]] = cursor_numbers(s)
        c = CURSOR[r["series"]]
        m = r["metric"]
        mm = re.match(r"Cursor window (in front|hidden), J, task (\d)", m)
        if mm:
            mode = "front" if mm.group(1) == "in front" else "hidden"
            L.put("cursor", r, c["per"][(mode, int(mm.group(2)))]["j"])
        elif m == "solved by Cursor":
            n = sum(1 for v in c["per"].values() if v["solved"])
            L.put("cursor", r, "%d of %d" % (n, len(c["per"])))
        elif m in ("Claude Code without a UI, J per task",
                   "Claude Code without a UI, J per task, whole run minus control"):
            L.put("cursor", r, c["claude"])
        elif m == "Claude Code without a UI, J per task, same -10 to +60 s window as Cursor":
            L.put("cursor", r, c["claude_win"])
        elif m == "Cursor open and waiting, W above control":
            L.put("cursor", r, c["waiting"])
        else:
            L.skip("cursor", r, "unknown metric")


def do_ollama_idle():
    for r in section_rows("ollama-idle"):
        s = Series.get(r["series"])
        arm = s.arms_named(r["run"])
        assert len(arm) == 1
        arm = arm[0]
        m = r["metric"]
        if m == "minutes":
            L.put("ollama-idle", r, (arm["measure_end"] - arm["measure_start"]) / 60.0)
        elif m == "whole machine V x I, W":
            L.put("ollama-idle", r, arm_vi_w(s, arm))
        elif m.startswith("control ") and m.endswith(", W"):
            k = int(m.split()[1].rstrip(","))
            L.put("ollama-idle", r, arm_vi_w(s, s.controls[k - 1]))
        elif m == "W above control":
            L.put("ollama-idle", r, arm_vi_w(s, arm) - control_vi_w(s))
        else:
            L.skip("ollama-idle", r, "unknown metric")


# --------------------------------------------------------------------------
# Page claims recomputed (the prose and tables, not the CSV)
# --------------------------------------------------------------------------

CLAIMS = []   # (item, claim as printed on the page, recomputed text, ok?)


def claim(item, printed, recomputed, ok):
    CLAIMS.append((item, printed, recomputed, ok))


def rng(vals, fmt="%.1f"):
    return (fmt % min(vals)) + " to " + (fmt % max(vals))


def kept(name, idx):
    """False when the series' meta.json lists the arm as dropped."""
    s = Series.get(name)
    arm = s.arm_index(idx)["arm"]
    return arm not in [d["arm"] for d in s.meta.get("dropped_runs", [])]


def stand(name):
    return Series.get(name).meta["machine"]


def page_claims():
    # ---- item 1
    by = defaultdict(list)   # condition -> [(series, idx, derived, nums)]
    for name, M in MINUTES.items():
        for a in M["series"].arms:
            if not kept(name, a["index"]):
                continue
            by[a["arm"]].append((name, a["index"], minutes_derived(M, a["index"]),
                                 M["nums"][a["index"]], M))
    f = lambda cond, k: [x[2][k] for x in by[cond]]
    claim(1, "ChatGPT front: 38 to 53% faster", rng(f("chatgptstream-front", "faster"), "%.1f"),
          rng(f("chatgptstream-front", "faster"), "%.0f") == "38 to 53")
    claim(1, "ChatGPT front: 27 to 34% less time", rng(f("chatgptstream-front", "less"), "%.2f"),
          rng(f("chatgptstream-front", "less"), "%.0f") == "27 to 34")
    claim(1, "ChatGPT front: 3.4 to 6.3% of battery per hour", rng(f("chatgptstream-front", "pph"), "%.2f"),
          rng(f("chatgptstream-front", "pph"), "%.1f") == "3.4 to 6.3")
    claim(1, "ChatGPT hidden: 0 to 10% faster (charge counter)", rng(f("chatgptstream-hidden", "faster"), "%.2f"),
          rng(f("chatgptstream-hidden", "faster"), "%.0f") == "0 to 10")
    claim(1, "ChatGPT hidden: 11 to 18% faster by voltage x current",
          rng(f("chatgptstream-hidden", "faster_vi"), "%.2f"),
          rng(f("chatgptstream-hidden", "faster_vi"), "%.0f") == "11 to 18")
    claim(1, "ChatGPT hidden: 0 to 1.2% per hour", rng(f("chatgptstream-hidden", "pph"), "%.3f"),
          rng(f("chatgptstream-hidden", "pph"), "%.1f") in ("0.0 to 1.2", "-0.0 to 1.2"))
    claim(1, "Claude front: 19 to 69% faster", rng(f("claudestream-front", "faster"), "%.2f"),
          rng(f("claudestream-front", "faster"), "%.0f") == "19 to 69")
    claim(1, "Claude front: 1.8 to 8.1% per hour", rng(f("claudestream-front", "pph"), "%.3f"),
          rng(f("claudestream-front", "pph"), "%.1f") == "1.8 to 8.1")
    claim(1, "Codex builds: 125 to 196% faster", rng(f("codex-loop-task", "faster"), "%.2f"),
          rng(f("codex-loop-task", "faster"), "%.0f") == "125 to 196")
    claim(1, "Codex builds: 13.1 to 15.9% per hour", rng(f("codex-loop-task", "pph"), "%.3f"),
          rng(f("codex-loop-task", "pph"), "%.1f") == "13.1 to 15.9")
    x = [1 + v / 100 for v in f("codex-loop-task", "faster")]
    claim(1, "Codex builds: 2.25 to 3 times the pace", rng(x, "%.3f"),
          "%.2f" % min(x) == "2.25" and round(max(x), 1) == 3.0)
    w = [(x[2]["w_cc_above"], x[2]["w_vi_above"]) for x in by["chatgptstream-front"]]
    claim(1, "ChatGPT front whole Mac above screen: +1.7 to 3.1 W (counter), +1.5 to 4.1 W (V x I)",
          rng([a for a, b in w], "%.2f") + " ; " + rng([b for a, b in w], "%.2f"),
          rng([a for a, b in w]) == "1.7 to 3.1" and rng([b for a, b in w]) == "1.5 to 4.1")
    procs = [subject_w(x[4]["series"], x[4]["series"].arm_index(x[1])) for x in by["chatgptstream-front"]]
    claim(1, "ChatGPT processes charged 0.28 to 0.35 W (subject.csv)", rng(procs, "%.4f"),
          rng(procs, "%.2f") == "0.28 to 0.35")
    ratios = [a / p for (a, b), p in zip(w, procs)] + [b / p for (a, b), p in zip(w, procs)]
    pubp = []
    for x in by["chatgptstream-front"]:
        r_ = [r for r in section_rows("minutes") if r["series"] == x[0]
              and r["run"] == "chatgptstream-front" and r["metric"].startswith("app processes")]
        pubp.append(float(r_[0]["value"]))
    ratios_p = [a / p for (a, b), p in zip(w, pubp)] + [b / p for (a, b), p in zip(w, pubp)]
    claim(1, "whole Mac above screen is five to thirteen times the per-process number",
          rng(ratios, "%.2f") + " with subject.csv; " + rng(ratios_p, "%.2f") + " with the published per-process W",
          round(min(ratios)) == 5 and round(max(ratios)) == 13)
    chipabove = []
    for x in by["chatgptstream-front"]:
        M = x[4]
        s = M["series"]
        chipabove.append(arm_chip_w(s, s.arm_index(x[1])) - control_chip_w(s))
    claim(1, "CPU+GPU above control during ChatGPT front: +0.5 to 0.6 W", rng(chipabove, "%.3f"),
          rng(chipabove) in ("0.5 to 0.6",))
    rest_cc = [a - c for (a, b), c in zip(w, chipabove)]
    rest_vi = [b - c for (a, b), c in zip(w, chipabove)]
    claim(1, "remaining 1.2 to 2.6 W (counter), 1.0 to 3.5 W (V x I)",
          rng(rest_cc, "%.2f") + " ; " + rng(rest_vi, "%.2f"),
          rng(rest_cc) == "1.2 to 2.6" and rng(rest_vi) == "1.0 to 3.5")
    cl = by["claudestream-front"]
    cproc = [subject_w(x[4]["series"], x[4]["series"].arm_index(x[1])) for x in cl]
    claim(1, "Claude processes 0.06 to 0.11 W", rng(cproc, "%.4f"), rng(cproc, "%.2f") == "0.06 to 0.11")
    claim(1, "Claude front whole Mac above screen 0.9 to 4.0 W (counter), 0.5 to 3.1 W (V x I)",
          rng([x[2]["w_cc_above"] for x in cl], "%.2f") + " ; " + rng([x[2]["w_vi_above"] for x in cl], "%.2f"),
          rng([x[2]["w_cc_above"] for x in cl]) == "0.9 to 4.0" and rng([x[2]["w_vi_above"] for x in cl]) == "0.5 to 3.1")
    # absolute V x I per Mac
    for mac, txt in (("M1, macOS 26", "3.2 to 3.3"), ("M1 Pro", "5.2 to 6.0")):
        v = [x[3]["vi"] for x in by["control"] if stand(x[0]).startswith(mac)]
        claim(1, "screen only, V x I, %s: %s W" % (mac, txt), rng(v, "%.3f"), rng(v) == txt)
    # hours table
    for cond, printed in (("control", "8.2 to 8.9 / 14.9 to 15.2 / 10.3 to 11.2"),
                          ("chatgptstream-front", "5.5 to 5.9 / 9.9 / 7.7"),
                          ("chatgptstream-hidden", "7.8 to 8.4 / 14.0 / 10.0"),
                          ("claudestream-front", "5.1 to 6.4 / 11.7 / 7.7 to 9.0"),
                          ("codex-loop-task", "3.6 to 3.8 / 5.1 / 4.0")):
        parts = []
        for mac in ("M1 Pro, macOS 27", "M1, macOS 26", "M1 Pro, macOS 14"):
            v = sorted(set("%.1f" % x[2]["hours_full"] for x in by[cond] if stand(x[0]) == mac),
                       key=float)
            parts.append(v[0] if len(v) == 1 else v[0] + " to " + v[-1])
        got = " / ".join(parts)
        claim(1, "hours from 100%%, %s: %s" % (cond, printed), got, got == printed)
    gpu_claude = [x[3]["gpu"] for x in cl]
    claim(1, "Claude window GPU: 0.07 W on the M1, 0.12 to 0.42 W on the M1 Pro machines",
          ", ".join("%s %.3f" % (stand(x[0])[:6], x[3]["gpu"]) for x in cl), True)
    cpu_codex = [x[3]["cpu"] for x in by["codex-loop-task"]]
    claim(1, "Codex battery runs: CPU 3.4 to 4.4 W", rng(cpu_codex, "%.3f"), rng(cpu_codex) == "3.4 to 4.4")
    # capacity vs gauge
    dev = [(M["cap_gauge"] / M["cap"] - 1) * 100 for M in MINUTES.values()]
    claim(1, "gauge slope differs from logged capacity by between 1% less and 9% more",
          rng(dev, "%.2f"), round(min(dev)) == -1 and round(max(dev)) == 9)
    # the counter sensitivity sentence (corrected page)
    m1 = [n for n in MINUTES if stand(n) == "M1, macOS 26"][0]
    MA = MINUTES_AFTER[m1]
    s1 = MINUTES[m1]["series"]
    fa = {a["arm"]: minutes_derived(MA, a["index"]) for a in s1.arms}
    fb = {a["arm"]: minutes_derived(MINUTES[m1], a["index"]) for a in s1.arms}
    claim(1, "read one tick later, the M1's ChatGPT runs: +13% front and -19% hidden rather than +51% and +7%; V x I +45% and +18%",
          "after: %+.1f / %+.1f; before: %+.1f / %+.1f; V x I: %+.1f / %+.1f" % (
              fa["chatgptstream-front"]["faster"], fa["chatgptstream-hidden"]["faster"],
              fb["chatgptstream-front"]["faster"], fb["chatgptstream-hidden"]["faster"],
              fb["chatgptstream-front"]["faster_vi"], fb["chatgptstream-hidden"]["faster_vi"]),
          (round(fa["chatgptstream-front"]["faster"]), round(fa["chatgptstream-hidden"]["faster"]),
           round(fb["chatgptstream-front"]["faster_vi"]), round(fb["chatgptstream-hidden"]["faster_vi"])) == (13, -19, 45, 18))
    ups = defaultdict(int)
    for name, M in MINUTES.items():
        t = [r for r in M["series"].bat if r["cc"] is not None and r["src"] == "Battery"]
        for i in range(1, len(t)):
            d_ = t[i]["cc"] - t[i - 1]["cc"]
            mac = "M1" if stand(name).startswith("M1,") else "M1 Pro"
            ups[mac] = max(ups[mac], d_)
    claim(1, "the counter climbs back by up to 55 mAh in one step on the M1 and up to 36 mAh on the M1 Pro machines",
          "M1 +%d, M1 Pro +%d" % (ups["M1"], ups["M1 Pro"]), (ups["M1"], ups["M1 Pro"]) == (55, 36))
    # how far every kept run moves when the counter is read one tick later
    shift = defaultdict(list)
    for name, M in MINUTES.items():
        for a in M["series"].arms:
            if a["arm"] == "control" or not kept(name, a["index"]):
                continue
            b_ = minutes_derived(M, a["index"])["faster"]
            a_ = minutes_derived(MINUTES_AFTER[name], a["index"])["faster"]
            shift[a["arm"]].append((stand(name), name[4:8], b_, a_))
    det = []
    for arm, v in shift.items():
        det.append("%s: published-reading range %s, one-tick-later range %s, largest move %.1f points (%s %s)" % (
            arm, rng([x[2] for x in v], "%+.1f"), rng([x[3] for x in v], "%+.1f"),
            max(abs(x[3] - x[2]) for x in v), *max(v, key=lambda x: abs(x[3] - x[2]))[:2]))
    claim(1, "(context) 'read the M1's counter figures, and every hidden run's, as rough': how much each kept run moves",
          "; ".join(det), True)

    # ---- item 2
    def ol(series_suffix, stand_, model):
        for (name, idx), n in OLLAMA.items():
            if name.endswith(series_suffix) and stand(name) == stand_ and model in Series.get(name).arm_index(idx)["arm"]:
                yield n
    b8 = [n for (name, idx), n in OLLAMA.items() if name.endswith("ollama-battery") and "8b" in Series.get(name).arm_index(idx)["arm"]]
    claim(2, "llama3.1:8b on battery: 1.6 to 2.1 J per token (V x I)", rng([n["jt_vi"] for n in b8], "%.3f"),
          rng([n["jt_vi"] for n in b8]) == "1.6 to 2.1")
    share = [n["jt_chip"] / n["jt_vi"] * 100 for n in b8]
    claim(2, "CPU+GPU account for 45 to 58% of the machine's energy per token", rng(share, "%.1f"),
          rng(share, "%.0f") == "45 to 58")
    kwh = [n["jt_vi"] * 1000 / 3600 for n in b8]
    claim(2, "1,000 tokens of 8B = 0.45 to 0.6 Wh", rng(kwh, "%.3f"),
          "%.2f" % min(kwh) == "0.45" and "%.1f" % max(kwh) == "0.6")
    m3 = [n for n in ol("ollama-battery", "M1, macOS 26", "3b")]
    claim(2, "llama3.2:3b about 0.9 J per token on the M1", "%.3f" % m3[0]["jt_vi"], round(m3[0]["jt_vi"], 1) == 0.9)
    mp = {stand(name): n for (name, idx), n in OLLAMA.items() if name.endswith("ollama-battery") and "8b" in Series.get(name).arm_index(idx)["arm"]}
    g = mp["M1 Pro, macOS 27"]["vi"] / mp["M1 Pro, macOS 14"]["vi"] - 1
    claim(2, "the two M1 Pro read 47.4 and 36.5 W, a 30% gap", "%.1f%%" % (g * 100), round(g * 100) == 30)

    # ---- item 3
    def ag(run, keep=True):
        out = []
        for (name, r), n in AGENTS.items():
            if r != run:
                continue
            a = Series.get(name).arms_named(r)[0]
            if keep and not kept(name, a["index"]):
                continue
            out.append((name, n))
        return out
    for run, printed_j, printed_min in (("codex-build-task", "523 to 887", "2.5 to 3.4"),
                                        ("copilot-build-task", "532 to 1739", "4.3 to 9.8"),
                                        ("claude-build-task", "372 to 1350", "2.7 to 7.5")):
        v = ag(run)
        got = rng([n["j"] for _, n in v], "%.0f")
        gm = rng([n["min_per_task"] for _, n in v], "%.1f")
        claim(3, "%s: %s J per task (%d runs), %s min" % (run, printed_j, len(v), printed_min),
              got + " J ; " + gm + " min", got == printed_j and gm == printed_min)
    for run, printed_j, printed_min in (("codex-x4-task", "577 to 1081", "4.4 to 7.3"),
                                        ("claude-x4-task", "546 to 859", "4.7 to 7.6")):
        v = ag(run)
        got = rng([n["j"] for _, n in v], "%.0f")
        gm = rng([n["all_done"] for _, n in v], "%.1f")
        claim(3, "%s: %s J per task (%d runs), all four done %s min" % (run, printed_j, len(v), printed_min),
              got + " J ; " + gm + " min", got == printed_j and gm == printed_min)
    allj = [n["j"] for (name, r), n in AGENTS.items()
            if kept(name, Series.get(name).arms_named(r)[0]["index"])]
    claim(3, "0.4 to 1.7 kJ per task (0.1 to 0.5 Wh)", rng([j / 1000 for j in allj], "%.3f") + " kJ",
          rng([j / 1000 for j in allj]) == "0.4 to 1.7")
    # four at once vs alone, "in the same series"
    # page (corrected): Codex 795 vs 657, 21% more; Claude Code 721 vs 752, 4% less
    for agent, printed in (("codex", "795 vs 657, +21%"), ("claude", "721 vs 752, -4%")):
        x4 = ag(agent + "-x4-task")
        x4series = set(nm for nm, _ in ag(agent + "-x4-task", keep=False))
        alone_same = [n["j"] for nm, n in ag(agent + "-build-task") if nm in x4series]
        alone_keptx4 = [n["j"] for nm, n in ag(agent + "-build-task") if nm in set(n2 for n2, _ in x4)]
        mx4 = sum(n["j"] for _, n in x4) / len(x4)
        ma = sum(alone_same) / len(alone_same)
        mb = sum(alone_keptx4) / len(alone_keptx4)
        got = "%.1f vs %.1f, %+.1f%% (alone over series with any x4 run, n=%d); alone over series with a kept x4 run: %.1f, %+.1f%% (n=%d)" % (
            mx4, ma, (mx4 / ma - 1) * 100, len(alone_same), mb, (mx4 / mb - 1) * 100, len(alone_keptx4))
        ok = ("%.0f vs %.0f, %+.0f%%" % (mx4, mb, (mx4 / mb - 1) * 100)).replace("+-", "-") == printed
        # the same means taken over the per-run values as printed (rounded to 1 J)
        rx4 = sum(round(n["j"]) for _, n in x4) / len(x4)
        ra = sum(round(j) for j in alone_same) / len(alone_same)
        got += "; from the printed per-run J: %.2f vs %.2f" % (rx4, ra)
        claim(3, "%s four at once vs alone: %s" % (agent, printed), got, ok)
    ratio = []
    for agent in ("codex", "claude"):
        for nm, n in ag(agent + "-x4-task"):
            single = [n2 for nm2, n2 in ag(agent + "-build-task") if nm2 == nm]
            if single:
                ratio.append(n["all_done"] / single[0]["min_per_task"])
    claim(3, "four finished in 0.8 to 2.5 times a single task, 1.8 on average",
          rng(ratio, "%.2f") + ", mean %.2f" % (sum(ratio) / len(ratio)),
          rng(ratio) == "0.8 to 2.5" and round(sum(ratio) / len(ratio), 1) == 1.8)

    # ---- item 4
    for app, label, printed in (("chatgptstream", "ChatGPT", ("0.19 to 0.49", "0.18 to 0.33", "0.08 to 0.11", "0.06 to 0.07")),
                                ("claudestream", "Claude Desktop", ("0.13 to 0.58", "0.21 to 0.88", "0.05 to 0.07", "0.05 to 0.08"))):
        for st, p in zip(("front", "behind", "hidden", "minimized"), printed):
            v = [n["above"] for (nm, a, s_), n in WINDOW.items() if a == app and s_ == st]
            got = rng(v, "%.3f")
            claim(4, "%s %s: +%s W" % (label, st, p), got, rng(v, "%.2f") == p)
    sav, sav_conf = [], []
    for (nm, a, st), n in WINDOW.items():
        if st not in ("hidden", "minimized"):
            continue
        fr = WINDOW[(nm, a, "front")]
        x = (1 - n["above"] / fr["above"]) * 100
        sav.append(x)
        y, u, no = fr["audit"]
        if u == 0 and no == 0:
            sav_conf.append(x)
    claim(4, "hiding or minimising removed 51 to 87% of the chip cost, about three quarters on average",
          rng(sav, "%.1f") + ", mean %.1f" % (sum(sav) / len(sav)),
          rng(sav, "%.0f") == "51 to 87")
    claim(4, "(context) strict reading: front audit all yes", rng(sav_conf, "%.2f"), True)
    sav_x = [(1 - n["above"] / WINDOW[(nm, a, "front")]["above"]) * 100
             for (nm, a, st), n in WINDOW.items()
             if st in ("hidden", "minimized") and not (nm.startswith("20261008T091621Z") and a == "chatgptstream")]
    claim(4, "(context) every Mac-day except the named M1 8 Oct ChatGPT",
          rng(sav_x, "%.2f"), True)
    # the page's own audit convention: Claude on the M1 answers inside the unsure band
    sav_y, sav_y_printed = [], []
    for (nm, a, st), n in WINDOW.items():
        if st not in ("hidden", "minimized"):
            continue
        fr = WINDOW[(nm, a, "front")]
        y, u, no = fr["audit"]
        claude_m1 = a == "claudestream" and stand(nm) == "M1, macOS 26"
        if (u == 0 and no == 0) or (claude_m1 and no == 0):
            sav_y.append((1 - n["above"] / fr["above"]) * 100)
            sav_y_printed.append((1 - round(n["above"], 3) / round(fr["above"], 3)) * 100)
    claim(4, "runs with confirmed answers gave 61 to 87% (READING: front all answered, Claude on the M1 counting its unsure band as answers)",
          rng(sav_y, "%.2f") + " unrounded; " + rng(sav_y_printed, "%.2f") + " from the printed 3-decimal watts",
          rng(sav_y, "%.0f") == "61 to 87")
    unsure = []
    apps_, states_ = set(), set()
    for (nm, a, st), n in sorted(WINDOW.items()):
        if nm.startswith("20261008") and (n["audit"][1] or n["audit"][2]) and st in ("front", "behind"):
            unsure.append("%s %s %s %d/%d/%d" % (stand(nm), a[:7], st, n["audit"][0], n["audit"][1], n["audit"][2]))
            apps_.add(a)
            states_.add(st)
    claim(4, "caption: several 8 Oct runs, for both apps, in front and behind another window, had prompts in the unsure or none band",
          "; ".join(unsure), len(apps_) == 2 and states_ == {"front", "behind"})
    hi = 0
    md = 0
    for (nm, a, st), n in WINDOW.items():
        if a == "claudestream" and st == "behind":
            md += 1
            hi += n["above"] > WINDOW[(nm, a, "front")]["above"]
    claim(4, "Claude read higher behind than in front in four of the five Mac-days", "%d of %d" % (hi, md), (hi, md) == (4, 5))

    # ---- item 5
    app_open = [n["asleep"] for f_, n in NIGHT.items() if f_.startswith("n1")]
    clean = [n["asleep"] for f_, n in NIGHT.items() if f_.startswith("n2")]
    claim(5, "asleep 96 to 99% with an app open, 98 to 99% with nothing open",
          rng(app_open, "%.2f") + " ; " + rng(clean, "%.2f"),
          rng(app_open, "%.0f") == "96 to 99" and rng(clean, "%.0f") == "98 to 99")
    hrs = [n["hours"] for n in NIGHT.values()]
    claim(5, "nights of 9.5 to 9.7 hours", rng(hrs, "%.3f"), rng(hrs, "%.1f") == "9.5 to 9.7")
    tbl = []
    for f_ in sorted(NIGHT):
        n = NIGHT[f_]
        tbl.append("%s %d (%d)" % (f_, n["dw"], n["wifibt"]))
    claim(5, "dark wakes 40 (0), 101 (64), 143 (108), 121 (88), 38 (0), 36 (0)", "; ".join(tbl), True)
    a = NIGHT.get("n1-claudecode-m1-pro-macos-27")
    b = NIGHT.get("n2-clean-m1-pro-macos-27")
    if a and b:
        claim(5, "the count moved 2.5 times on one M1 Pro", "%.3f" % (b["dw"] / a["dw"]), round(b["dw"] / a["dw"], 1) == 2.5)

    # ---- item 6: terminals, idle, leftovers, cursor, ollama idle, browser, threads
    def above_rows(key, runname):
        return [m for row, m, ms, ok, why in L.rows[key] if row["run"] == runname and not row["note"]]
    for run, printed in (("claude-idle-terminal", "-0.006 to +0.001"), ("claude-idle-iterm", "+0.015 to +0.025"),
                         ("claude-idle-ghostty", "+0.002 to +0.008"), ("claude-stream-terminal", "+0.027 to +0.045"),
                         ("claude-stream-iterm", "+0.054 to +0.098"), ("claude-stream-ghostty", "+0.038 to +0.071"),
                         ("shell-iterm", "-0.007 to +0.018"), ("shell-ghostty", "+0.000 to +0.011")):
        v = above_rows("terminals", run)
        got = rng(v, "%+.3f")
        norm = lambda s: s.replace("-0.000", "+0.000")
        claim(6, "terminal %s: %s W (%d runs)" % (run, printed, len(v)), got + " (%d runs)" % len(v), norm(got) == printed)
    v = [m for row, m, ms, ok, why in L.rows["leftovers"]]
    claim(6, "after closing: from 0.006 W below to 0.010 W above", rng(v, "%+.4f"), rng(v, "%+.3f") == "-0.006 to +0.010")
    for run, printed in (("chatgpt-front", "+0.07 to +0.10"), ("chatgpt-hidden", "+0.00 to +0.01"),
                         ("claudeapp-front", "+0.00 to +0.11"), ("claudeapp-hidden", "+0.00 to +0.01"),
                         ("cursor-front", "+0.03 to +0.05"), ("cursor-hidden", "+0.01 to +0.05")):
        v = [m for row, m, ms, ok, why in L.rows["idle"] if row["run"] == run]
        got = rng(v, "%+.2f").replace("-0.00", "+0.00")
        claim(6, "idle %s: %s W" % (run, printed), rng(v, "%+.4f"), got == printed)
    fr = [c["per"][k]["j"] for c in CURSOR.values() for k in c["per"] if k[0] == "front"]
    hid = []
    for nm, c in CURSOR.items():
        for k, v in c["per"].items():
            if k[0] == "hidden" and v["solved"]:
                hid.append(v["j"])
    claim(6, "Cursor 26 to 104 J per edit in front, 16 to 27 J hidden (solved tasks)",
          rng(fr, "%.1f") + " ; " + rng(hid, "%.1f"), rng(fr, "%.0f") == "26 to 104" and rng(hid, "%.0f") == "16 to 27")
    cc = [c["claude"] for c in CURSOR.values()]
    claim(6, "(CSV) Claude Code without a UI, whole run minus control: 3.0 to 5.4 J per task", rng(cc, "%.3f"),
          rng(cc, "%.1f") == "3.0 to 5.4")
    cw_ = [c["claude_win"] for c in CURSOR.values()]
    claim(6, "Claude Code without a UI 7.1 to 10.7 J per task, counted in the same 70 seconds around sending",
          rng(cw_, "%.3f"), rng(cw_, "%.1f") == "7.1 to 10.7")
    dbl = []
    for nm, c in sorted(CURSOR.items()):
        dbl.append("%s: sum of the three 70-s windows %.1f J vs the whole 600-s run %.1f J; one window over all three tasks %.2f J per task" % (
            stand(nm), c["claude_win_sum"], c["claude_run_sum"], c["claude_union"]))
    claim(6, "the per-task 70-s windows of Claude Code do not overlap (needed for 'same window' to be per task)",
          "; ".join(dbl), all(c["claude_win_sum"] <= c["claude_run_sum"] for c in CURSOR.values()))
    def ratios(base_key):
        f_r, h_r = [], []
        for nm, c in CURSOR.items():
            for k, v in c["per"].items():
                if k[0] == "front":
                    f_r.append(v["j"] / c[base_key])
                elif v["solved"]:
                    h_r.append(v["j"] / c[base_key])
        return f_r, h_r
    f_r, h_r = ratios("claude_win")
    claim(6, "Cursor's is 2.4 to 15 times that with its window in front and 1.5 to 3.8 times hidden",
          rng(f_r, "%.2f") + " ; " + rng(h_r, "%.2f"),
          ("%.1f" % min(f_r)) == "2.4" and round(max(f_r)) == 15
          and ("%.1f" % min(h_r)) == "1.5" and ("%.1f" % max(h_r)) == "3.8")
    for key, label in (("claude", "whole run minus control"), ("claude_union", "one window over all three tasks")):
        f2, h2 = ratios(key)
        claim(6, "(context) Cursor / Claude Code with Claude counted as %s" % label,
              "front " + rng(f2, "%.2f") + " ; hidden " + rng(h2, "%.2f"), True)
    hr = []
    for nm, c in CURSOR.items():
        f_ = [v["j"] for k, v in c["per"].items() if k[0] == "front"]
        h_ = [v["j"] for k, v in c["per"].items() if k[0] == "hidden" and v["solved"]]
        hr.append((sum(f_) / len(f_)) / (sum(h_) / len(h_)))
    claim(6, "hiding Cursor made each edit 1.7 to 3 times cheaper on average per Mac", rng(hr, "%.2f"),
          "%.1f" % min(hr) == "1.7" and round(max(hr)) == 3)
    pct = [v["j"] / (50 * 3600) * 100 for c in CURSOR.values() for k, v in c["per"].items() if k[0] == "front" or v["solved"]]
    claim(6, "0.01 to 0.06% of a battery of about 50 Wh", rng(pct, "%.4f"), rng(pct, "%.2f") == "0.01 to 0.06")
    wt = [c["waiting"] for c in CURSOR.values()]
    claim(6, "Cursor open and waiting, with the agents window and no folder, added +0.03 to 0.04 W",
          rng(wt, "%.4f"), rng(wt, "%.2f") == "0.03 to 0.04")
    li = {}
    for row, m, ms, ok, why in L.rows["ollama-idle"]:
        if row["metric"] == "W above control":
            li[(row["stand"], row["run"], row["series"][:16])] = m
    claim(6, "model loaded 1 h: -0.04 W (M1), -0.01 W (M1 Pro 14); server 30 min: -0.06 W, +0.02 W; first 10 min +0.14 to 0.26 W",
          "; ".join("%s %s %s %+.3f" % (k[2], k[0][:12], k[1], v) for k, v in sorted(li.items())), True)
    # browser
    def bw(series_prefix, armname):
        out = []
        for (nm, idx), b in BROWSER.items():
            if nm.startswith(series_prefix) and b["arm"]["arm"] == armname:
                out.append((idx, b))
        return sorted(out, key=lambda x: x[0])
    pairs = []
    for sp in ("20261009T165125Z", "20261009T090538Z", "20261009T165128Z", "20261009T090535Z"):
        app = bw(sp, "chatgptstream-front")
        saf = bw(sp, "safari-chatgpt")
        for i, b in saf:
            if b["audit"][0] != 3:
                continue
            near = min(app, key=lambda x: abs(x[0] - i))[1]
            if near["audit"][0] == 3:
                pairs.append(b["above"] / near["above"])
    claim(6, "Safari/app for ChatGPT on 9 October: 1.7 to 2.5 times across four fully answered pairs",
          rng(pairs, "%.3f") + " (%d pairs, nearest app run)" % len(pairs),
          len(pairs) == 4 and rng(pairs, "%.1f") == "1.7 to 2.5")
    for sp, printed in (("20261011T135602Z", "about 0.9"), ("20261012T131330Z", "about 1.0 (0.60 against 0.62 W)")):
        app = [b["above"] for _, b in bw(sp, "chatgptstream-front")]
        saf = [b["above"] for _, b in bw(sp, "safari-chatgpt")]
        r_ = (sum(saf) / len(saf)) / (sum(app) / len(app))
        claim(6, "Safari/app for ChatGPT %s: %s" % (sp[:8], printed),
              "%.3f (Safari %.4f, app %.4f)" % (r_, sum(saf) / len(saf), sum(app) / len(app)),
              ("%.1f" % r_) == printed.split()[1])
    # long thread
    rel, jumps, gpt, cla = [], [], [], []
    for (nm, name), ws in THREADS.items():
        use = ws
        if nm.startswith("20261012T064545Z") and name == "chatgptthread-front":
            use = ws[:7]    # messages 8-12 not used (limit)
        if len(use) >= 6:
            f3 = sum(use[:3]) / 3
            l3 = sum(use[-3:]) / 3
            rel.append((l3 / f3 - 1) * 100)
        for i in range(1, len(use)):
            jumps.append(max(use[i] / use[i - 1], use[i - 1] / use[i]))
        (gpt if name.startswith("chatgpt") else cla).extend(use)
    claim(6, "thread: last three vs first three from 64% lower to 34% higher", rng(rel, "%+.1f"),
          rng(rel, "%+.0f") == "-64 to +34")
    jr = []
    for (nm, name), ws in THREADS.items():
        use = ws[:7] if (nm.startswith("20261012T064545Z") and name == "chatgptthread-front") else ws
        u = [round(x, 2) for x in use]
        for i in range(1, len(u)):
            jr.append(max(u[i] / u[i - 1], u[i - 1] / u[i]))
    claim(6, "thread: message-to-message jump up to 2.5 times",
          "%.3f (from the printed 2-decimal values: %.3f)" % (max(jumps), max(jr)), round(max(jumps), 1) == 2.5)
    first9, cla_rest = [], []
    for (nm, name), ws in THREADS.items():
        if not name.startswith("claude"):
            continue
        k = 0
        if nm.startswith("20261009"):
            # READING: "started expensive and dropped after the first one to four
            # messages" = the leading messages above 0.4 W
            while k < len(ws) and ws[k] > 0.4:
                k += 1
            first9 += ws[:k]
        cla_rest += ws[k:]
    gpt_wo = sorted(gpt)[:-1]
    claim(6, "thread: ChatGPT 0.4 to 0.7 W per message (bar the 1.07 outlier), Claude 0.17 to 0.5 W, Claude's first 9 Oct messages up to 1.0 W",
          "ChatGPT " + rng(gpt_wo, "%.3f") + "; Claude after the expensive start " + rng(cla_rest, "%.3f")
          + "; the expensive start (%d messages) max %.3f" % (len(first9), max(first9)),
          rng(gpt_wo) == "0.4 to 0.7" and rng(cla_rest, "%.1f") == "0.2 to 0.5" and round(max(first9), 1) == 1.0)
    # browser, Claude
    cl_r, cl_gap = [], []
    for sp in ("20261009T090535Z", "20261009T090538Z", "20261012T082546Z"):
        app = [b["above"] for _, b in bw(sp, "claudestream-front")]
        for br in ("safari-claude", "chrome-claude"):
            tab = [b["above"] for _, b in bw(sp, br)]
            cl_r.append("%s %s x%.3f" % (stand([n for n in BROWSER if n[0].startswith(sp)][0][0])[:16], br[:6],
                                          (sum(tab) / len(tab)) / (sum(app) / len(app))))
            cl_gap.append(max(tab) - min(app))
    claim(6, "Claude tab vs app: M1 1.3 to 1.5 times, M1 Pro 27 within 15%, widest gap 0.13 W",
          "; ".join(cl_r) + "; widest (best tab run minus lowest app run) %.4f W" % max(cl_gap), True)
    # Chrome prompts that went through, 12 October
    def yes_prompts(sp, armname):
        out = []
        for _, b in bw(sp, armname):
            out += [w for w in b["ws"] if audit_class(w, AUDIT_ROUND) == "y"]
        return out
    c27 = yes_prompts("20261012T131330Z", "chrome-chatgpt")
    a27 = yes_prompts("20261012T131330Z", "chatgptstream-front")
    c1 = yes_prompts("20261012T014718Z", "chrome-chatgpt")
    a1 = yes_prompts("20261012T014718Z", "chatgptstream-front")
    claim(6, "Chrome 12 October: M1 Pro 27 0.33 to 0.67 W against the app's 0.52 to 0.86 W; M1 0.25 and 0.46 W against 0.32 to 0.45 W",
          "M1 Pro 27: Chrome " + rng(c27, "%.3f") + " vs app " + rng(a27, "%.3f")
          + "; M1: Chrome " + ", ".join("%.3f" % x for x in c1) + " vs app " + rng(a1, "%.3f"),
          rng(c27, "%.2f") == "0.33 to 0.67" and rng(a27, "%.2f") == "0.52 to 0.86"
          and sorted("%.2f" % x for x in c1) == ["0.25", "0.46"] and rng(a1, "%.2f") == "0.32 to 0.45")


# --------------------------------------------------------------------------
# Alternatives: how the matches move when a reading changes
# --------------------------------------------------------------------------

ALT = []


def alternatives():
    # (a) charge counter at the boundary
    for rule in ("before", "after", "nearest"):
        Mx = compute_minutes(rule)
        n_ok = n = 0
        heads = []
        for name, M in Mx.items():
            for a in M["series"].arms:
                d = minutes_derived(M, a["index"])
                if a["arm"] in ("chatgptstream-front", "chatgptstream-hidden") and kept(name, a["index"]):
                    heads.append("%s %s %s %+.1f%%" % (stand(name)[:16], name[4:8], a["arm"][14:], d["faster"]))
        rows = [r for r in section_rows("minutes") if r["metric"] == "mAh drained in 30 min"]
        seen = defaultdict(int)
        for r in rows:
            k = (r["series"], r["run"])
            seen[k] += 1
            arm = Mx[r["series"]]["series"].arms_named(r["run"])[seen[k] - 1]
            v = Mx[r["series"]]["nums"][arm["index"]]["drain"]
            n += 1
            n_ok += ("%.0f" % v) == r["value"]
        ALT.append(("1 charge counter at boundary = %s tick" % rule, "%d/%d mAh rows match" % (n_ok, n),
                    "; ".join(heads)))
    # (a2) is the charge counter monotonic on battery? A pure integral of current is.
    det = []
    for name, M in MINUTES.items():
        s = M["series"]
        t = [r for r in s.bat if r["cc"] is not None and r["src"] == "Battery"]
        steps = [t[i]["cc"] - t[i - 1]["cc"] for i in range(1, len(t)) if t[i]["cc"] != t[i - 1]["cc"]]
        ups = [x for x in steps if x > 0]
        det.append("%s %s: %d counter changes on battery, %d of them UP (largest +%d mAh, sum +%d mAh); largest single drop %d mAh" % (
            name[:16], stand(name), len(steps), len(ups), max(ups) if ups else 0, sum(ups), min(steps)))
    ALT.append(("1b AppleRawCurrentCapacity on battery in the minutes series", "", "; ".join(det)))
    # (b) chip snapshot assignment, on every chip-W row of minutes/window/terminals/idle/browser
    targets = []
    for r in section_rows("minutes"):
        if r["metric"] in ("chip CPU, W", "chip GPU, W"):
            targets.append(r)
    for rule in ("mid", "inside", "end", "overlap"):
        ok = n = 0
        seen = defaultdict(int)
        for r in targets:
            s = Series.get(r["series"])
            k = (r["series"], r["run"], r["metric"])
            seen[k] += 1
            arm = s.arms_named(r["run"])[seen[k] - 1]
            key = ("cpu",) if "CPU" in r["metric"] else ("gpu",)
            v = arm_chip_w(s, arm, key, rule)
            n += 1
            ok += same(r["value"], fmt_like(r["value"], v))
        ok2 = n2 = 0
        for r in section_rows("browser") + section_rows("threads"):
            if not r["metric"].startswith("chip W 140 s"):
                continue
            s = Series.get(r["series"])
            if r["section"].startswith("long thread"):
                arm = s.arms_named("chatgptthread-front" if r["run"] == "ChatGPT" else "claudethread-front")[0]
            else:
                arm = s.arm_index(int(RUN_N.search(r["run"]).group(1)))
            ps = prompts_for_arm(s, arm)
            kk = int(r["metric"].split()[-1])
            v = chip_w(s.chip, ps[kk - 1], ps[kk - 1] + 140, rule=rule)
            n2 += 1
            ok2 += same(r["value"], fmt_like(r["value"], v))
        ALT.append(("2 chip snapshot rule = %s" % rule,
                    "minutes CPU/GPU %d/%d, per-prompt 140 s %d/%d" % (ok, n, ok2, n2), ""))
    # (c) audit rounding
    for rnd in (None, 2):
        ok = n = 0
        bad = []
        for key in ("minutes", "window", "browser"):
            for row, mine, ms, okk, why in L.rows[key]:
                if "answers" not in row["metric"]:
                    continue
                s = Series.get(row["series"])
                if key == "window":
                    app = "chatgptstream" if row["run"] == "ChatGPT" else "claudestream"
                    st = STATE[row["metric"].split(",")[0]]
                    ws = []
                    for a in s.arms_named("%s-%s" % (app, st)):
                        ws += [audit_w(s, t) for t in prompts_for_arm(s, a)]
                elif key == "browser":
                    ws = BROWSER[(row["series"], int(RUN_N.search(row["run"]).group(1)))]["ws"]
                else:
                    cands = s.arms_named(row["run"])
                    ws = None
                    for a in cands:
                        w_ = [audit_w(s, t) for t in prompts_for_arm(s, a)]
                        if "%d/%d/%d" % audit_counts(w_, 2) == mine:
                            ws = w_
                    if ws is None:
                        continue
                got = "%d/%d/%d" % audit_counts(ws, rnd)
                n += 1
                if got == row["value"]:
                    ok += 1
                else:
                    bad.append("%s %s %s: published %s, got %s" % (short(row["series"]), row["run"], row["metric"][:20], row["value"], got))
        ALT.append(("3 audit threshold on %s value" % ("unrounded" if rnd is None else "0.01-rounded"),
                    "%d/%d answer rows match" % (ok, n), "; ".join(bad)))
    # (d) control choice for agents and window states
    ok = n = 0
    for (name, run), nn in AGENTS.items():
        s = Series.get(name)
        alt = agent_numbers(s, s.arms_named(run)[0], control="prev")
        r = [x for x in section_rows("agents") if x["series"] == name and x["run"] == run and x["metric"] == "J per task"][0]
        n += 1
        ok += ("%.0f" % alt["j"]) == r["value"]
    ALT.append(("4 agents: control = preceding control only", "%d/%d J-per-task rows match" % (ok, n), ""))
    ok = n = 0
    for (name, run), nn in AGENTS.items():
        s = Series.get(name)
        alt = agent_numbers(s, s.arms_named(run)[0], window="measure")
        r = [x for x in section_rows("agents") if x["series"] == name and x["run"] == run and x["metric"] == "J per task"][0]
        n += 1
        ok += ("%.0f" % alt["j"]) == r["value"]
    ALT.append(("5 agents: measure window instead of whole arm", "%d/%d J-per-task rows match" % (ok, n), ""))
    ok = n = 0
    for (name, app, st), nn in WINDOW.items():
        s = Series.get(name)
        alt = window_numbers(s, app, st, control="bracket")
        label = "ChatGPT" if app == "chatgptstream" else "Claude Desktop"
        mlabel = [k for k, v in STATE.items() if v == st][0] + ", chip W above control"
        r = [x for x in section_rows("window") if x["series"] == name and x["run"] == label and x["metric"] == mlabel][0]
        n += 1
        ok += same(r["value"], fmt_like(r["value"], alt["above"]))
    ALT.append(("6 window states: control = mean of the two controls around each run",
                "%d/%d rows match" % (ok, n), ""))
    # (e) ChatGPT processes: subject.csv vs coalitions.csv
    det = []
    for name, M in MINUTES.items():
        s = M["series"]
        co = s.log("coalitions.csv")
        for a in s.arms:
            if not a["arm"].startswith("chatgptstream"):
                continue
            ms, me = a["measure_start"], a["measure_end"]
            ce = sum(float(x["energy_mj_since_previous_tick"]) for x in co
                     if x["coalition"] == "ChatGPT" and ms < float(x["t"]) <= me) / 1000 / (me - ms)
            tot = sum(x["e"] for x in s.subject if x["arm"] == a["arm"] and x["index"] == a["index"]) / 1000 / (me - ms)
            det.append("%s %s: subject in window %.4f, subject whole arm %.4f, ChatGPT coalition %.4f" % (
                name[4:16], a["arm"][14:], subject_w(s, a), tot, ce))
    ALT.append(("7 ChatGPT processes W: what the files give", "", "; ".join(det)))
    # ANE share
    e_ane = e_cg = 0.0
    nser = 0
    for name in sorted(os.listdir(RUNS)):
        s = Series.get(name)
        if not s.chip:
            continue
        nser += 1
        e_ane += sum(c["ane"] for c in s.chip)
        e_cg += sum(c["cpu"] + c["gpu"] for c in s.chip)
    ALT.append(("8 Neural Engine share of CPU+GPU energy (page: 0.003% across the 44 series with chip readings)",
                "%.4f%% across %d series with chip.csv (%d run folders in data/runs)" % (
                    e_ane / e_cg * 100, nser, len(os.listdir(RUNS))), ""))
    # battery table on the page vs the journals
    per = defaultdict(lambda: dict(cyc=set(), cap=[], capmin=[], capbat=[]))
    for name in sorted(os.listdir(RUNS)):
        s = Series.get(name)
        mac = s.meta["machine"]
        for j in s.journal:
            if j["cycles"]:
                per[mac]["cyc"].add(int(j["cycles"]))
            key = "fcc_mah" if j["fcc_mah"] else "raw_max_mah"
            if j.get(key):
                per[mac]["cap"].append(float(j[key]))
                if "minutes" in name:
                    per[mac]["capmin"].append(float(j[key]))
                if j.get("source_start") == "Battery":
                    per[mac]["capbat"].append(float(j[key]))
    txt = []
    for mac, d in sorted(per.items()):
        txt.append("%s: cycles %d-%d, capacity all series %.0f-%.0f, every run on battery %.0f-%.0f, 30-minute minutes series %.0f-%.0f" % (
            mac, min(d["cyc"]), max(d["cyc"]), min(d["cap"]), max(d["cap"]), min(d["capbat"]), max(d["capbat"]),
            min(d["capmin"]), max(d["capmin"])))
    ALT.append(("9 battery table (page: 1719-1721 cyc, 4391-4436 mAh; 533, 4212-4269; 1047-1048, 4727-4773; capacity 'in the logs of the battery runs')",
                "", "; ".join(txt)))
    # brightness
    br = defaultdict(set)
    for name in sorted(os.listdir(RUNS)):
        s = Series.get(name)
        for j in s.journal:
            if j.get("brightness"):
                br[s.meta["machine"]].add(round(float(j["brightness"]), 3))
    ALT.append(("10 brightness (page: 0.687 on the M1 Pro machines, 0.712 on the M1)", "",
                "; ".join("%s %s" % (k, sorted(v)) for k, v in sorted(br.items()))))


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

ITEM_OF = {"minutes": 1, "ollama": 2, "agents": 3, "window": 4, "nights": 5}


def report():
    print("Independent replication of results/ai-battery-usage-mac.csv")
    print("=" * 78)
    tot_n = tot_ok = 0
    for key in SECTION_KEYS:
        rows = L.rows[key]
        sk = L.skipped[key]
        ok = sum(1 for x in rows if x[3])
        tot_n += len(rows)
        tot_ok += ok
        print("\n[%s] %s" % (key, SECTION_KEYS[key]))
        print("  compared %d rows, matched as printed %d, mismatched %d, not compared %d"
              % (len(rows), ok, len(rows) - ok, len(sk)))
        by_metric = defaultdict(lambda: [0, 0])
        for row, mine, ms, okk, why in rows:
            by_metric[row["metric"]][0] += 1
            by_metric[row["metric"]][1] += okk
        for m, (n, k) in by_metric.items():
            if k != n:
                print("    metric %-55s %d/%d" % (m[:55], k, n))
        for row, mine, ms, okk, why in rows:
            if (not okk) or SHOW_ROWS:
                d = numeric_diff(row["value"], mine)
                print("    %s %-8s %-18s %-26s %-40s published %-12s mine %-12s raw %s%s%s" % (
                    "  " if okk else "!!", short(row["series"]), row["stand"][:18], row["run"][:26],
                    row["metric"][:40], row["value"], ms, raw(mine),
                    ("  diff %+.4g" % d) if d is not None else "",
                    ("  [" + row["note"][:50] + "]") if row["note"] else ""))
        for row, why in sk:
            if SHOW_ROWS or why != "published value empty (dropped run)":
                print("    -- skipped %s %s %s: %s" % (short(row["series"]), row["run"], row["metric"], why))
    print("\nTOTAL compared %d, matched %d, mismatched %d, rows in file %d" % (
        tot_n, tot_ok, tot_n - tot_ok, len(RESULT_ROWS)))
    covered = set(r["_i"] for rows in L.rows.values() for r, *_ in rows) | set(
        r["_i"] for rows in L.skipped.values() for r, _ in rows)
    left = [r for r in RESULT_ROWS if r["_i"] not in covered]
    print("rows in no handled section: %d" % len(left))

    print("\nPAGE CLAIMS (prose and tables)")
    print("=" * 78)
    for item, printed, got, ok in CLAIMS:
        print("  [%s] item %s  %s\n          recomputed: %s" % ("ok" if ok else "!!", item, printed, got))

    print("\nALTERNATIVE READINGS")
    print("=" * 78)
    for name, summary, detail in ALT:
        print("  %s: %s" % (name, summary))
        if detail:
            for part in detail.split("; "):
                print("      " + part)


def main():
    do_minutes()
    do_ollama()
    do_agents()
    do_window()
    do_nights()
    do_simple_above("terminals")
    do_simple_above("idle")
    do_simple_above("leftovers", {"after Copilot": "copilot-afterhalf", "after Codex": "codex-afterhalf",
                                  "after Claude Code": "claude-afterhalf"})
    do_browser()
    do_threads()
    do_cursor()
    do_ollama_idle()
    page_claims()
    alternatives()
    report()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Check every number of the results file against a recomputation from the public data.

    /usr/bin/python3 scripts/check.py [section ...]

Runs the computations of recompute.py (same folder) over ../data and compares each row with
../results/ai-battery-usage-mac.csv. A pair of rows agrees when
- the value, formatted the way the results file prints it, is the same string;
- the machine (`stand`), the power source (`power`), the `day` and the `instrument` are the same strings;
- both notes are empty or both are not (the wording of a note may differ, its presence may not).

Rows are matched by (section, series, run, metric). The results file repeats some of these keys: a
minutes series has two `control` runs, an Ollama series repeats each model run, three clean nights share
one series name, and the battery-capacity rows of the minutes section carry no series. Such rows are told
apart by the machine (`stand`) and then by their order, which is time order in both files.

Prints rows checked, matched, values that differ (both values), rows whose other columns differ, results-file
rows the recomputation does not produce, and recomputed rows the results file does not have. The results-file
rows recompute.py does not recompute (recompute.not_recomputed() names them) are counted apart, with the reason
recompute.NOT_RECOMPUTED gives; run over every section, so are the rows of any section that is neither recomputed
nor named there.

Exits 1 when any value or other column differs, when a results-file row is not produced, when a recomputed row is
not in the results file, or (over every section) when the results file has rows that are neither recomputed nor
named in NOT_RECOMPUTED, or when the number of rows of a NOT_RECOMPUTED reason differs from
recompute.NOT_RECOMPUTED_ROWS; 0 otherwise.
"""
import csv
import hashlib
import os
import re
import sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True  # no __pycache__ next to the scripts
import recompute  # noqa: E402

RESULTS = os.path.join(os.path.dirname(HERE), "results", "ai-battery-usage-mac.csv")

# The results file prints the capacity rows of the minutes section with an empty series and run.
NO_SERIES_METRICS = {"battery capacity as the battery log reports it, mAh",
                     "battery capacity by the slope of the percent gauge, mAh"}
# Columns besides the value that must be the same string in both files.
SAME = ("stand", "power", "day", "instrument")


def results_key(rec):
    """The key under which the results file prints this recomputed number."""
    series, run = rec["series"], rec["run"]
    if rec["metric"] in NO_SERIES_METRICS:
        series, run = "", ""
    if series.startswith("nights/"):
        # The night folders end in the machine (n2-clean-m1-pro-macos-27); the results file names the
        # night without it and gives the machine in `stand`.
        slug = re.sub(r"[^a-z0-9]+", "-", rec["stand"].lower()).strip("-")
        if series.endswith("-" + slug):
            series = series[:-len(slug) - 1]
    return rec["section"], series, run, rec["metric"]


def index(rows, keyfn):
    """key -> rows in order."""
    out = OrderedDict()
    for r in rows:
        out.setdefault(keyfn(r), []).append(r)
    return out


def pair(published, mine):
    """Pairs of (published row or None, recomputed row or None) for one key."""
    if len(published) == 1 and len(mine) == 1:
        return [(published[0], mine[0])]
    out = []
    stands = []
    for r in published + mine:
        if r["stand"] not in stands:
            stands.append(r["stand"])
    for st in stands:
        a = [r for r in published if r["stand"] == st]
        b = [r for r in mine if r["stand"] == st]
        for k in range(max(len(a), len(b))):
            out.append((a[k] if k < len(a) else None, b[k] if k < len(b) else None))
    return out


def differences(p, m):
    """The columns other than the value in which a pair of rows disagrees."""
    out = [(col, p[col], m[col]) for col in SAME if p[col] != m[col]]
    if bool(p["note"]) != bool(m["note"]):
        out.append(("note", p["note"] or "(empty)", m["note"] or "(empty)"))
    return out


SECTION_KEYS = {name: key for key, (name, _instrument) in recompute.SECTIONS.items()}


def label(key, stand):
    section, series, run, metric = key
    return "%s | %s | %s | %s | %s" % (SECTION_KEYS.get(section, section), series or "(no series)", stand,
                                       run or "(no run)", metric)


def main(argv):
    keys = argv[1:] or list(recompute.SECTIONS)
    names = {recompute.SECTIONS[k][0] for k in keys if k in recompute.SECTIONS}
    every_section = not argv[1:]
    with open(RESULTS, newline="") as f:
        everything = list(csv.DictReader(f))
    known = {name for name, _instrument in recompute.SECTIONS.values()}
    published = []
    apart = OrderedDict()  # NOT_RECOMPUTED reason -> rows
    unknown = OrderedDict()  # section neither recomputed nor named in NOT_RECOMPUTED -> rows
    for r in everything:
        why = recompute.not_recomputed(r)
        if why:
            if every_section or r["section"] in names:
                apart.setdefault(why, []).append(r)
        elif r["section"] in names:
            published.append(r)
        elif r["section"] not in known and every_section:
            unknown.setdefault(r["section"], []).append(r)
    counts = OrderedDict()  # NOT_RECOMPUTED reason -> (rows found, rows expected), where the two differ
    pinned = []  # NOT_RECOMPUTED reasons whose rows differ from recompute.NOT_RECOMPUTED_SHA256
    if every_section or recompute.SECTIONS["wattmate"][0] in names:
        for why, n in recompute.NOT_RECOMPUTED_ROWS.items():
            if len(apart.get(why, [])) != n:
                counts[why] = (len(apart.get(why, [])), n)
        for why, digest in recompute.NOT_RECOMPUTED_SHA256.items():
            text = "\n".join("\t".join(r[col] for col in recompute.NOT_RECOMPUTED_COLUMNS) for r in apart.get(why, []))
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
                pinned.append(why)
    mine = []
    for sec in recompute.compute(keys):
        mine += sec.records()

    pub_ix = index(published, lambda r: (r["section"], r["series"], r["run"], r["metric"]))
    my_ix = index(mine, results_key)
    matched, mismatched, missing, extra, passport = [], [], [], [], []
    for key in list(pub_ix) + [k for k in my_ix if k not in pub_ix]:
        for p, m in pair(pub_ix.get(key, []), my_ix.get(key, [])):
            if p is None:
                extra.append((key, m))
                continue
            if m is None:
                missing.append((key, p))
                continue
            diff = differences(p, m)
            if diff:
                passport.append((key, p, diff))
            if p["value"] != m["value"]:
                mismatched.append((key, p, m))
            elif not diff:
                matched.append((key, p, m))

    print("results file: results/%s" % os.path.basename(RESULTS))
    print("sections: %s" % " ".join(keys))
    print("rows checked: %d" % len(published))
    print("matched (value and every other column): %d" % len(matched))
    print("values that differ: %d" % len(mismatched))
    for key, p, m in mismatched:
        print("  %s: results file %r, recomputed %r" % (label(key, p["stand"]), p["value"], m["value"]))
    print("rows whose machine, power source, day, instrument or note (empty or not) differ: %d" % len(passport))
    for key, p, diff in passport:
        print("  %s: %s" % (label(key, p["stand"]), "; ".join(
            "%s: results file %r, recomputed %r" % d for d in diff)))
    print("results-file rows not produced: %d" % len(missing))
    for key, p in missing:
        print("  %s: %r" % (label(key, p["stand"]), p["value"]))
    print("recomputed rows not in the results file: %d" % len(extra))
    for key, m in extra:
        print("  %s: %r" % (label(key, m["stand"]), m["value"]))
    if apart or every_section:
        print("results-file rows not recomputed from data/: %d of %d"
              % (sum(len(v) for v in apart.values()), len(everything) if every_section else
                 sum(len(v) for v in apart.values()) + len(published)))
        for why, rows in apart.items():
            print("  %d rows: %s" % (len(rows), recompute.NOT_RECOMPUTED[why]))
    if unknown:
        print("results-file rows neither recomputed nor named in recompute.NOT_RECOMPUTED: %d"
              % sum(len(v) for v in unknown.values()))
        for name, rows in unknown.items():
            print("  %s: %d rows" % (name, len(rows)))
    for why, (found, n) in counts.items():
        print("results-file rows for %r: %d, expected %d" % (why, found, n))
    for why in pinned:
        print("results-file rows for %r differ from the rows pinned in recompute.NOT_RECOMPUTED_SHA256" % why)
    return 1 if mismatched or passport or missing or extra or unknown or counts or pinned else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

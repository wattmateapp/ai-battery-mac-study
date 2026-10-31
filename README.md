# Does AI drain your Mac battery? Data and code

This archive holds the data and code behind the study
[Does AI drain your Mac battery?](https://wattmateapp.com/ai-battery-usage-mac). It covers ChatGPT, Claude Desktop,
Claude Code, Codex CLI, Copilot CLI, Cursor and Ollama, measured on 5 Apple silicon Macs between 5 and
24 October 2026 (see "The Macs").

The measurements do not come from [WattMate](https://wattmateapp.com), a battery app for the Mac. They come from
`powermetrics`, the battery's own counters and a per-process collector. WattMate was closed during the runs, with
4 exceptions that the page names:

| Case | Mac | Series | WattMate open | In `data/` |
|---|---|---|---|---|
| The 2 window series of 14 October | M1 Pro, macOS 27 | `20261014T094238Z-window-vs-answer`, `20261014T144957Z-window-vs-textedit` | in every run, controls included | yes |
| 1 extra run | M3 Max, macOS 27 | `20261013T214438Z-m3-minutes-m3`, run `chatgptstream-front-wattmate` | in that run only | yes |
| The Release check of 15 October | M1 Pro, macOS 27, and M1, macOS 26 | `20261015T095458Z-wattmate-release`, `20261015T095459Z-wattmate-release` | in every run, controls included | yes |
| WattMate compared with the lab's instruments, 13 October | M1, macOS 26 | `20261013T170744Z-agents15-recon-battery` | through the whole series | no |

Every number on the study page can be recomputed from these files with the scripts in `scripts/`, except 82 of the
111 rows that compare WattMate with the lab's instruments (see "What is not here, on purpose") and the check of
WattMate 1.6, whose series are not in this version (see "Version 1.1").

## Version 1.1

Tests added on a MacBook Air M5, macOS 27, 24 October 2026:

- [x] ChatGPT window in front and hidden, ABBA order
- [x] Claude Code (Haiku) build tasks against the same builds with no agent
- [x] Ollama llama3.1:8b answering back to back

What changed:

* 3 folders in `data/runs/`
* 198 rows in `results/`
* nothing else in `results/` or `data/` changed

Planned for version 1.2:

- [ ] WattMate 1.6 against the lab's instruments (the page's figures for this check are not in `results/` of 1.1):
  * M1 Pro, macOS 27
  * M1, macOS 26
  * MacBook Air M5, macOS 27
- [ ] MacBook Air M5: ChatGPT window with and without answers
- [ ] MacBook Air M5: battery minutes
- [ ] MacBook Air M5: Ollama night
- [ ] MacBook Air M5: Codex in a terminal
- [ ] MacBook Air M5: long thread
- [ ] MacBook Air M5: Claude Code in 3 terminals
- [ ] MacBook Air M5: agents on a build task
- [ ] MacBook Air M5: browser tabs against apps
- [ ] MacBook Air M5: Cursor
- [ ] MacBook Air M5: Ollama on battery
- [ ] MacBook Air M5: ChatGPT window against a TextEdit window

## What is here

```
results/ai-battery-usage-mac.csv   every number on the page, one row each, with the series it came from (none on the
                                   battery-capacity rows, which give the machine and day, or on the 2 Neural Engine
                                   rows, which sum every series)
data/runs/<series>/                one folder per measurement series (one Mac, one session)
data/nights/<night>/               overnight sleep logs, reduced to events
scripts/recompute.py               recomputes from data/ every number that data/ allows
scripts/check.py                   compares the recomputation with results/, row by row
independent/replicate.py           a replication written from the page's method alone (see "Checked twice")
```

A series folder is named by its start time in UTC and its design, for example `20261011T141657Z-minutes-battery`.
The Mac a series ran on is in its `meta.json` (`"machine": "M1, macOS 26"`).

## The Macs

The first column is how the data names each Mac, in the `machine` field of `meta.json` and in the `stand` column of
`results/`: the chip and the major macOS version. The exact version and build are in the third column. The M3 Max ran
only the ChatGPT window series of 13 and 14 October, and the MacBook Air M5 only the 3 series of 24 October.

| Name in the data | Model | macOS | Display | Battery | Setup |
|---|---|---|---|---|---|
| M1 Pro, macOS 27 | MacBook Pro 14-inch (MacBookPro18,3), 16 GB | 27.0.1 (26A434) | mini-LED, ProMotion | 75%, 1,719 to 1,723 cycles | Spotlight, automatic updates and Bluetooth off; brightness pinned; display awake during a series |
| M1, macOS 26 | MacBook Pro 13-inch (MacBookPro17,1), 16 GB | 26.7.1 (25G241) | LCD, 60 Hz | 85%, 532 to 535 cycles | Spotlight, automatic updates and Bluetooth off; brightness pinned; display awake during a series |
| M1 Pro, macOS 14 | MacBook Pro 14-inch (MacBookPro18,3), 16 GB | 14.2.1 (23C71) | mini-LED, ProMotion | 81%, 1,046 to 1,049 cycles | Spotlight, automatic updates and Bluetooth off; brightness pinned; display awake during a series |
| M3 Max, macOS 27 | MacBook Pro 14-inch (Mac15,10), 36 GB | 27.0 (26A428) | mini-LED, ProMotion | 86%, 617 cycles | Spotlight, automatic updates and Bluetooth off (set by hand, not checked by the runner); brightness pinned; display awake during a series |
| MacBook Air M5, macOS 27 | MacBook Air (Mac17,3), 16 GB | 27.0.1 (26A434) | LCD | 3 to 5 cycles | Spotlight, automatic updates and Bluetooth off; brightness pinned; display awake during a series; no `powermetrics` |

## Files in a series folder

All times are Unix seconds (UTC). Energy is in millijoules (mJ), power in watts (W).

| File | One row per | Columns |
|---|---|---|
| `arms.csv` | arm (one condition) | `arm`, `index`, `arm_start`, `measure_start`, `measure_end`, `arm_stop`. A condition starts at `arm_start`; the measured window runs from `measure_start` to `measure_end`, after a warm-up. `control` is the same Mac with the screen on and nothing running. |
| `chip.csv` | 5-second `powermetrics` snapshot | `t_end`, `seconds`, `cpu_mj`, `gpu_mj`, `ane_mj`. "Chip" on the page is CPU plus GPU. A snapshot belongs to a window when its midpoint (`t_end − seconds/2`) lies inside it. 12 series have no `chip.csv`: the 3 `k` calibration series, the 4 parts of the Ollama night, the 2 M3 Max series and the 3 MacBook Air M5 series. |
| `battery.csv` | collector tick, about every 10 s | `t` (the tick's time), `power_source` (`AC` or `Battery`), `charge_counter_mah` (AppleRawCurrentCapacity), `percent`, `voltage_mv`, `pack_current_ma`, `system_power_in_mw`, and `whole_mac_w`, which is power in from the cable minus pack power: the "voltage times current" reading on the page. |
| `subject.csv` | tick inside an arm | `t`, `arm`, `index`, `subject`, `energy_mj_since_previous_tick`: energy used since the previous tick by the processes of the app or agent the arm measures (ChatGPT.app in a ChatGPT arm, TextEdit.app in a TextEdit arm, the agent's process tree in an agent arm). |
| `processes.csv` | tick inside an arm and process group | Only in the 4 window series of 14 October and the 2 series of the Release check of 15 October: `t`, `arm`, `index`, `process`, `energy_mj_since_previous_tick`. It has the 2 groups the page prints beside the arm: `Codex (Renderer)`, the helper processes that draw ChatGPT's window (part of ChatGPT.app, in ChatGPT arms), and `WattMate.app`, WattMate itself (every arm; zero where it was not open). |
| `coalitions.csv` | tick and coalition | `t`, `coalition`, `energy_mj_since_previous_tick`: energy the kernel charged to each app's process coalition since the previous tick. It has rows for the apps under test, Terminal (where the lab's runner works) and WattMate where it was open; everything else is one `other` row. |
| `journal.csv` | arm | `arm`, `index`, `t_measure_start`, `t_measure_end`; the state of charge and power source at the start and end (`soc_start`, `soc_end`, `source_start`, `source_end`); the full charge capacity the battery reports (`fcc_mah` on macOS 27, `raw_max_mah` on 26 and 14) and `nominal_mah`; `cycles`, `brightness`, `lowpowermode`, `temperature_c` and the macOS `build`. |
| `app-stream.csv`, `browser-stream.csv` | prompt | `app` and `mode` (the window's state: `front`, `behind`, `hidden`, `minimized`), or `browser` and `service`; then `prompt` and `sent_at`, the time it was sent. |
| `app-answers.csv` | prompt sent (from 13 October; the first prompt of each arm is logged but not judged, and in the 2 answering arms of `20261014T094238Z-window-vs-answer` the last prompt has 2 readings) | `app`, `mode`, `prompt`, `bytes_received_since_send` (bytes ChatGPT and its helper processes received between sending this prompt and the next one, from single `nettop` snapshots), `read_at`. The value is negative in 26 rows. 3 of them are judged: prompt 7 of `20261014T030828Z-m3-minutes-m3-repeat`, prompt 4 of `20261014T094239Z-window-vs-answer` and prompt 3 of the second hidden run of `20261015T095459Z-wattmate-release`. The rule counts a negative value as no answer. |
| `app-versions.csv` | version reading (from 13 October) | `app`, `mode`, `version`, `build`, `at`: the app's version during the series. |
| `agent-stream.csv` | prompt to an agent in a terminal | `col1` the agent, `col2` the prompt's number, `col3` the time it was sent. |
| `agent-task.csv` | agent task | `agent` (`claude`, `codex` or `copilot`), `task` (its number in the run), `start`, `end`, `agent_exit_code`, `unittest_exit_code` (the test suite's exit code; `-` where none was recorded). |
| `cursor-agent.csv` | Cursor task | `mode` (the window's state), `task`, `sent_at`, `file_mtime` (the edited file's modification time), `exit_code`. |
| `ollama.csv` | Ollama answer | `model`, `answer` (its number in the run; `loaded` and `serve` mark when an idle run loaded the model or started the server without one), `start`, `end`, `eval_count_tokens` (tokens generated), `eval_rate_tokens_per_s` (generation speed). |
| `text-window.csv` | TextEdit arm event (14 October) | `event`, `app`, `x`, `y`, `width`, `height`, `font_size`, `at`. A `frame` row is the window frame the arm read (`app` says from which window) before it opened TextEdit in it; a `stop_front` row names the app in front at the end of the arm. |
| `meta.json` | series | `series`; `machine`; `macos_build`, the builds the journal saw; `battery`, the `cycles` and `full_charge_capacity_mah` values the journal saw; `used_by_page_sections`, the page sections that use the series, by the section names in `scripts/recompute.py`; `dropped_runs`, the arms that were thrown out, each with `arm` and `reason`, and with `messages` (such as `8-12`) when only those messages were dropped from the arms whose names start with `arm`; `note`, which points to the drops marked in `results/`. 3 sections read series less directly. The 2 reading sections, `window-vs-answer-reading` and `window-vs-textedit-reading`, use the series of `window-vs-answer` and `window-vs-textedit` and, for their reference, 4 battery-minutes series that the script names. The 2 Neural Engine rows of the `window` section read every series that has `chip.csv`, 53 of them, including the 2 of the Release check of 15 October. The WattMate comparison, `wattmate`, is named by the 2 series of the Release check; its M3 Max run is named in the script. |

A night folder, `data/nights/<night>/`, holds 2 files. `meta.json` names the `machine`, what was `left_open` (an
app, or `nothing (clean night)`), and the night's `start` and `end`. Only events between them count. `events.csv`
lists the Sleep, DarkWake and Wake events of the night from `pmset -g log` (`t_utc`, `kind`), with the length of
each sleep (`sleep_secs`) and whether a dark wake came over Wi-Fi (`wifibt`) or from the clock (`rtc`).

## Rules the numbers depend on

- Chip windows. A 5-second `powermetrics` reading counts in the interval that contains its midpoint
  (`t_end − seconds/2`). Watts are the summed energy over the summed `seconds`. "Above control" subtracts the mean of
  all control arms of the same series.
- Charge counter. Battery drain is the drop in `charge_counter_mah` between the last tick at or before each boundary
  of the measured window. The counter is the battery's own count, not a plain sum of current: when a heavy load
  stops, it climbs back for a while. In the 30-minute battery-minutes runs of 11 and 12 October it rose by up to
  55 mAh in one step on the M1 (during a run of builds) and by up to 36 mAh on the M1 Pro machines (just after one).
  After the 8B model's runs on the M1 on 13 October it rose by up to 153 mAh. `results/` therefore also gives the
  same ratio read one tick after each boundary, as a sensitivity check. Where the two differ a lot (on the M1, and in
  hidden-window runs), rely on the voltage times current column. Ticks on the cable inside a battery run are
  skipped (`cable_cut` in `scripts/recompute.py`).
- Whole Mac. `whole_mac_w` is the mean over the ticks inside the window.
- Process energy. `subject.csv` and `processes.csv` are summed from the first tick at or after the start of a window
  to the last tick at or before its end, over the time between those two ticks. `coalitions.csv` is read from the
  last tick at or before each boundary.
- Per-prompt audit. Whether a prompt got an answer is judged from the chip's mean power in the 140 seconds after it
  was sent, rounded to hundredths: 0.25 W or more is an answer, 0.15 to 0.25 is unsure, below 0.15 is none. A hidden
  window draws an answer too cheaply for this audit to prove it, so from 13 October the bytes ChatGPT received after
  each prompt are logged instead (`app-answers.csv`). The first prompt of each arm is not judged. On the M1 and the
  2 M1 Pro machines, 20,000 bytes or more is an answer and under 5,000 is none. On the M3 Max, 110,000 or more is
  an answer and 80,000 or less is none; with nothing asked, ChatGPT receives about 69,000 bytes there in 145 seconds.
- Agent tasks. Energy per task is the chip over the whole arm, from its start, minus control, divided by the number
  of tasks. Cursor is counted from 10 seconds before sending to 60 seconds after. The Claude Code tasks it is compared
  with ran back to back, so they are counted in one window from 10 seconds before the first to 60 seconds after the
  last; a window per task would count the same work up to 3 times.
- Days. The `day` column of `results/` is the UTC day a series started, or the span of UTC days its runs cover. The
  2 Neural Engine rows, which sum every series with chip readings, have no day. A night carries its span: a night
  in `data/nights/` from its `start` to its `end`. The Ollama night of 7 to 8 October ran as 2 series
  (`ai-night-1`, `ai-night-2`) on each of the 2 Macs it used, the M1 and the M1 Pro with macOS 14; its span runs
  from the first part's start to the last part's end, on the rows of both parts.
- Dropped arms. `meta.json` lists the arms that were thrown out, with the reason. Dropped prompts and runs inside kept
  arms are marked in the `note` column of `results/ai-battery-usage-mac.csv`: every ChatGPT run in Chrome, the Safari
  runs in which a prompt went unanswered, 2 Cursor tasks, and thread messages after a usage limit.

## Checked twice

Before publication the numbers were reproduced from these files in 2 ways: by `scripts/recompute.py`, a port of the
lab's own script, and by a replication written from the method on the page alone, without seeing the lab's code.

`scripts/check.py` pairs every row the port recomputes with its row in `results/`. A pair passes only when the value
is the same string as `results/` prints, the machine, power source, day and instrument match, and either both notes
are empty or both are not (the wording of a note may differ). The check also fails on a row of `results/` that the
port does not produce, on a recomputed row that `results/` does not have, and on a row of `results/` that the port
neither recomputes nor lists as not recomputed. The port passes on every row it recomputes, 2,594 of 2,676.
`check.py` counts the other 82 separately, with the reason (most of them are the WattMate comparison, below), and
fails if that count or any of those rows changes.

The independent replication was written on 13 October. At that point it matched every row but 5, which differed
in the last printed digit. It was not updated when the page grew after that: run against the current `results/`, it
stops at the battery-minutes series of 13 October, which it does not know. The replication found the charge
counter's boundary sensitivity, a comparison that used different windows for Cursor and Claude Code, and a few
figures rounded twice. The page was corrected before it went out.

## What is not here, on purpose

The raw collector logs record the full path and command line of every process on the Mac, so they are not
published. Per-process energy appears only for the app or agent an arm measures (`subject.csv`), with 2 exceptions,
both in `processes.csv` of the 4 window series of 14 October and the 2 series of the Release check of
15 October, because the page prints them beside the arm:

- ChatGPT's renderer (`Codex (Renderer)`), the helper processes that draw ChatGPT's window. They are part of
  ChatGPT.app and appear in the ChatGPT arms. In the Release check, a run with the window in front counts only when
  the renderer drew 0.1 W or more.
- WattMate itself (`WattMate.app`), in every arm of those series, controls included, because the page prints the
  app's own cost in the window series of 14 October. In the Release check it was open in every run as well. Where
  WattMate was not open (the M1's 2 series of 14 October), the group is there with zero energy.

Per-app coalition energy appears only for the apps under test (ChatGPT, Claude, Cursor, Ghostty, Google Chrome,
Safari, Terminal and TextEdit; Terminal is also where the lab's runner works) and for WattMate, in the 5 series
where it was open. Everything else is summed into `other`. No serial numbers, user names or paths leave the lab.

WattMate's own database is not published either, because it records every app that ran on the Mac. As a result, 82
of the 111 rows of `results/` that compare WattMate with the lab's instruments cannot be recomputed here. Most are
read over WattMate's own 90-second windows, which only its database holds. The rest need the collector's full
per-process log. The 28 rows of the M1's run of 13 October come from a series that is not in `data/`
(`20261013T170744Z-agents15-recon-battery`).

The 29 rows that need only `data/` are recomputed and checked like the rest. One is the screen alone of the M3 Max
run (the mean voltage times current of its series' control runs). The other 28 are rows of the Release check of
15 October: for each ChatGPT run, the answers by bytes received, ChatGPT's renderer and whether the run is valid by
the rules written before the data; and on each Mac, the whole Mac's difference between the window in front and
hidden, by voltage times current and by the charge counter. The remaining 28 rows of the Release check need
WattMate's database: its whole Mac against voltage times current in each run; in front minus hidden, its whole Mac,
its ChatGPT row, Screen and system and the other app rows; voltage times current over its own 90-second windows,
whose bounds only the database holds; and the shares built on them.

## Reproduce

```
/usr/bin/python3 scripts/recompute.py            # every section
/usr/bin/python3 scripts/recompute.py minutes    # one section (the names are in the script's first lines)
/usr/bin/python3 scripts/check.py                # compare with results/; exits 1 on any difference it checks (see "Checked twice")
```

Python 3.9 or later, standard library only.

## Conflict of interest

The author makes and sells WattMate, and part of this study checks WattMate's own numbers. That is why the data and
the code are published, and why the runs in which WattMate was open are named above. This study was not
preregistered. The checks of WattMate's numbers that followed it are, with rules, thresholds and scripts published
before the first counted run: https://doi.org/10.5281/zenodo.23697150.

## Limits

- One author and one lab. No one outside the study has repeated the measurements.
- The charge counter climbs back after a heavy load (see "Charge counter"), so short or light runs read by it are
  rough; where it and voltage times current disagree, rely on voltage times current.
- Voltage times current and the Mac's once a second power reading disagree on the rise above the screen alone. In
  the check of WattMate 1.6 (not in this version), with the ChatGPT window in front, voltage times current read the rise 7 and
  10% higher on the M1 Pro with macOS 27 and 35 and 3% higher on the MacBook Air M5. With the window hidden it read
  1.5 and 2 times higher on the M1 Pro, but about the same and 22% lower on the M5. The series in this archive did not
  log the once a second reading, so how far their figures are off, and in which direction, is not known.
- The M3 Max's settings were set by hand and not checked by the runner (see "The Macs").
- The 3 MacBook Air M5 series ran once each.
- 82 rows cannot be recomputed from these files (see "What is not here, on purpose").

## File checksums

`SHA256SUMS` lists the SHA-256 of every other file in the archive. Check it with `shasum -a 256 -c SHA256SUMS`
from the archive's folder.

## Licence and citation

The data (`data/`, `results/`) is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The code
(`scripts/` and `independent/`) is under the MIT licence (`LICENSE`). To cite, use `CITATION.cff` or: Staradubau, V.
(2026). *Does AI drain your Mac battery? Data and code* (Version 1.1) [Data set]. WattMate. Zenodo.
https://doi.org/10.5281/zenodo.23391844

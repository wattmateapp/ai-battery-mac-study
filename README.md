# Does AI drain your Mac battery? Data and code

This archive holds the data and the code behind the study
[Does AI drain your Mac battery?](https://wattmateapp.com/ai-battery-usage-mac): ChatGPT, Claude Desktop,
Claude Code, Codex CLI, Copilot CLI, Cursor and Ollama, measured on used Apple silicon MacBook Pros from 5 to
15 October 2026.

The study was made by Vitali Staradubau, who makes [WattMate](https://wattmateapp.com), a battery app for the Mac.
The measurements did not use the app: they come from `powermetrics`, the battery's own counters and a per-process
collector. WattMate was closed during the runs except in places the page names: the two window series of
14 October on the M1 Pro with macOS 27 (open in every run, controls included), one extra run on the M3 Max, the
Release check of 15 October on two of the test Macs, the M1 Pro with macOS 27 and the M1 (open in every run,
controls included), and the M1 series of the comparison of WattMate with the lab's instruments, which is not in
`data/`. Every number on the study page can be recomputed from the files here with the scripts in `scripts/`, except
82 of the 111 rows of that comparison (see "What is not here, on purpose").

## What is here

```
results/ai-battery-usage-mac.csv   every number on the page, one row each, with the series it came from (none on the
                                   battery-capacity rows, which give the machine and day, or on the two Neural Engine
                                   rows, which sum every series)
data/runs/<series>/                one folder per measurement series (one Mac, one session)
data/nights/<night>/               overnight sleep logs, reduced to events
scripts/recompute.py               recomputes from data/ every number that data/ allows
scripts/check.py                   compares the recomputation with results/, row by row
independent/replicate.py           a replication written from the page's method alone (see "Checked twice")
```

A series folder is named by its start time in UTC and its design, for example `20261011T141657Z-minutes-battery`;
the two series on the M3 Max carry `m3` in the name (`20261013T214438Z-m3-minutes-m3`), as `results/` names them.
Which Mac it ran on is in its `meta.json` (`"machine": "M1, macOS 26"`).

## The Macs

| Mac | Model | macOS | Display | Battery |
|---|---|---|---|---|
| M1 Pro, macOS 27 | MacBook Pro 14-inch (MacBookPro18,3) | 27.0.1 (26A434) | mini-LED, ProMotion | 75%, 1,719 to 1,723 cycles |
| M1, macOS 26 | MacBook Pro 13-inch (MacBookPro17,1) | 26.7.1 (25G241) | LCD, 60 Hz | 85%, 532 to 535 cycles |
| M1 Pro, macOS 14 | MacBook Pro 14-inch (MacBookPro18,3) | 14.2.1 (23C71) | mini-LED, ProMotion | 81%, 1,046 to 1,049 cycles |
| M3 Max, macOS 27 (the author's working Mac, ChatGPT window series of 13 and 14 October only) | MacBook Pro 14-inch (Mac15,10), 36 GB | 27.0 (26A428) | mini-LED, ProMotion | 86%, 617 cycles |

The three test Macs (the M1 and the two M1 Pro machines) have 16 GB of memory each and were used for nothing but
measuring: Spotlight, automatic updates and Bluetooth were off, brightness was pinned, and the display never slept
during a series. The M3 Max is not one of them. It is the author's working Mac, with 36 GB, and its background was
not checked.

## Files in a series folder

All times are Unix seconds (UTC). Energy is in millijoules (mJ), power in watts (W).

| File | One row per | Columns |
|---|---|---|
| `arms.csv` | arm (one condition) | `arm`, `index`, `arm_start`, `measure_start`, `measure_end`, `arm_stop`. A condition starts at `arm_start`; the measured window is `measure_start` to `measure_end`, after a warm-up. `control` is the same Mac with the screen on and nothing running. |
| `chip.csv` | 5-second `powermetrics` snapshot | `t_end`, `seconds`, `cpu_mj`, `gpu_mj`, `ane_mj`. "Chip" on the page is CPU plus GPU; a snapshot belongs to a window when its midpoint (`t_end − seconds/2`) lies inside it. There is no `chip.csv` in nine series: the three `k` calibration series, the four parts of the Ollama night and the M3 Max's two series. |
| `battery.csv` | collector tick, about every 10 s | `t` (the tick's time), `power_source` (`AC` or `Battery`), `charge_counter_mah` (AppleRawCurrentCapacity), `percent`, `voltage_mv`, `pack_current_ma`, `system_power_in_mw`, `whole_mac_w` = power in from the cable minus pack power, the "voltage times current" reading on the page. |
| `subject.csv` | tick inside an arm | `t`, `arm`, `index`, `subject`, `energy_mj_since_previous_tick`: energy of the processes of the app or agent the arm measures (ChatGPT.app during a ChatGPT arm, TextEdit.app during a TextEdit arm, the agent's process tree during an agent arm), since the previous tick. |
| `processes.csv` | tick inside an arm and process group | the four window series of 14 October and the two series of the Release check of 15 October only: `t`, `arm`, `index`, `process`, `energy_mj_since_previous_tick`. Two groups the page prints beside the arm: `Codex (Renderer)`, the helper processes that draw ChatGPT's window (part of ChatGPT.app, in ChatGPT arms), and `WattMate.app`, WattMate itself (every arm; zero where it was not open). |
| `coalitions.csv` | tick and coalition | `t`, `coalition`, `energy_mj_since_previous_tick`: energy charged by the kernel to each app's process coalition since the previous tick, for the apps under test, Terminal (where the lab's runner works) and WattMate where it was open; everything else is one `other` row. |
| `journal.csv` | arm | `arm`, `index`, `t_measure_start`, `t_measure_end`, the state of charge and power source at the start and end (`soc_start`, `soc_end`, `source_start`, `source_end`), the battery-reported full charge capacity (`fcc_mah` on macOS 27, `raw_max_mah` on 26 and 14) and `nominal_mah`, `cycles`, `brightness`, `lowpowermode`, `temperature_c`, and the macOS `build`. |
| `app-stream.csv`, `browser-stream.csv` | prompt | `app` and `mode` (the window's state: `front`, `behind`, `hidden`, `minimized`), or `browser` and `service`; then `prompt` and `sent_at`, when it was sent. |
| `app-answers.csv` | prompt sent (from 13 October; the first prompt of each arm is logged but not judged, and in the two answering arms of `20261014T094238Z-window-vs-answer` the last prompt has two readings) | `app`, `mode`, `prompt`, `bytes_received_since_send` (bytes ChatGPT and its helper processes received from sending this prompt to the next one, from single `nettop` snapshots), `read_at`. The value is negative in 26 rows, three of them judged (prompt 7 of `20261014T030828Z-m3-minutes-m3-repeat`, prompt 4 of `20261014T094239Z-window-vs-answer`, prompt 3 of the second hidden run of `20261015T095459Z-wattmate-release`); the rule counts a negative value as no answer. |
| `app-versions.csv` | version reading (from 13 October) | `app`, `mode`, `version`, `build`, `at`: the app's version during the series. |
| `agent-stream.csv` | prompt to an agent in a terminal | `col1` the agent, `col2` the prompt's number, `col3` when it was sent. |
| `agent-task.csv` | agent task | `agent` (`claude`, `codex` or `copilot`), `task` (its number in the run), `start`, `end`, `agent_exit_code`, `unittest_exit_code` (the test suite's exit code; `-` where none was recorded). |
| `cursor-agent.csv` | Cursor task | `mode` (the window's state), `task`, `sent_at`, `file_mtime` (the edited file's modification time), `exit_code`. |
| `ollama.csv` | Ollama answer | `model`, `answer` (its number in the run; `loaded` and `serve` instead mark when an idle run loaded the model or started the server without one), `start`, `end`, `eval_count_tokens` (tokens generated), `eval_rate_tokens_per_s` (generation speed). |
| `text-window.csv` | TextEdit arm event (14 October) | `event`, `app`, `x`, `y`, `width`, `height`, `font_size`, `at`. A `frame` row is the window frame the arm read (`app` says from which window) before it opened TextEdit in it; a `stop_front` row names the app in front at the end of the arm. |
| `meta.json` | series | `series`; `machine`; `macos_build`, the builds the journal saw; `battery`, the `cycles` and `full_charge_capacity_mah` values the journal saw; `used_by_page_sections`, the page sections that use the series, by the section names of `scripts/recompute.py` (the two reading sections, `window-vs-answer-reading` and `window-vs-textedit-reading`, use the series of `window-vs-answer` and `window-vs-textedit` and, for their reference, four battery-minutes series the script names; the `window` section's two Neural Engine rows read every series that has `chip.csv`, 53 of them, the two of the Release check of 15 October included; the WattMate comparison, `wattmate`, is named by the two series of the Release check, and its M3 Max run is named in the script); `dropped_runs`, the arms we threw out, each with `arm` and `reason`, and with `messages` (such as `8-12`) when only those messages of the arms whose name starts with `arm` were dropped; `note`, which points to the drops marked in `results/`. |

A night folder, `data/nights/<night>/`, holds two files. `meta.json` names the `machine`, what was `left_open` (an
app, or `nothing (clean night)`), and the night's `start` and `end`; only events between them count.
`events.csv` lists the Sleep, DarkWake and Wake events of the night from `pmset -g log` (`t_utc`, `kind`), with the
length of each sleep (`sleep_secs`) and whether a dark wake came over Wi-Fi (`wifibt`) or from the clock (`rtc`).

## Rules that the numbers depend on

- **Chip windows.** A 5-second `powermetrics` reading counts in the interval that contains its midpoint
  (`t_end − seconds/2`); watts are the summed energy over the summed `seconds`. "Above control" subtracts the mean
  of all control arms of the same series.
- **Charge counter.** Battery drain is the drop in `charge_counter_mah` between the last tick at or before each
  boundary of the measured window. The counter is the battery's own count, not a pure sum of current: when a heavy
  load drops it climbs back for a while. In the 30-minute battery-minutes runs of 11 and 12 October it rose by up to
  55 mAh in one step on the M1 (during a run of builds) and up to 36 mAh on the M1 Pro machines (just after one);
  after the 8B model's runs on the M1 on 13 October, by up to 153 mAh. `results/` therefore also carries the same
  ratio read one tick after each boundary, as a sensitivity check; where the two differ a lot (the M1, and
  hidden-window runs), lean on the voltage times current column.
- **Whole Mac.** `whole_mac_w` is the mean over the ticks inside the window.
- **Process energy.** `subject.csv` and `processes.csv` are summed from the first tick at or after the start of a
  window to the last tick at or before its end, over the time between those two ticks. `coalitions.csv` is read from
  the last tick at or before each boundary.
- **Per-prompt audit.** Whether a prompt got an answer is judged from the chip's mean power in the 140 seconds after
  it was sent, rounded to hundredths: 0.25 W or more is an answer, 0.15 to 0.25 unsure, below 0.15 none. A hidden
  window draws an answer too cheaply for this audit to prove it; from 13 October the bytes ChatGPT received after each
  prompt are logged instead (`app-answers.csv`). The first prompt of each arm is not judged; on the test Macs 20,000
  bytes or more is an answer and under 5,000 none; on the M3 Max 110,000 or more is an answer and 80,000 or less none
  (with nothing asked, ChatGPT receives about 69,000 bytes there in 145 seconds).
- **Agent tasks.** Energy per task is the chip over the whole arm, from its start, minus control, divided by the
  number of tasks. Cursor is counted from 10 seconds before to 60 seconds after sending; the Claude Code tasks it is
  compared with ran back to back, so they are counted in one window from 10 seconds before the first to 60 seconds
  after the last (a window per task would count the same work up to three times).
- **Days.** The `day` column of `results/` is the UTC day a series started, or the span of UTC days its runs cover.
  The two Neural Engine rows, which sum every series with chip readings, have no day.
  A night carries its span: a night in `data/nights/` from its `start` to its `end`, and the Ollama night of 7 to
  8 October, which the lab ran as two series on each of the two Macs it used, the M1 and the M1 Pro with macOS 14
  (`ai-night-1`, `ai-night-2`), from the first part's start
  to the last part's end, on the rows of both parts.
- **Dropped arms.** `meta.json` lists the arms we threw out with the reason; dropped prompts and runs inside kept
  arms (every ChatGPT run in Chrome, the Safari runs in which a prompt went unanswered, two Cursor tasks, thread messages after a usage limit) are marked in the `note`
  column of `results/ai-battery-usage-mac.csv`.

## Checked twice

Before publication the numbers were reproduced from these files in two ways: by `scripts/recompute.py`, a port of
the lab's own script, and by a replication written from the method on the page alone, without seeing the lab's code.

`scripts/check.py` pairs every row the port recomputes with its row in `results/`. A pair passes only when the value
is the same string as `results/` prints it, the machine, power source, day and instrument are the same, and both
notes are empty or both are not (the wording of a note may differ). A row of `results/` the port does not produce,
or a recomputed row that `results/` does not have, fails the check as well; so does a row of `results/` that the
port neither recomputes nor lists as not recomputed. The port passes on every row it recomputes, 2,396 of 2,478;
`check.py` counts the other 82 apart, with the reason (most of the WattMate comparison, below), and fails if that
count, or any of those rows, changes.

The independent replication was written on 13 October, when it matched every row but five, which differed in the
last printed digit. It was not updated when the page grew after that: run as it is against the current `results/`,
it stops at the battery-minutes series of 13 October, which it does not know. The replication is what found the
charge counter's boundary sensitivity, a comparison that used different windows for Cursor and Claude Code, and a
few figures rounded twice; the page was corrected before it went out.

## What is not here, on purpose

The raw collector logs record the full path and command line of every process on the Mac. They are not published.
Per-process energy appears only for the app or agent an arm measures (`subject.csv`), with two named exceptions,
both in `processes.csv` of the four window series of 14 October and of the two series of the Release check of
15 October, because the page prints them beside the arm:

- **ChatGPT's renderer** (`Codex (Renderer)`), the helper processes that draw ChatGPT's window. They are a part of
  ChatGPT.app; they appear in the ChatGPT arms. In the Release check a run with the window in front counts only
  when the renderer drew 0.1 W or more.
- **WattMate itself** (`WattMate.app`), in every arm of those series, controls included, because the page prints
  the app's own cost in the window series of 14 October; in the Release check it was open in every run as well.
  Where WattMate was not open (the M1's two series of 14 October) the group is there with zero energy.

Per-app coalition energy appears only for the apps under test (ChatGPT, Claude, Cursor, Ghostty, Google Chrome,
Safari, Terminal, TextEdit; Terminal is also where the lab's runner works) and for WattMate, in the five series
where it was open; everything else is summed into `other`. No serial numbers, user names or paths leave the lab.

WattMate's own database is not published either: it records every app that ran on the Mac. Of the 111 rows of
`results/` that compare WattMate with the lab's instruments, 82 cannot be recomputed here. Most are read over
WattMate's own 90-second windows, which only its database holds; the rest need the collector's full per-process log;
and the 28 rows of the M1's run of 13 October come from a series that is not in `data/`
(`20261013T170744Z-agents15-recon-battery`). The 29 rows that need only `data/` are recomputed and checked like the
rest: the screen alone of the M3 Max run (the mean voltage times current of its series' control runs), and 28 rows of
the Release check of 15 October. Those are, for each ChatGPT run, the answers by bytes received, ChatGPT's renderer
and whether the run is valid by the rules written before the data, and, on each Mac, the whole Mac's difference
between the window in front and hidden, by voltage times current and by the charge counter. The other 28 rows of
the Release check need WattMate's database: its whole Mac against voltage times current in each run; in front minus
hidden, its whole Mac, its ChatGPT row, Screen and system and the other app rows; voltage times current over its own
90-second windows, whose bounds only the database holds; and the shares built on them.

## Reproduce

```
/usr/bin/python3 scripts/recompute.py            # every section
/usr/bin/python3 scripts/recompute.py minutes    # one section (the names are in the script's first lines)
/usr/bin/python3 scripts/check.py                # compare with results/; exits 1 on any difference it checks (see "Checked twice")
```

Python 3.9 or later, standard library only.

## Licence and citation

The data (`data/`, `results/`) is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); the code
(`scripts/` and `independent/`) under the MIT licence (`LICENSE`). To cite, use `CITATION.cff` or: Staradubau, V.
(2026). *Does AI drain your Mac battery? Data and code* (Version 1.0) [Data set]. WattMate. Zenodo.
https://doi.org/10.5281/zenodo.23391845

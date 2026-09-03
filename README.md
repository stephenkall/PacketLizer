# PacketLizer

A low-profile connection-stability monitor built to **gather evidence of packet
loss** to present to your ISP. It lives in the system tray (next to the clock),
continuously probes a configurable target, stores everything in a compact SQLite
database and produces **on-demand reports in HTML + PDF + CSV**.

## Why it exists

`ping -t target > log.txt` works, but the result is a wall of text. PacketLizer
does the same thing automatically and turns the data into a dashboard with loss
%, outage frequency / time of day, average duration, MTBF, a latency-vs-time
chart (timeouts drawn on the configured timeout line) and a verbose CSV with
every single packet.

## Download

Every push to `main` that passes the tests publishes a **GitHub Release** with a
ready-to-run `PacketLizer-vX.Y.Z.N.exe` attached — grab the latest from the
[Releases page](https://github.com/stephenkall/PacketLizer/releases). No Python
required to run it.

## Install from source

Needs Python 3.11+ on Windows. You don't have to install dependencies by hand —
the program installs them from `requirements.txt` on first launch.

```powershell
git clone https://github.com/stephenkall/PacketLizer.git
cd PacketLizer
pythonw main.py            # starts in the background, tray icon only
```

### Build the executable yourself

```powershell
python build_exe.py        # produces dist\PacketLizer.exe (single file, no console)
dist\PacketLizer.exe
```

`build_exe.py` bundles only what PacketLizer needs (pystray, Pillow, icmplib,
matplotlib + numpy, reportlab, plus the stdlib) and explicitly excludes heavy
unrelated packages that might be installed in your dev environment, so the `.exe`
stays around ~45 MB.

## Usage

| Command | What it does |
|---|---|
| `pythonw main.py` | Monitor + tray icon + window (normal mode) |
| `python main.py --monitor` | Monitor only, foreground, with logs (Ctrl+C stops and saves) |
| `python main.py --monitor --duration 3600` | Headless monitor with a run deadline (stops after 1 h) |
| `python main.py --monitor --target 1.1.1.1 --interval 2` | Override target/interval for this run only |
| `python main.py --language pt_BR ...` | Override the UI/report language for this run (`auto`, `en`, `pt_BR`) |
| `python main.py --report --format both` | Generate an HTML + PDF report (+ CSV) in the current folder |
| `python main.py --report --days 7` | Report for the last 7 days only |
| `python main.py --report --since 2026-09-01 --until 2026-09-03` | Report for a date range |
| `python main.py --export-csv --out data.csv` | Export every sample to CSV |
| `python main.py --install-autostart` | Enable start-with-Windows (HKCU registry, no admin) |
| `python main.py --install-autostart --startup-folder` | Same, via the Startup folder |
| `python main.py --uninstall-autostart` | Disable start-with-Windows |
| `python main.py --config` | Print the effective configuration and paths |

### Main window

The icon sits in the tray and the window **does not show up in the taskbar**
(it's a *tool window*). On first run the window opens so you can set the target;
after that it **starts hidden** — click the tray icon to open it. The window
shows:

* the **current state** (Running / Unstable / OUTAGE in progress / Paused), with
  a colored indicator;
* an **editable configuration** panel: a text field for the **target (domain or
  IP)**, ping interval, per-ping timeout, number of consecutive losses that
  counts as an outage, history retention in days, a **language** selector, and a
  **"Start automatically with Windows"** checkbox. **Save & apply** writes
  `config.json`; if the target/interval/timeout changed, the monitor restarts
  automatically. The language switch takes effect immediately; the autostart
  checkbox takes effect on click;
* target, probe method, last sample, loss %, outage count and how long it has
  been monitoring;
* **Pause / Resume** the monitoring (standby);
* **Quit** (with confirmation);
* **Generate report** with optional **start date** and **end date**: no start
  date means since the beginning of the data, no end date means up to the most
  recent sample. The HTML opens automatically when done;
* **Data & logs**: **Clear all logs** wipes every recorded sample (with a
  confirmation), and **Delete specific logs...** opens a dialog to remove
  samples by a date range and/or a selection of targets — target(s) only
  deletes all dates for them, dates only deletes all targets in the range, both
  narrows to the intersection. The monitor is stopped for the deletion and
  restarts fresh afterwards (session counters reset).

Closing the window with `X` just hides it back to the tray; monitoring
continues. With no GUI environment (`tkinter` missing), the program falls back
to a simple menu on the tray icon itself.

> **`--monitor` mode is headless on purpose**: no window and no tray icon, it
> only writes logs to the console — including a `[status]` line every 15 s so you
> can see it's alive. Use normal mode (no arguments) for the window and tray.

## Probe method

At startup the program decides on its own:

* **Raw ICMP** (via `icmplib`) when the process has administrator privileges —
  more precise timestamps;
* the **operating system's `ping`** (output parsing, locale-independent) when it
  does **not** have privileges. Nothing is required from the user.

If raw ICMP loses permission at runtime, the monitor switches to `ping`
automatically. The child `ping` process is launched hidden (CREATE_NO_WINDOW), so
the windowed build never flashes a console window.

## Localization

The whole UI and the reports are localized through `packetlizer/i18n.py`.
Built-in languages: **English**, **Portuguese (Brazil)**, **Spanish** and
**Mandarin (Simplified Chinese)**; the default is `auto` (detected from the
operating system). Pick one from the **Language** selector in the window, set
`"language"` in `config.json` (`"auto"`, `"en"`, `"pt_BR"`, `"es"`, `"zh"`), or
pass `--language`. Adding a language is just another flat `dict[str, str]` in
`i18n.py` (missing keys fall back to English) — there is no per-language build
step.

## Where the data lives

`%LOCALAPPDATA%\PacketLizer\` (outside the repository):

```
config.json              parameters (target, interval, timeout, retention, ...)
packetlizer.db           SQLite: samples(ts, rtt_ms, status, target) + meta
packetlizer.log          application log
reports\                 reports generated from the tray/window
```

`config.json`:

```json
{
  "target": "www.vivo.com.br",
  "interval_seconds": 1.0,
  "timeout_ms": 2000,
  "outage_min_consecutive": 3,
  "retention_days": 60,
  "db_path": "",
  "prefer_raw_icmp": true,
  "language": "auto"
}
```

Changes made in the window are written to this file (on **Save & apply** and
also on **Quit**) and reloaded on the next launch — nothing is lost between
runs.

An **outage** is a run of `outage_min_consecutive` or more consecutive losses.
`timeout_ms` is both the per-ping timeout **and** the latency value used on the
chart to mark a lost packet. `retention_days` deletes older history at startup
and compacts the database (VACUUM); **`0` = unlimited retention** (nothing is
deleted).

## The report

Each sample records the **target** it was probed against. If you changed the
target over time, the report contains **a separate block per target** (dashboard
+ chart + tables for each), using only that target's samples — old data is not
relabeled with the new target. Databases created by earlier versions have their
history assigned automatically to the last target in use.

Each block has:

1. **Dashboard**: loss %, availability, outage count, outages/day, total
   downtime, mean/median/max outage duration, mean interval between outages,
   MTBF, most critical hour and weekday, latency p50/p95 and jitter.
2. **Chart** latency vs time, with lost packets plotted on the timeout line
   (`timeout_ms`, e.g. 2000 ms) and outage windows shaded; below it, loss % per
   calendar hour.
3. **Tables**: every outage, daily summary, status breakdown.

The **verbose CSV** (`timestamp_iso, timestamp_epoch, target, rtt_ms,
status_code, status`, one row per packet) is always generated alongside the
HTML/PDF and covers all targets.

## Development

```powershell
pip install -r requirements.txt pytest
pytest -q
```

### CI/CD (GitHub Actions)

`.github/workflows/ci.yml`:

* **`test`** — runs `pytest` on Python 3.11 and 3.12 (Ubuntu) for every push and
  pull request.
* **`release`** — on every push to `main` that passes `test`, builds
  `PacketLizer.exe` on Windows, smoke-tests it (`--config`), and publishes a
  **GitHub Release** tagged `v<__version__>.<run number>` with the versioned
  `.exe` attached and marked as *latest*.

Bump `packetlizer.__version__` in `packetlizer/__init__.py` when you want the
`X.Y.Z` part of the release tag to change.

"""PacketLizer - connection-stability monitor (ICMP) with tray, reports and CSV.

Quick usage:
    pythonw main.py                     # start monitor + tray icon + window (normal mode)
    python  main.py --monitor           # run the monitor only, foreground, with logs
    python  main.py --report --format both
    python  main.py --export-csv --out C:\\tmp\\packetlizer.csv
    python  main.py --install-autostart # register start-with-Windows (no admin)
    python  main.py --config            # print the effective configuration
"""
import subprocess
import sys
from pathlib import Path


def _ensure_deps():
    req = Path(__file__).parent / "requirements.txt"
    if not req.exists():
        return
    try:
        import pkg_resources  # type: ignore

        pkg_resources.require(
            [ln for ln in req.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
        )
        return
    except ImportError:
        try:
            import pystray  # noqa: F401
            import PIL  # noqa: F401
            import icmplib  # noqa: F401
            import matplotlib  # noqa: F401
            import reportlab  # noqa: F401
            import tqdm  # noqa: F401

            return
        except Exception:
            pass
    except Exception:
        pass
    print("Installing dependencies from requirements.txt ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req), "-q"])
    print("Done.")


if __name__ == "__main__" and not getattr(sys, "frozen", False):
    _ensure_deps()

import argparse  # noqa: E402

from packetlizer.config import Config, load_config  # noqa: E402
from packetlizer.i18n import available_languages, set_language  # noqa: E402


def _cmd_report(cfg: Config, args) -> int:
    from packetlizer.report import generate_reports

    out_dir = Path(args.out) if args.out else Path.cwd()
    paths = generate_reports(
        cfg,
        out_dir=out_dir,
        fmt=args.format,
        days=args.days,
        since=args.since,
        until=args.until,
    )
    for p in paths:
        print(f"Generated: {p}")
    return 0


def _cmd_export_csv(cfg: Config, args) -> int:
    from packetlizer.report import export_csv

    out = Path(args.out) if args.out else Path.cwd() / "packetlizer_export.csv"
    n = export_csv(cfg, out, days=args.days, since=args.since, until=args.until)
    print(f"Exported {n} samples to {out}")
    return 0


def _cmd_config(cfg: Config, _args) -> int:
    print(cfg.describe())
    return 0


def _cmd_autostart(cfg: Config, args, enable: bool) -> int:
    from packetlizer.autostart import set_autostart

    ok, msg = set_autostart(enable, mode=args.mode, use_startup_folder=args.startup_folder)
    print(msg)
    return 0 if ok else 1


def _cmd_monitor(cfg: Config, args) -> int:
    from packetlizer.monitor import run_monitor_foreground

    return run_monitor_foreground(cfg, duration=args.duration)


def _cmd_tray(cfg: Config, _args) -> int:
    from packetlizer.app import run_tray_app

    return run_tray_app(cfg)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="packetlizer", description="Connection-stability monitor.")
    p.add_argument("--config-file", help="Path to an alternative config.json.")

    g = p.add_argument_group("modes (no flag = tray + window)")
    g.add_argument("--monitor", action="store_true", help="Run the monitor only, foreground.")
    g.add_argument("--report", action="store_true", help="Generate a report on demand and exit.")
    g.add_argument("--export-csv", action="store_true", help="Export samples to CSV and exit.")
    g.add_argument("--config", action="store_true", help="Print the effective configuration and exit.")
    g.add_argument("--install-autostart", action="store_true", help="Enable start-with-Windows.")
    g.add_argument("--uninstall-autostart", action="store_true", help="Disable start-with-Windows.")

    p.add_argument("--format", choices=["html", "pdf", "both"], default="both", help="Report format.")
    p.add_argument("--out", help="Output file/folder.")
    p.add_argument("--days", type=int, default=None, help="Consider only the last N days.")
    p.add_argument("--since", help="Start date/time (ISO 8601).")
    p.add_argument("--until", help="End date/time (ISO 8601).")
    p.add_argument("--mode", choices=["auto", "script", "exe"], default="auto", help="Autostart mode.")
    p.add_argument("--startup-folder", action="store_true",
                   help="Use the Startup folder instead of the registry.")

    o = p.add_argument_group("override the configuration for this run")
    o.add_argument("--target", help="Domain or IP to probe (e.g. www.vivo.com.br).")
    o.add_argument("--interval", type=float, help="Seconds between pings.")
    o.add_argument("--timeout", type=int, help="Per-ping timeout in ms.")
    o.add_argument("--language", choices=available_languages(), help="UI/report language.")
    o.add_argument("--duration", type=int, default=None,
                   help="With --monitor: stop automatically after N seconds (run deadline).")
    return p


def _apply_overrides(cfg: Config, args) -> None:
    if args.target:
        cfg.target = args.target.strip()
    if args.interval:
        cfg.interval_seconds = max(0.2, args.interval)
    if args.timeout:
        cfg.timeout_ms = max(200, args.timeout)
    if args.language:
        cfg.language = args.language


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config_file)
    _apply_overrides(cfg, args)
    set_language(cfg.language)

    if args.config:
        return _cmd_config(cfg, args)
    if args.install_autostart:
        return _cmd_autostart(cfg, args, True)
    if args.uninstall_autostart:
        return _cmd_autostart(cfg, args, False)
    if args.report:
        return _cmd_report(cfg, args)
    if args.export_csv:
        return _cmd_export_csv(cfg, args)
    if args.monitor:
        return _cmd_monitor(cfg, args)
    return _cmd_tray(cfg, args)


if __name__ == "__main__":
    sys.exit(main())

"""PacketLizer - monitor de estabilidade de conexao (ICMP) com bandeja, relatorios e CSV.

Uso rapido:
    pythonw main.py                     # inicia monitor + icone na bandeja (modo normal)
    python  main.py --monitor           # roda so o monitor em primeiro plano (com logs)
    python  main.py --report --format both
    python  main.py --export-csv --out C:\\tmp\\packetlizer.csv
    python  main.py --install-autostart # registra inicio automatico (sem admin)
    python  main.py --config            # mostra a configuracao efetiva
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
    print("Instalando dependencias de requirements.txt ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req), "-q"])
    print("Concluido.")


if __name__ == "__main__" and not getattr(sys, "frozen", False):
    _ensure_deps()

import argparse  # noqa: E402

from packetlizer.config import Config, load_config  # noqa: E402


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
        print(f"Gerado: {p}")
    return 0


def _cmd_export_csv(cfg: Config, args) -> int:
    from packetlizer.report import export_csv

    out = Path(args.out) if args.out else Path.cwd() / "packetlizer_export.csv"
    n = export_csv(cfg, out, days=args.days, since=args.since, until=args.until)
    print(f"Exportadas {n} amostras para {out}")
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
    p = argparse.ArgumentParser(prog="packetlizer", description="Monitor de estabilidade de conexao.")
    p.add_argument("--config-file", help="Caminho para um config.json alternativo.")
    sub = p.add_subparsers(dest="_sub")

    g = p.add_argument_group("modos (sem subcomando = bandeja)")
    g.add_argument("--monitor", action="store_true", help="Roda so o monitor em primeiro plano.")
    g.add_argument("--report", action="store_true", help="Gera relatorio sob demanda e sai.")
    g.add_argument("--export-csv", action="store_true", help="Exporta amostras para CSV e sai.")
    g.add_argument("--config", action="store_true", help="Mostra a configuracao efetiva e sai.")
    g.add_argument("--install-autostart", action="store_true", help="Ativa inicio com o Windows.")
    g.add_argument("--uninstall-autostart", action="store_true", help="Desativa inicio com o Windows.")

    p.add_argument("--format", choices=["html", "pdf", "both"], default="both", help="Formato do relatorio.")
    p.add_argument("--out", help="Arquivo/pasta de saida.")
    p.add_argument("--days", type=int, default=None, help="Considerar apenas os ultimos N dias.")
    p.add_argument("--since", help="Data/hora inicial (ISO 8601).")
    p.add_argument("--until", help="Data/hora final (ISO 8601).")
    p.add_argument("--mode", choices=["auto", "script", "exe"], default="auto", help="Modo de autostart.")
    p.add_argument("--startup-folder", action="store_true", help="Usa a pasta Inicializar em vez do registro.")

    o = p.add_argument_group("sobrescreve a configuracao para esta execucao")
    o.add_argument("--target", help="Dominio ou IP a ser sondado (ex.: www.vivo.com.br).")
    o.add_argument("--interval", type=float, help="Segundos entre cada ping.")
    o.add_argument("--timeout", type=int, help="Timeout de cada ping em ms.")
    o.add_argument("--duration", type=int, default=None,
                   help="Com --monitor: encerra automaticamente apos N segundos (prazo de execucao).")
    return p


def _apply_overrides(cfg: Config, args) -> None:
    if args.target:
        cfg.target = args.target.strip()
    if args.interval:
        cfg.interval_seconds = max(0.2, args.interval)
    if args.timeout:
        cfg.timeout_ms = max(200, args.timeout)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config_file)
    _apply_overrides(cfg, args)

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

"""Minimal in-process localization: no gettext / .mo tooling.

Each language is a flat ``dict[str, str]``. Lookups fall back to English and
then to the key itself, so a missing translation never crashes the UI. Strings
may carry ``{named}`` placeholders consumed by :func:`t` via ``str.format``.

Default language is ``"auto"`` -> detected from the operating system.
"""
from __future__ import annotations

import locale
import logging
import os
import sys

log = logging.getLogger("packetlizer.i18n")

_EN: dict[str, str] = {
    # live state
    "state.starting": "Starting...",
    "state.running": "Running",
    "state.unstable": "Unstable (recent losses)",
    "state.outage": "OUTAGE in progress",
    "state.paused": "Paused (standby)",
    # window sections
    "win.section.config": "Configuration",
    "win.section.status": "Status",
    "win.section.report": "Generate report (HTML + PDF + CSV)",
    # config fields
    "win.field.target": "Target (domain or IP)",
    "win.field.interval": "Interval between pings (s)",
    "win.field.timeout": "Timeout per ping (ms)",
    "win.field.outage_min": "Consecutive losses to count as an outage",
    "win.field.retention": "History retention (days, 0 = unlimited)",
    "win.field.language": "Language",
    "win.field.start_date": "Start date (optional)",
    "win.field.end_date": "End date (optional)",
    "win.hint.date_format": "Format: YYYY-MM-DD (e.g. 2026-09-01). Empty = everything.",
    # buttons / checkbox
    "win.btn.save_apply": "Save & apply",
    "win.btn.generate_report": "Generate report",
    "win.btn.pause": "Pause",
    "win.btn.resume": "Resume",
    "win.btn.open_data_folder": "Open data folder",
    "win.btn.quit": "Quit",
    "win.chk.autostart": "Start automatically with Windows",
    # status values
    "win.status.target": "Target",
    "win.status.method": "Method",
    "win.status.last_sample": "Last sample",
    "win.status.loss": "Packet loss",
    "win.status.outages": "Outages detected",
    "win.status.monitoring_for": "Monitoring for",
    "win.value.no_response": "no response",
    "win.value.method_fmt": "{name}  ({reason})",
    "win.value.last_fmt": "{time}  -  {status} ({rtt})",
    "win.value.loss_fmt": "{pct:.2f}%   ({lost} of {total})",
    # report panel feedback
    "win.report.generating": "Generating report...",
    "win.report.done": "OK - {n} files generated in:\n{path}",
    "win.report.error": "Error: {err}",
    "win.cfg.saved_restarted": "Saved to {file}. Monitor restarted with the new target; this session's stats reset.",
    "win.cfg.saved_applied": "Saved to {file}. Settings applied without restarting the monitor.",
    # dialogs
    "dlg.invalid_title": "Invalid configuration",
    "dlg.invalid_target_empty": "Enter a domain or IP in the 'Target' field.",
    "dlg.invalid_target_space": "The target cannot contain spaces.",
    "dlg.invalid_numbers": "Interval, timeout, losses and retention must be numbers.",
    "dlg.invalid_interval_min": "The minimum interval between pings is 0.2 s.",
    "dlg.invalid_timeout_min": "The minimum timeout is 200 ms.",
    "dlg.invalid_outage_min": "'Consecutive losses to count as an outage' must be >= 1.",
    "dlg.invalid_retention_neg": "Invalid retention. Use 0 for unlimited retention.",
    "dlg.save_error_title": "Save error",
    "dlg.quit_title": "Quit PacketLizer",
    "dlg.quit_confirm": "Stop monitoring and close the program?",
    # tray menu
    "menu.open_window": "Open window",
    "menu.pause_resume": "Pause / Resume",
    "menu.generate_report_all": "Generate report (all)",
    "menu.quit": "Quit",
    "menu.state_fmt": "State: {state}",
    "menu.loss_fmt": "{target} | loss {pct:.2f}%",
    # notifications
    "notify.running_tray": "PacketLizer is running in the tray. Click the icon to open the window.",
    "notify.hidden": "PacketLizer keeps running in the tray. Click the icon to reopen.",
    "notify.paused": "Monitoring paused (standby).",
    "notify.resumed": "Monitoring resumed.",
    "notify.report_done": "Report generated: {names}",
    "notify.autostart_on": "PacketLizer will start automatically with Windows.",
    "notify.autostart_off": "Automatic start with Windows is now disabled.",
    "notify.autostart_fail": "Could not change the automatic-start setting: {err}",
    # probe method descriptions
    "probe.raw_privileged": "raw ICMP (privileged process)",
    "probe.ping_no_admin": "OS ping (no admin privileges)",
    "probe.ping_raw_unavailable": "OS ping (raw ICMP unavailable)",
    # language names
    "lang.auto": "System default",
    "lang.en": "English",
    "lang.pt_BR": "Portugues (Brasil)",
    # report document
    "rpt.doc_title": "PacketLizer - Stability report",
    "rpt.header": "PacketLizer — Stability report",
    "rpt.window_fmt": "Window: {start} → {end}",
    "rpt.total_fmt": "{n} samples total",
    "rpt.generated_fmt": "generated at {ts}",
    "rpt.multi_intro_fmt": "This report covers {n} distinct targets. Each block below uses only the samples collected against that target:",
    "rpt.multi_note": "Each block below uses only the samples collected against that target.",
    "rpt.block_target_fmt": "Target: {target}",
    "rpt.block_window_fmt": "Window for this target: {start} → {end}  |  {n} samples collected against this target  |  interval {interval:.0f}s",
    "rpt.h_latency": "Latency over time",
    "rpt.h_outages_fmt": "Outages detected ({n})",
    "rpt.h_daily": "Daily summary",
    "rpt.h_status": "Status breakdown",
    "rpt.no_outages": "No outages recorded.",
    "rpt.no_data": "No data.",
    "rpt.no_samples": "No samples in the requested range - let the monitor run before generating a report.",
    "rpt.unknown_target": "(unknown target)",
    "rpt.footer_fmt": (
        "Generated by PacketLizer. Each block uses only the samples collected against "
        "that target. Lost packets are plotted on the timeout line at {ms:.0f} ms (the "
        "configured per-ping timeout). An outage is a run of consecutive losses at or "
        "above the configured threshold (outage_min_consecutive)."
    ),
    # table columns
    "col.num": "#",
    "col.start": "Start",
    "col.duration": "Duration",
    "col.lost_packets": "Lost packets",
    "col.kinds": "Kinds",
    "col.date": "Date",
    "col.samples": "Samples",
    "col.lost": "Lost",
    "col.loss_pct": "Loss %",
    "col.outages": "Outages",
    "col.avg_latency": "Avg latency",
    "col.status": "Status",
    "col.count": "Count",
    "col.pct": "%",
    # KPI cards: title + sub-line
    "kpi.loss": "Packet loss",
    "kpi.loss_sub_fmt": "{lost} of {total} packets",
    "kpi.availability": "Availability",
    "kpi.availability_sub": "analyzed window",
    "kpi.outages": "Outages",
    "kpi.outages_sub_fmt": "{per_day:.1f} per day",
    "kpi.downtime": "Total downtime",
    "kpi.downtime_sub": "sum of outages",
    "kpi.avg_outage": "Mean outage duration",
    "kpi.avg_outage_sub_fmt": "median {median} / max {max}",
    "kpi.interval_between": "Mean interval between outages",
    "kpi.interval_between_sub_fmt": "MTBF {mtbf}",
    "kpi.peak_hour": "Most critical hour",
    "kpi.peak_hour_sub_fmt": "{n} outages in that hour",
    "kpi.no_outages": "no outages",
    "kpi.peak_weekday": "Most critical weekday",
    "kpi.peak_weekday_sub_fmt": "{n} outages",
    "kpi.latency_pct": "Latency p50 / p95",
    "kpi.latency_pct_sub_fmt": "avg {avg:.0f} ms - jitter {jitter:.0f} ms",
    # chart
    "chart.title_fmt": "Latency vs time - target {target}  (timeout = {ms:.0f} ms)",
    "chart.latency_ms": "Latency (ms)",
    "chart.lost_packet": "Lost packet",
    "chart.timeout_fmt": "Timeout ({ms:.0f} ms)",
    "chart.loss_per_hour": "Loss %/h",
    # weekdays (0 = Monday)
    "weekday.0": "Monday",
    "weekday.1": "Tuesday",
    "weekday.2": "Wednesday",
    "weekday.3": "Thursday",
    "weekday.4": "Friday",
    "weekday.5": "Saturday",
    "weekday.6": "Sunday",
}

_PT_BR: dict[str, str] = {
    "state.starting": "Iniciando...",
    "state.running": "Em execucao",
    "state.unstable": "Instavel (perdas recentes)",
    "state.outage": "QUEDA em andamento",
    "state.paused": "Em pausa (standby)",
    "win.section.config": "Configuracao",
    "win.section.status": "Status",
    "win.section.report": "Gerar relatorio (HTML + PDF + CSV)",
    "win.field.target": "Alvo (dominio ou IP)",
    "win.field.interval": "Intervalo entre pings (s)",
    "win.field.timeout": "Timeout por ping (ms)",
    "win.field.outage_min": "Perdas seguidas para contar uma queda",
    "win.field.retention": "Retencao do historico (dias, 0 = ilimitada)",
    "win.field.language": "Idioma",
    "win.field.start_date": "Data inicial (opcional)",
    "win.field.end_date": "Data final (opcional)",
    "win.hint.date_format": "Formato: AAAA-MM-DD (ex.: 2026-09-01). Vazio = tudo.",
    "win.btn.save_apply": "Salvar e aplicar",
    "win.btn.generate_report": "Gerar relatorio",
    "win.btn.pause": "Pausar",
    "win.btn.resume": "Retomar",
    "win.btn.open_data_folder": "Abrir pasta de dados",
    "win.btn.quit": "Encerrar programa",
    "win.chk.autostart": "Iniciar automaticamente com o Windows",
    "win.status.target": "Alvo",
    "win.status.method": "Metodo",
    "win.status.last_sample": "Ultima amostra",
    "win.status.loss": "Perda de pacotes",
    "win.status.outages": "Quedas detectadas",
    "win.status.monitoring_for": "Monitorando ha",
    "win.value.no_response": "sem resposta",
    "win.value.method_fmt": "{name}  ({reason})",
    "win.value.last_fmt": "{time}  -  {status} ({rtt})",
    "win.value.loss_fmt": "{pct:.2f}%   ({lost} de {total})",
    "win.report.generating": "Gerando relatorio...",
    "win.report.done": "OK - {n} arquivos gerados em:\n{path}",
    "win.report.error": "Erro: {err}",
    "win.cfg.saved_restarted": "Salvo em {file}. Monitor reiniciado com o novo alvo; as estatisticas desta sessao recomecam.",
    "win.cfg.saved_applied": "Salvo em {file}. Ajustes aplicados sem reiniciar o monitor.",
    "dlg.invalid_title": "Configuracao invalida",
    "dlg.invalid_target_empty": "Informe um dominio ou IP no campo 'Alvo'.",
    "dlg.invalid_target_space": "O alvo nao pode conter espacos.",
    "dlg.invalid_numbers": "Intervalo, timeout, perdas e retencao devem ser numeros.",
    "dlg.invalid_interval_min": "O intervalo minimo entre pings e 0,2 s.",
    "dlg.invalid_timeout_min": "O timeout minimo e 200 ms.",
    "dlg.invalid_outage_min": "'Perdas seguidas para contar uma queda' precisa ser >= 1.",
    "dlg.invalid_retention_neg": "Retencao invalida. Use 0 para retencao ilimitada.",
    "dlg.save_error_title": "Erro ao salvar",
    "dlg.quit_title": "Encerrar PacketLizer",
    "dlg.quit_confirm": "Encerrar o monitoramento e fechar o programa?",
    "menu.open_window": "Abrir janela",
    "menu.pause_resume": "Pausar / Retomar",
    "menu.generate_report_all": "Gerar relatorio (tudo)",
    "menu.quit": "Encerrar programa",
    "menu.state_fmt": "Estado: {state}",
    "menu.loss_fmt": "{target} | perda {pct:.2f}%",
    "notify.running_tray": "PacketLizer rodando na bandeja. Clique no icone para abrir a janela.",
    "notify.hidden": "PacketLizer continua na bandeja. Clique no icone para reabrir.",
    "notify.paused": "Monitoramento pausado (standby).",
    "notify.resumed": "Monitoramento retomado.",
    "notify.report_done": "Relatorio gerado: {names}",
    "notify.autostart_on": "PacketLizer vai iniciar automaticamente com o Windows.",
    "notify.autostart_off": "Inicio automatico com o Windows desativado.",
    "notify.autostart_fail": "Nao consegui mudar o inicio automatico: {err}",
    "probe.raw_privileged": "ICMP raw (processo com privilegio)",
    "probe.ping_no_admin": "ping do SO (sem privilegio de admin)",
    "probe.ping_raw_unavailable": "ping do SO (ICMP raw indisponivel)",
    "lang.auto": "Padrao do sistema",
    "lang.en": "English",
    "lang.pt_BR": "Portugues (Brasil)",
    "rpt.doc_title": "PacketLizer - Relatorio de estabilidade",
    "rpt.header": "PacketLizer — Relatorio de estabilidade",
    "rpt.window_fmt": "Janela: {start} → {end}",
    "rpt.total_fmt": "{n} amostras no total",
    "rpt.generated_fmt": "gerado em {ts}",
    "rpt.multi_intro_fmt": "Este relatorio cobre {n} alvos distintos. Cada bloco abaixo usa apenas as amostras coletadas contra aquele alvo:",
    "rpt.multi_note": "Cada bloco a seguir usa somente as amostras coletadas contra aquele alvo.",
    "rpt.block_target_fmt": "Alvo: {target}",
    "rpt.block_window_fmt": "Janela deste alvo: {start} → {end}  |  {n} amostras coletadas contra este alvo  |  intervalo {interval:.0f}s",
    "rpt.h_latency": "Latencia ao longo do tempo",
    "rpt.h_outages_fmt": "Quedas detectadas ({n})",
    "rpt.h_daily": "Resumo diario",
    "rpt.h_status": "Distribuicao por status",
    "rpt.no_outages": "Nenhuma queda registrada.",
    "rpt.no_data": "Sem dados.",
    "rpt.no_samples": "Sem amostras no intervalo pedido - deixe o monitor rodar antes de gerar o relatorio.",
    "rpt.unknown_target": "(alvo desconhecido)",
    "rpt.footer_fmt": (
        "Gerado por PacketLizer. Cada bloco usa somente as amostras coletadas contra "
        "aquele alvo. Pacotes perdidos aparecem na linha de timeout em {ms:.0f} ms (o "
        "timeout por ping configurado). Uma queda (outage) e uma sequencia de perdas "
        "consecutivas igual ou acima do limiar configurado (outage_min_consecutive)."
    ),
    "col.num": "#",
    "col.start": "Inicio",
    "col.duration": "Duracao",
    "col.lost_packets": "Pacotes perdidos",
    "col.kinds": "Tipos",
    "col.date": "Data",
    "col.samples": "Amostras",
    "col.lost": "Perdidos",
    "col.loss_pct": "Perda %",
    "col.outages": "Quedas",
    "col.avg_latency": "Latencia media",
    "col.status": "Status",
    "col.count": "Qtde",
    "col.pct": "%",
    "kpi.loss": "Perda de pacotes",
    "kpi.loss_sub_fmt": "{lost} de {total} pacotes",
    "kpi.availability": "Disponibilidade",
    "kpi.availability_sub": "janela analisada",
    "kpi.outages": "Quedas",
    "kpi.outages_sub_fmt": "{per_day:.1f} por dia",
    "kpi.downtime": "Tempo total fora",
    "kpi.downtime_sub": "somatorio das quedas",
    "kpi.avg_outage": "Duracao media da queda",
    "kpi.avg_outage_sub_fmt": "mediana {median} / max {max}",
    "kpi.interval_between": "Intervalo medio entre quedas",
    "kpi.interval_between_sub_fmt": "MTBF {mtbf}",
    "kpi.peak_hour": "Horario mais critico",
    "kpi.peak_hour_sub_fmt": "{n} quedas nessa hora",
    "kpi.no_outages": "sem quedas",
    "kpi.peak_weekday": "Dia da semana mais critico",
    "kpi.peak_weekday_sub_fmt": "{n} quedas",
    "kpi.latency_pct": "Latencia p50 / p95",
    "kpi.latency_pct_sub_fmt": "media {avg:.0f} ms - jitter {jitter:.0f} ms",
    "chart.title_fmt": "Latencia x tempo - alvo {target}  (timeout = {ms:.0f} ms)",
    "chart.latency_ms": "Latencia (ms)",
    "chart.lost_packet": "Pacote perdido",
    "chart.timeout_fmt": "Timeout ({ms:.0f} ms)",
    "chart.loss_per_hour": "Perda %/h",
    "weekday.0": "Segunda",
    "weekday.1": "Terca",
    "weekday.2": "Quarta",
    "weekday.3": "Quinta",
    "weekday.4": "Sexta",
    "weekday.5": "Sabado",
    "weekday.6": "Domingo",
}

LANGUAGES: dict[str, dict[str, str]] = {"en": _EN, "pt_BR": _PT_BR}

_current = "en"


def available_languages() -> list[str]:
    """Selectable language codes, ``"auto"`` first."""
    return ["auto", *LANGUAGES]


def language_display_name(code: str) -> str:
    return t(f"lang.{code}") if code != "auto" else t("lang.auto")


def detect_system_language() -> str:
    """Best-effort mapping of the OS UI language to a supported code."""
    candidates: list[str] = []
    if sys.platform == "win32":
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            candidates.append(locale.windows_locale.get(lcid, ""))
        except Exception:
            pass
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # getdefaultlocale is deprecated but still the simplest
            candidates.append(locale.getdefaultlocale()[0] or "")
    except Exception:
        pass
    for env in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        candidates.append((os.environ.get(env, "") or "").split(".")[0].split(":")[0])

    for raw in candidates:
        code = (raw or "").replace("-", "_")
        if code in LANGUAGES:
            return code
        root = code.split("_")[0]
        for lang in LANGUAGES:
            if lang.split("_")[0] == root:
                return lang
    return "en"


def set_language(lang: str | None) -> str:
    """Set the active language. ``None``/``"auto"`` -> detect from the OS."""
    global _current
    if not lang or lang == "auto":
        _current = detect_system_language()
    elif lang in LANGUAGES:
        _current = lang
    else:
        log.warning("Unknown language %r, falling back to English", lang)
        _current = "en"
    return _current


def current_language() -> str:
    return _current


def t(key: str, **kwargs) -> str:
    """Translate ``key`` into the active language, then ``str.format(**kwargs)``."""
    text = LANGUAGES.get(_current, _EN).get(key) or _EN.get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text

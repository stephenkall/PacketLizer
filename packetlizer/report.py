"""On-demand report generation: HTML (dashboard + chart), PDF and verbose CSV.

All user-facing strings go through :mod:`packetlizer.i18n`, so the report is
rendered in whatever language the app is currently set to.
"""
from __future__ import annotations

import base64
import csv
import io
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from .analytics import Report, analyze, humanize_seconds
from .config import STATUS_LABEL, Config
from .i18n import current_language, t
from .storage import Storage

log = logging.getLogger("packetlizer.report")


# ---------------------------------------------------------------------------
# time-range selection
# ---------------------------------------------------------------------------
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_dt(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return int(datetime.strptime(value, "%Y-%m-%d").timestamp())


def parse_report_dates(since: str | None, until: str | None) -> tuple[int | None, int | None]:
    """Convert the report dates into (start_ts, end_ts).

    * empty start date -> None  (since the beginning of time)
    * empty end date    -> None  (up to now)
    * date-only end     -> includes the whole day (23:59:59)
    """
    start = _parse_dt(since)
    end = _parse_dt(until)
    if end is not None and until and _DATE_ONLY.fullmatch(until.strip()):
        end += 86399
    return start, end


def _resolve_window(days, since, until) -> tuple[int | None, int | None]:
    start, end = parse_report_dates(since, until)
    if days and start is None:
        start = int(time.time() - days * 86400)
    return start, end


# ---------------------------------------------------------------------------
# chart
# ---------------------------------------------------------------------------
def _downsample(points: list[tuple[int, float | None, int]], max_points: int = 4000):
    """Bucket the points, keeping latency peaks and flagging losses."""
    if len(points) <= max_points:
        return points
    bucket = len(points) // max_points + 1
    out = []
    for i in range(0, len(points), bucket):
        chunk = points[i : i + bucket]
        ts = chunk[len(chunk) // 2][0]
        lost_any = any(st != 0 for _, _, st in chunk)
        rtts = [r for _, r, st in chunk if st == 0 and r is not None]
        if lost_any:
            out.append((ts, None, 1))
        else:
            out.append((ts, max(rtts) if rtts else None, 0))
    return out


def build_chart_png(rep: Report, samples_for_chart: list[tuple[int, float | None, int]]) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    sentinel = rep.timeout_sentinel_ms
    pts = _downsample(samples_for_chart)
    xs = [datetime.fromtimestamp(ts) for ts, _, _ in pts]
    ys = [(r if (st == 0 and r is not None) else sentinel) for _, r, st in pts]
    lost_x = [datetime.fromtimestamp(ts) for ts, _, st in pts if st != 0]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 6.2), gridspec_kw={"height_ratios": [3, 1]}, sharex=True
    )
    ax1.plot(xs, ys, linewidth=0.7, color="#2563eb", label=t("chart.latency_ms"))
    if lost_x:
        ax1.scatter(lost_x, [sentinel] * len(lost_x), s=10, color="#dc2626",
                    zorder=3, label=t("chart.lost_packet"))
    ax1.axhline(sentinel, color="#dc2626", linestyle=":", linewidth=0.8,
                label=t("chart.timeout_fmt", ms=sentinel))
    for o in rep.outages:
        ax1.axvspan(datetime.fromtimestamp(o.start_ts),
                    datetime.fromtimestamp(o.recovered_ts or o.end_ts),
                    color="#dc2626", alpha=0.12)
    ax1.set_ylim(0, sentinel * 1.08)
    ax1.set_ylabel(t("chart.latency_ms"))
    ax1.set_title(t("chart.title_fmt", target=rep.target, ms=sentinel))
    ax1.grid(True, alpha=0.25)
    ax1.legend(loc="upper right", fontsize=8)

    # loss % per calendar hour
    if rep.daily:
        by_hour: dict[datetime, list[int]] = {}
        for ts, _, st in samples_for_chart:
            h = datetime.fromtimestamp(ts).replace(minute=0, second=0, microsecond=0)
            b = by_hour.setdefault(h, [0, 0])
            b[0] += 1
            b[1] += 1 if st != 0 else 0
        hh = sorted(by_hour)
        ax2.bar(hh, [by_hour[h][1] / by_hour[h][0] * 100 for h in hh],
                width=0.03, color="#dc2626")
    ax2.set_ylabel(t("chart.loss_per_hour"))
    ax2.grid(True, alpha=0.25)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d %H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def _kpi_cards(rep: Report) -> list[tuple[str, str, str]]:
    """List of (title, value, sub-line) for the dashboard, localized."""
    peak_h = rep.peak_hour
    peak_wd = rep.peak_weekday
    return [
        (t("kpi.loss"), f"{rep.loss_pct:.2f}%",
         t("kpi.loss_sub_fmt", lost=rep.lost, total=rep.total)),
        (t("kpi.availability"), f"{rep.availability_pct:.3f}%", t("kpi.availability_sub")),
        (t("kpi.outages"), f"{rep.outage_count}",
         t("kpi.outages_sub_fmt", per_day=rep.outages_per_day)),
        (t("kpi.downtime"), humanize_seconds(rep.total_downtime_s), t("kpi.downtime_sub")),
        (t("kpi.avg_outage"), humanize_seconds(rep.avg_outage_s),
         t("kpi.avg_outage_sub_fmt", median=humanize_seconds(rep.median_outage_s),
           max=humanize_seconds(rep.max_outage_s))),
        (t("kpi.interval_between"), humanize_seconds(rep.mean_interval_between_outages_s),
         t("kpi.interval_between_sub_fmt", mtbf=humanize_seconds(rep.mtbf_s))),
        (t("kpi.peak_hour"),
         f"{peak_h:02d}h" if peak_h is not None else "-",
         t("kpi.peak_hour_sub_fmt", n=rep.hour_histogram[peak_h]) if peak_h is not None
         else t("kpi.no_outages")),
        (t("kpi.peak_weekday"),
         t(f"weekday.{peak_wd}") if peak_wd is not None else "-",
         t("kpi.peak_weekday_sub_fmt", n=rep.weekday_histogram[peak_wd]) if peak_wd is not None
         else t("kpi.no_outages")),
        (t("kpi.latency_pct"), f"{rep.latency['p50']:.0f} / {rep.latency['p95']:.0f} ms",
         t("kpi.latency_pct_sub_fmt", avg=rep.latency['avg'], jitter=rep.latency['jitter'])),
    ]


def _fmt_ts(ts: int | None) -> str:
    """Locale-neutral timestamp (ISO-like)."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"


def _html_section(rep: Report, chart_png: bytes, idx: int) -> str:
    chart_b64 = base64.b64encode(chart_png).decode("ascii")
    cards = "".join(
        f'<div class="card"><div class="k">{title}</div><div class="v">{value}</div>'
        f'<div class="s">{sub}</div></div>'
        for title, value, sub in _kpi_cards(rep)
    )
    outage_rows = "".join(
        f"<tr><td>{i + 1}</td><td>{o.start_dt.strftime('%Y-%m-%d %H:%M:%S')}</td>"
        f"<td>{humanize_seconds(o.duration_s)}</td><td>{o.lost_count}</td>"
        f"<td>{', '.join(f'{k}:{n}' for k, n in o.status_kinds.items())}</td></tr>"
        for i, o in enumerate(rep.outages)
    ) or f'<tr><td colspan="5">{t("rpt.no_outages")}</td></tr>'
    daily_rows = "".join(
        f"<tr><td>{d['date']}</td><td>{d['samples']}</td><td>{d['lost']}</td>"
        f"<td>{d['loss_pct']:.2f}%</td><td>{d['outages']}</td>"
        f"<td>{('%.0f ms' % d['avg_rtt_ms']) if d['avg_rtt_ms'] is not None else '-'}</td></tr>"
        for d in rep.daily
    ) or f'<tr><td colspan="6">{t("rpt.no_data")}</td></tr>'
    status_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td><td>{v / rep.total * 100:.2f}%</td></tr>"
        for k, v in sorted(rep.status_breakdown.items())
    )
    return f"""  <section class="target-block">
  <h2 id="target-{idx}">{t("rpt.block_target_fmt", target=rep.target)}</h2>
  <p class="sub">{t("rpt.block_window_fmt", start=_fmt_ts(rep.first_ts), end=_fmt_ts(rep.last_ts),
                     n=rep.total, interval=rep.interval_seconds)}</p>

  <div class="grid">{cards}</div>

  <h3>{t("rpt.h_latency")}</h3>
  <img alt="{t("rpt.h_latency")} - {rep.target}" src="data:image/png;base64,{chart_b64}">

  <h3>{t("rpt.h_outages_fmt", n=rep.outage_count)}</h3>
  <table><thead><tr><th>{t("col.num")}</th><th>{t("col.start")}</th><th>{t("col.duration")}</th>
  <th>{t("col.lost_packets")}</th><th>{t("col.kinds")}</th></tr></thead>
  <tbody>{outage_rows}</tbody></table>

  <h3>{t("rpt.h_daily")}</h3>
  <table><thead><tr><th>{t("col.date")}</th><th>{t("col.samples")}</th><th>{t("col.lost")}</th>
  <th>{t("col.loss_pct")}</th><th>{t("col.outages")}</th><th>{t("col.avg_latency")}</th></tr></thead>
  <tbody>{daily_rows}</tbody></table>

  <h3>{t("rpt.h_status")}</h3>
  <table><thead><tr><th>{t("col.status")}</th><th>{t("col.count")}</th><th>{t("col.pct")}</th></tr></thead>
  <tbody>{status_rows}</tbody></table>
  </section>"""


def render_html(sections: list[tuple[Report, bytes]], first_ts, last_ts) -> str:
    gen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_all = sum(rep.total for rep, _ in sections)
    if len(sections) > 1:
        nav = (f'<p class="nav">{t("rpt.multi_intro_fmt", n=len(sections))}</p>'
               '<ul class="nav">%s</ul>') % "".join(
            f'<li><a href="#target-{i}">{rep.target}</a> '
            f'&mdash; {rep.total}, {_fmt_ts(rep.first_ts)} &rarr; {_fmt_ts(rep.last_ts)}</li>'
            for i, (rep, _) in enumerate(sections))
    else:
        nav = ""
    body = "\n".join(_html_section(rep, png, i) for i, (rep, png) in enumerate(sections))
    sentinel = sections[0][0].timeout_sentinel_ms
    return f"""<!doctype html>
<html lang="{current_language().replace('_', '-').lower()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t("rpt.doc_title")}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background:#f6f7f9; color:#111; }}
 header {{ background:#0f172a; color:#fff; padding:20px 28px; }}
 header h1 {{ margin:0; font-size:20px; }}
 header p {{ margin:4px 0 0; opacity:.8; font-size:13px; }}
 main {{ max-width:1160px; margin:0 auto; padding:24px 28px 60px; }}
 .nav {{ font-size:13px; }} .nav a {{ color:#2563eb; }}
 .target-block {{ border-top:3px solid #0f172a; margin-top:34px; padding-top:6px; }}
 .target-block:first-of-type {{ border-top:0; margin-top:10px; }}
 .sub {{ color:#6b7280; font-size:13px; margin:2px 0 16px; }}
 .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:14px; }}
 .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:14px 16px; }}
 .card .k {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:#6b7280; }}
 .card .v {{ font-size:24px; font-weight:700; margin:6px 0 2px; }}
 .card .s {{ font-size:12px; color:#6b7280; }}
 h2 {{ margin:20px 0 4px; font-size:18px; }}
 h3 {{ margin:26px 0 10px; font-size:15px; }}
 img {{ max-width:100%; border:1px solid #e5e7eb; border-radius:10px; background:#fff; }}
 table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid #e5e7eb; border-radius:10px; overflow:hidden; }}
 th, td {{ text-align:left; padding:8px 12px; border-bottom:1px solid #eef0f2; font-size:13px; }}
 th {{ background:#f9fafb; }}
 @media (prefers-color-scheme: dark) {{
   body {{ background:#0b0f17; color:#e5e7eb; }}
   .card, table, img {{ background:#111827; border-color:#1f2937; }}
   th {{ background:#0f172a; }} td, th {{ border-color:#1f2937; }}
   .card .k, .card .s, .sub {{ color:#9ca3af; }}
   .target-block {{ border-color:#334155; }}
 }}
</style></head><body>
<header>
  <h1>{t("rpt.header")}</h1>
  <p>{t("rpt.window_fmt", start=_fmt_ts(first_ts), end=_fmt_ts(last_ts))}
     &nbsp;|&nbsp; {t("rpt.total_fmt", n=total_all)} &nbsp;|&nbsp; {t("rpt.generated_fmt", ts=gen)}</p>
</header>
<main>
  {nav}
{body}

  <p style="margin-top:30px;color:#6b7280;font-size:12px">{t("rpt.footer_fmt", ms=sentinel)}</p>
</main></body></html>"""


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def render_pdf(sections: list[tuple[Report, bytes]], first_ts, last_ts, out_path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Image,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph(t("rpt.header"), styles["Title"]))
    targets = ", ".join(rep.target for rep, _ in sections)
    story.append(Paragraph(
        f"{t('rpt.window_fmt', start=_fmt_ts(first_ts), end=_fmt_ts(last_ts))}<br/>"
        f"{t('rpt.total_fmt', n=sum(r.total for r, _ in sections))}<br/>"
        f"{t('kpi.outages')} ({len(sections)}): {targets}<br/>"
        f"{t('rpt.generated_fmt', ts=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}",
        styles["Normal"]))
    if len(sections) > 1:
        story.append(Paragraph(f"<i>{t('rpt.multi_note')}</i>", styles["Normal"]))
    story.append(Spacer(1, 0.4 * cm))

    for si, (rep, chart_png) in enumerate(sections):
        if si:
            story.append(PageBreak())
        story.append(Paragraph(t("rpt.block_target_fmt", target=rep.target), styles["Heading1"]))
        story.append(Paragraph(
            t("rpt.block_window_fmt", start=_fmt_ts(rep.first_ts), end=_fmt_ts(rep.last_ts),
              n=rep.total, interval=rep.interval_seconds),
            styles["Normal"]))
        story.append(Spacer(1, 0.3 * cm))

        kpi_tbl = Table([[k, v] for k, v, _ in _kpi_cards(rep)], colWidths=[7 * cm, 9 * cm])
        kpi_tbl.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f4f7")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(kpi_tbl)
        story.append(Spacer(1, 0.4 * cm))

        from reportlab.lib.utils import ImageReader

        iw, ih = ImageReader(io.BytesIO(chart_png)).getSize()
        w = 17 * cm
        story.append(Image(io.BytesIO(chart_png), width=w, height=w * ih / iw))
        story.append(Spacer(1, 0.4 * cm))

        story.append(Paragraph(t("rpt.h_outages_fmt", n=rep.outage_count), styles["Heading2"]))
        orows = [[t("col.num"), t("col.start"), t("col.duration"), t("col.lost_packets")]]
        for i, o in enumerate(rep.outages[:60]):
            orows.append([str(i + 1), o.start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                          humanize_seconds(o.duration_s), str(o.lost_count)])
        ot = Table(orows, colWidths=[1.2 * cm, 6 * cm, 4 * cm, 3 * cm])
        ot.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f4f7")),
        ]))
        story.append(ot)

    SimpleDocTemplate(str(out_path), pagesize=A4,
                      leftMargin=2 * cm, rightMargin=2 * cm,
                      topMargin=1.5 * cm, bottomMargin=1.5 * cm).build(story)


# ---------------------------------------------------------------------------
# CSV verbose
# ---------------------------------------------------------------------------
def export_csv(cfg: Config, out_path: Path, *, days=None, since=None, until=None) -> int:
    start, end = _resolve_window(days, since, until)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with Storage(cfg.resolved_db_path()) as st, out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp_iso", "timestamp_epoch", "target", "rtt_ms", "status_code", "status"])
        for s in st.iter_samples(start, end):
            iso = datetime.fromtimestamp(s.ts).isoformat()
            w.writerow([iso, s.ts, s.target or "", "" if s.rtt_ms is None else f"{s.rtt_ms:.3f}",
                        s.status, STATUS_LABEL.get(s.status, str(s.status))])
            n += 1
    return n


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
def _load_report(cfg: Config, start, end):
    st = Storage(cfg.resolved_db_path())
    try:
        interval = float(st.get_meta("interval_seconds", str(cfg.interval_seconds)))
        # Chart latency value for a lost packet = the per-ping timeout.
        # Fall back to the current config (older databases stored no timeout_ms).
        sentinel = float(st.get_meta("timeout_ms")
                         or st.get_meta("timeout_sentinel_ms")
                         or cfg.timeout_ms)
        target = st.get_meta("target", cfg.target)
        samples = list(st.iter_samples(start, end))
    finally:
        st.close()
    rep = analyze(
        samples,
        target=target,
        interval_seconds=interval,
        timeout_sentinel_ms=sentinel,
        outage_min_consecutive=cfg.outage_min_consecutive,
    )
    chart_src = [(s.ts, s.rtt_ms, s.status) for s in samples]
    return rep, chart_src


def _load_reports(cfg: Config, start, end) -> list[tuple[Report, list]]:
    """One Report per distinct target present in the range, oldest first."""
    st = Storage(cfg.resolved_db_path())
    try:
        interval = float(st.get_meta("interval_seconds", str(cfg.interval_seconds)))
        sentinel = float(st.get_meta("timeout_ms")
                         or st.get_meta("timeout_sentinel_ms")
                         or cfg.timeout_ms)
        targets = st.distinct_targets(start, end)
        out: list[tuple[Report, list]] = []
        for tgt in targets:
            samples = list(st.iter_samples(start, end, target=tgt))
            if not samples:
                continue
            rep = analyze(
                samples,
                target=(tgt or t("rpt.unknown_target")),
                interval_seconds=interval,
                timeout_sentinel_ms=sentinel,
                outage_min_consecutive=cfg.outage_min_consecutive,
            )
            out.append((rep, [(s.ts, s.rtt_ms, s.status) for s in samples]))
    finally:
        st.close()
    out.sort(key=lambda rc: rc[0].first_ts or 0)
    return out


def generate_reports(cfg: Config, *, out_dir: Path, fmt: str = "both",
                     days=None, since=None, until=None) -> list[Path]:
    start, end = _resolve_window(days, since, until)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = _load_reports(cfg, start, end)
    if not reports:
        raise SystemExit(t("rpt.no_samples"))
    sections = [(rep, build_chart_png(rep, src)) for rep, src in reports]
    first_ts = min(rep.first_ts for rep, _ in reports)
    last_ts = max(rep.last_ts for rep, _ in reports)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = out_dir / f"packetlizer_report_{stamp}"
    made: list[Path] = []
    if fmt in ("html", "both"):
        p = base.with_suffix(".html")
        p.write_text(render_html(sections, first_ts, last_ts), encoding="utf-8")
        made.append(p)
    if fmt in ("pdf", "both"):
        p = base.with_suffix(".pdf")
        render_pdf(sections, first_ts, last_ts, p)
        made.append(p)
    # the verbose CSV (with a 'target' column) always accompanies the report
    csv_path = base.with_suffix(".csv")
    export_csv(cfg, csv_path, days=days, since=since, until=until)
    made.append(csv_path)
    return made

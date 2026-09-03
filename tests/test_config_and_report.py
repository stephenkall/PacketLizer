import csv
from datetime import datetime

from packetlizer.config import Config, load_config
from packetlizer.report import _load_report, export_csv, generate_reports, parse_report_dates
from packetlizer.storage import Storage
from packetlizer.config import STATUS_OK, STATUS_TIMEOUT


def test_parse_report_dates_empty_means_open_ended():
    assert parse_report_dates(None, None) == (None, None)
    assert parse_report_dates("", "  ") == (None, None)


def test_parse_report_dates_date_only_end_is_inclusive():
    start, end = parse_report_dates("2026-09-01", "2026-09-03")
    assert start == int(datetime(2026, 9, 1).timestamp())
    # data final so com dia -> vai ate 23:59:59 daquele dia
    assert end == int(datetime(2026, 9, 3).timestamp()) + 86399


def test_parse_report_dates_accepts_iso_datetime():
    start, end = parse_report_dates("2026-09-01T08:30:00", "2026-09-01T18:00:00")
    assert start == int(datetime(2026, 9, 1, 8, 30).timestamp())
    assert end == int(datetime(2026, 9, 1, 18, 0).timestamp())


def test_config_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    cfg = load_config(str(p))
    assert p.exists()
    assert cfg.target == "www.vivo.com.br"
    cfg.target = "1.1.1.1"
    cfg.save(p)
    again = load_config(str(p))
    assert again.target == "1.1.1.1"


def _seed(db, n=200, timeout_ms="2000"):
    with Storage(db) as st:
        st.set_meta("target", "www.vivo.com.br")
        st.set_meta("interval_seconds", "1.0")
        st.set_meta("timeout_ms", timeout_ms)
        base = 1_700_000_000
        for i in range(n):
            lost = 50 <= i < 60
            st.add(None if lost else 20.0 + (i % 4), STATUS_TIMEOUT if lost else STATUS_OK, base + i)
        st.commit()


def test_export_csv(tmp_path):
    db = tmp_path / "d.db"
    _seed(db)
    cfg = Config(db_path=str(db))
    out = tmp_path / "e.csv"
    n = export_csv(cfg, out)
    assert n == 200
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert rows[0]["status"] == "ok"
    assert rows[55]["status"] == "timeout"
    assert rows[55]["rtt_ms"] == ""


def test_generate_reports_html_pdf_csv(tmp_path):
    db = tmp_path / "d.db"
    _seed(db)
    cfg = Config(db_path=str(db))
    made = generate_reports(cfg, out_dir=tmp_path / "out", fmt="both")
    suffixes = sorted(p.suffix for p in made)
    assert suffixes == [".csv", ".html", ".pdf"]
    for p in made:
        assert p.exists() and p.stat().st_size > 0
    html = next(p for p in made if p.suffix == ".html").read_text(encoding="utf-8")
    assert "Perda de pacotes" in html
    assert "data:image/png;base64," in html


def test_report_uses_configured_timeout_as_chart_sentinel(tmp_path):
    db = tmp_path / "d.db"
    _seed(db, timeout_ms="2000")
    cfg = Config(db_path=str(db))
    rep, _ = _load_report(cfg, None, None)
    assert rep.timeout_sentinel_ms == 2000.0

    db2 = tmp_path / "d2.db"
    _seed(db2, timeout_ms="1500")
    rep2, _ = _load_report(Config(db_path=str(db2)), None, None)
    assert rep2.timeout_sentinel_ms == 1500.0


def test_config_has_no_sentinel_field_and_retention_zero_is_unlimited(tmp_path):
    cfg = load_config(str(tmp_path / "config.json"))
    assert "timeout_sentinel_ms" not in cfg.to_json()
    cfg.retention_days = 0
    cfg.save()
    assert load_config(str(tmp_path / "config.json")).retention_days == 0

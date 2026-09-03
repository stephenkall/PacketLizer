import csv
from datetime import datetime

from packetlizer.config import Config, load_config
from packetlizer.report import (
    _load_report,
    _load_reports,
    export_csv,
    generate_reports,
    parse_report_dates,
)
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


def _seed(db, n=200, timeout_ms="2000", target="www.vivo.com.br", base=1_700_000_000,
         lost_range=(50, 60)):
    with Storage(db) as st:
        st.set_meta("target", target)
        st.set_meta("interval_seconds", "1.0")
        st.set_meta("timeout_ms", timeout_ms)
        for i in range(n):
            lost = lost_range[0] <= i < lost_range[1]
            st.add(None if lost else 20.0 + (i % 4),
                   STATUS_TIMEOUT if lost else STATUS_OK, base + i, target=target)
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


def test_report_separates_blocks_by_target(tmp_path):
    db = tmp_path / "multi.db"
    # alvo antigo, depois alvo novo (janelas de tempo distintas)
    _seed(db, n=120, target="www.vivo.com.br", base=1_700_000_000, lost_range=(30, 45))
    _seed(db, n=80, target="1.1.1.1", base=1_700_100_000, lost_range=(10, 14))
    cfg = Config(db_path=str(db))

    reports = _load_reports(cfg, None, None)
    assert [r.target for r, _ in reports] == ["www.vivo.com.br", "1.1.1.1"]
    assert reports[0][0].total == 120
    assert reports[1][0].total == 80
    # a perda de cada bloco vem so das suas amostras
    assert reports[0][0].lost == 15
    assert reports[1][0].lost == 4

    made = generate_reports(cfg, out_dir=tmp_path / "out", fmt="both")
    html = next(p for p in made if p.suffix == ".html").read_text(encoding="utf-8")
    assert "Alvo: www.vivo.com.br" in html
    assert "Alvo: 1.1.1.1" in html
    assert html.count("Latencia ao longo do tempo") == 2

    csv_path = next(p for p in made if p.suffix == ".csv")
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert {r["target"] for r in rows} == {"www.vivo.com.br", "1.1.1.1"}


def test_report_attributes_old_data_to_its_own_target(tmp_path):
    """Trocar o alvo nao deve reetiquetar as amostras antigas."""
    db = tmp_path / "switch.db"
    _seed(db, n=60, target="alvo-antigo", base=1_700_000_000, lost_range=(0, 0))
    _seed(db, n=60, target="alvo-novo", base=1_700_050_000, lost_range=(0, 0))
    reports = _load_reports(Config(db_path=str(db)), None, None)
    assert {r.target for r, _ in reports} == {"alvo-antigo", "alvo-novo"}
    assert all(r.total == 60 for r, _ in reports)


def test_config_has_no_sentinel_field_and_retention_zero_is_unlimited(tmp_path):
    cfg = load_config(str(tmp_path / "config.json"))
    assert "timeout_sentinel_ms" not in cfg.to_json()
    cfg.retention_days = 0
    cfg.save()
    assert load_config(str(tmp_path / "config.json")).retention_days == 0

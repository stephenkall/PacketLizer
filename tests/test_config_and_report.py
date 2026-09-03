import csv

from packetlizer.config import Config, load_config
from packetlizer.report import export_csv, generate_reports
from packetlizer.storage import Storage
from packetlizer.config import STATUS_OK, STATUS_TIMEOUT


def test_config_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    cfg = load_config(str(p))
    assert p.exists()
    assert cfg.target == "www.vivo.com.br"
    cfg.target = "1.1.1.1"
    cfg.save(p)
    again = load_config(str(p))
    assert again.target == "1.1.1.1"


def _seed(db, n=200):
    with Storage(db) as st:
        st.set_meta("target", "www.vivo.com.br")
        st.set_meta("interval_seconds", "1.0")
        st.set_meta("timeout_sentinel_ms", "9999")
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

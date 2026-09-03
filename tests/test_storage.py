import sqlite3
import time

from packetlizer.config import STATUS_OK, STATUS_TIMEOUT
from packetlizer.storage import Storage


def test_target_column_and_distinct_targets(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        base = 1_700_000_000
        for i in range(10):
            st.add(10.0, STATUS_OK, base + i, target="a")
        for i in range(5):
            st.add(10.0, STATUS_OK, base + 100 + i, target="b")
        st.commit()
        assert st.distinct_targets() == ["a", "b"]
        assert [s.target for s in st.iter_samples(target="a")] == ["a"] * 10
        assert len(list(st.iter_samples(target="b"))) == 5


def test_migration_backfills_target_from_meta(tmp_path):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE samples (ts INTEGER NOT NULL, rtt_ms REAL, status INTEGER NOT NULL);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
    )
    con.execute("INSERT INTO meta VALUES ('target', 'alvo-antigo')")
    con.executemany("INSERT INTO samples VALUES (?,?,?)",
                    [(1_700_000_000 + i, 12.0, 0) for i in range(20)])
    con.commit()
    con.close()

    with Storage(db) as st:
        assert st.distinct_targets() == ["alvo-antigo"]
        assert all(s.target == "alvo-antigo" for s in st.iter_samples())


def test_roundtrip_and_range(tmp_path):
    db = tmp_path / "t.db"
    with Storage(db) as st:
        base = 1_700_000_000
        for i in range(100):
            st.add(10.0 + i, STATUS_OK if i % 5 else STATUS_TIMEOUT, base + i)
        st.commit()
        assert st.count() == 100
        assert st.count(start=base + 50) == 50
        lo, hi = st.time_bounds()
        assert lo == base and hi == base + 99
        got = list(st.iter_samples(base + 10, base + 19))
        assert len(got) == 10
        assert got[0].ts == base + 10


def test_meta(tmp_path):
    with Storage(tmp_path / "m.db") as st:
        assert st.get_meta("target", "x") == "x"
        st.set_meta("target", "www.vivo.com.br")
        assert st.get_meta("target") == "www.vivo.com.br"


def test_purge(tmp_path):
    with Storage(tmp_path / "p.db") as st:
        now = int(time.time())
        st.add(1.0, STATUS_OK, now - 10 * 86400)
        st.add(1.0, STATUS_OK, now)
        st.commit()
        removed = st.purge_older_than(now - 5 * 86400)
        assert removed == 1
        assert st.count() == 1


def _seed_two_targets(st, base=1_700_000_000):
    for i in range(30):
        st.add(10.0, STATUS_OK, base + i, target="a")
    for i in range(20):
        st.add(10.0, STATUS_OK, base + 1000 + i, target="b")
    st.commit()


def test_delete_samples_by_target_only(tmp_path):
    with Storage(tmp_path / "d.db") as st:
        _seed_two_targets(st)
        removed = st.delete_samples(targets=["a"])
        assert removed == 30
        assert st.distinct_targets() == ["b"]
        assert st.count() == 20


def test_delete_samples_by_date_only(tmp_path):
    base = 1_700_000_000
    with Storage(tmp_path / "d.db") as st:
        _seed_two_targets(st, base)
        # window covers only target "a" rows
        removed = st.delete_samples(start=base, end=base + 29)
        assert removed == 30
        assert st.count() == 20
        assert st.distinct_targets() == ["b"]


def test_delete_samples_by_target_and_date(tmp_path):
    base = 1_700_000_000
    with Storage(tmp_path / "d.db") as st:
        _seed_two_targets(st, base)
        # target "b" but a window that only overlaps its first 5 rows
        removed = st.delete_samples(start=base + 1000, end=base + 1004, targets=["b"])
        assert removed == 5
        assert st.count() == 45
        assert st.count_samples(targets=["b"]) == 15
        assert st.count_samples(targets=["a"]) == 30


def test_delete_samples_needs_a_filter(tmp_path):
    import pytest

    with Storage(tmp_path / "d.db") as st:
        _seed_two_targets(st)
        with pytest.raises(ValueError):
            st.delete_samples()


def test_clear_all_samples(tmp_path):
    with Storage(tmp_path / "d.db") as st:
        _seed_two_targets(st)
        st.set_meta("target", "a")
        removed = st.clear_all_samples()
        assert removed == 50
        assert st.count() == 0
        assert st.get_meta("target") == "a"  # meta is preserved

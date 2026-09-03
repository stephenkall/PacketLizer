import time

from packetlizer.config import STATUS_OK, STATUS_TIMEOUT
from packetlizer.storage import Storage


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

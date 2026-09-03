import pytest

from packetlizer import i18n


@pytest.fixture(autouse=True)
def _reset_language():
    yield
    i18n.set_language("en")


def test_available_languages_lists_auto_first():
    langs = i18n.available_languages()
    assert langs[0] == "auto"
    assert "en" in langs and "pt_BR" in langs


def test_translation_switches_with_language():
    i18n.set_language("en")
    assert i18n.t("win.btn.quit") == "Quit"
    i18n.set_language("pt_BR")
    assert i18n.t("win.btn.quit") == "Encerrar programa"


def test_unknown_language_falls_back_to_english():
    i18n.set_language("xx_YY")
    assert i18n.current_language() == "en"
    assert i18n.t("win.btn.pause") == "Pause"


def test_auto_resolves_to_a_supported_language():
    resolved = i18n.set_language("auto")
    assert resolved in i18n.LANGUAGES


def test_missing_key_returns_key_itself():
    i18n.set_language("en")
    assert i18n.t("no.such.key") == "no.such.key"


def test_placeholder_formatting_and_bad_placeholder_is_safe():
    i18n.set_language("en")
    assert i18n.t("notify.report_done", names="a.html, b.pdf") == "Report generated: a.html, b.pdf"
    # missing placeholder -> returns the unformatted string instead of raising
    assert "{names}" in i18n.t("notify.report_done")


def test_pt_br_has_every_english_key():
    missing = set(i18n.LANGUAGES["en"]) - set(i18n.LANGUAGES["pt_BR"])
    assert not missing, f"pt_BR missing keys: {sorted(missing)}"

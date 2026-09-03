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


@pytest.mark.parametrize("code", [c for c in i18n.LANGUAGES if c != "en"])
def test_every_language_has_the_same_keys_as_english(code):
    en_keys = set(i18n.LANGUAGES["en"])
    lang_keys = set(i18n.LANGUAGES[code])
    assert not (en_keys - lang_keys), f"{code} missing: {sorted(en_keys - lang_keys)}"
    assert not (lang_keys - en_keys), f"{code} has extra: {sorted(lang_keys - en_keys)}"


def test_spanish_and_mandarin_are_available():
    assert {"es", "zh"} <= set(i18n.available_languages())
    i18n.set_language("es")
    assert i18n.t("win.btn.quit") == "Salir"
    i18n.set_language("zh")
    assert i18n.t("win.btn.quit") == "退出"  # 退出


def test_display_names_are_native_regardless_of_active_language():
    i18n.set_language("en")
    assert i18n.language_display_name("pt_BR") == "Português (Brasil)"
    i18n.set_language("pt_BR")
    assert i18n.language_display_name("pt_BR") == "Português (Brasil)"
    # "auto" is the one that follows the active language
    assert i18n.language_display_name("auto") == i18n.t("lang.auto")

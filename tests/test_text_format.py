from app.services.text_format import normalize_tg_text, to_max_text_payload


def test_normalize_tg_text_trims_and_normalizes_bullets():
    src = " \r\n• one\r\n● two\r\n◦ three\r\n"
    assert normalize_tg_text(src) == "- one\n- two\n- three"


def test_to_max_text_payload_detects_markdown():
    text, format_ = to_max_text_payload("**Bold** and [link](https://example.com)")
    assert text == "**Bold** and [link](https://example.com)"
    assert format_ == "markdown"


def test_to_max_text_payload_plain_text():
    text, format_ = to_max_text_payload("Просто текст без разметки")
    assert text == "Просто текст без разметки"
    assert format_ is None

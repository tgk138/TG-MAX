from app.services.text_format import normalize_tg_text, to_max_text_payload


def test_normalize_tg_text_trims_and_normalizes_bullets():
    src = " \r\n• one\r\n● two\r\n◦ three\r\n"
    assert normalize_tg_text(src) == "- one\n- two\n- three"


def test_to_max_text_payload_converts_to_html():
    text, format_ = to_max_text_payload("**Bold** and ~~strike~~")
    assert "<b>Bold</b>" in text
    assert "<s>strike</s>" in text
    assert format_ == "html"


def test_to_max_text_payload_plain_text():
    text, format_ = to_max_text_payload("Просто текст без разметки")
    assert text == "Просто текст без разметки"
    assert format_ is None

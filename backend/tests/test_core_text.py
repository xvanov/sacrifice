from app.core.text import truncate


def test_short_text_is_unchanged():
    assert truncate("hello", 10) == "hello"


def test_text_at_the_limit_is_unchanged():
    assert truncate("hello", 5) == "hello"

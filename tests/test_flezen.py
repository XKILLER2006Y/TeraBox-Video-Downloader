import pytest
from flezenDL import (
    extract_flezen_id,
    extract_all_flezen_urls,
    get_flezen_info,
    FlezenDirectError,
    FlezenError,
)


def test_extract_flezen_id():
    url = "https://flezen.com/s/da8ernpbjlnl21agq2k0jhlhrthwh5q"
    assert extract_flezen_id(url) == "da8ernpbjlnl21agq2k0jhlhrthwh5q"
    assert extract_flezen_id("https://www.flezen.com/s/test_id_123") == "test_id_123"
    assert extract_flezen_id("https://google.com") is None


def test_extract_all_flezen_urls():
    text = (
        "Check out this video: https://flezen.com/s/da8ernpbjlnl21agq2k0jhlhrthwh5q\n"
        "And another one: 👉 https://flezen.com/s/da8ero1bjlnl21agq2lgo1bhhnxplci\n"
        "Duplicate: https://flezen.com/s/da8ernpbjlnl21agq2k0jhlhrthwh5q"
    )
    urls = extract_all_flezen_urls(text)
    assert len(urls) == 2
    assert "https://flezen.com/s/da8ernpbjlnl21agq2k0jhlhrthwh5q" in urls
    assert "https://flezen.com/s/da8ero1bjlnl21agq2lgo1bhhnxplci" in urls


def test_get_flezen_info_live():
    url = "https://flezen.com/s/da8ernpbjlnl21agq2k0jhlhrthwh5q"
    info = get_flezen_info(url)
    assert info["share_id"] == "da8ernpbjlnl21agq2k0jhlhrthwh5q"
    assert info["size"] == 9516519
    assert "Wi*d" in info["filename"]
    assert info["filename"].endswith(".mp4")
    assert info["views"] >= 0


def test_flezen_404_error():
    with pytest.raises(FlezenDirectError):
        get_flezen_info("https://flezen.com/s/nonexistent_id_99999999999999999999")

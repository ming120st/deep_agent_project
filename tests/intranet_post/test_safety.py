import pytest

from integrations.intranet_post import is_safe_html, safe_url


@pytest.mark.parametrize(
    "url",
    ["https://example.com", "http://example.com/a?b=1&c=2", "https://example.com:8443/x#frag", "HTTPS://EXAMPLE.COM"],
)
def test_safe_urls(url):
    assert safe_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "vbscript:x",
        "data:text/html;base64,xxx",
        "file:///etc/passwd",
        "https://u:p@example.com",
        "https://example.com:99999",
        "https://example.com/\n",  # trailing newline is stripped, but inner ones are not
        "https://exa mple.com",
        "https://example.com/<x>",
        "https://example.com/`x",
        "https://example.com\\@evil.com",
        "https://" + "a" * 2000 + ".com",
        None,
        123,
    ],
)
def test_unsafe_urls(url):
    if url == "https://example.com/\n":
        assert safe_url(url) == "https://example.com/"  # surrounding whitespace is trimmed, not rejected
    else:
        assert safe_url(url) is None


@pytest.mark.parametrize(
    "html",
    [
        "<div style='color:red'>ok</div>",
        '<a href="https://example.com/?a=1&online=1">x</a>',
        "<div>Based on x = y, and online=1</div>",
        '<img src="{{img:a}}" alt="x">',
        "<table><tr><td style='a:b'>c</td></tr></table>",
    ],
)
def test_safe_html_passes(html):
    assert is_safe_html(html)


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script>",
        "<ScRiPt src=x></ScRiPt>",
        "<style>body{}</style>",
        "<iframe src=x></iframe>",
        "<form></form>",
        "<input>",
        "<button>x</button>",
        "<object></object>",
        "<embed>",
        "<link rel=stylesheet>",
        "<meta http-equiv=refresh>",
        "<base href=x>",
        "<img src=x onerror=alert(1)>",
        "<a onclick='x'>t</a>",
        "<div\nonmouseover=x>",
        '<a href="javascript:alert(1)">x</a>',
        "<a href=' JavaScript:alert(1)'>x</a>",
        '<a href="vbscript:x">x</a>',
    ],
)
def test_unsafe_html_is_caught(html):
    assert not is_safe_html(html)

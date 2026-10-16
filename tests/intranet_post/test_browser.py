"""Renders the preview (which imitates the intranet's global CSS) in real Chromium and checks
*computed* styles, i.e. that the page CSS did not leak into the post. Skipped without Chromium."""
import pytest

from integrations.intranet_post import render_post
from integrations.intranet_post.preview import build_preview_html
from integrations.intranet_post.themes import DEFAULT

sync_api = pytest.importorskip("playwright.sync_api")

CHECK_JS = """
() => {
  const root = document.getElementById('IntraBoardView1_Content');
  const first = (sel) => root.querySelector(sel);
  const cs = (el) => getComputedStyle(el);
  return {
    tds: [...root.querySelectorAll('td')].map(td => ({color: cs(td).color, font: cs(td).fontFamily, bg: cs(td).backgroundColor, size: cs(td).fontSize})),
    thCount: root.querySelectorAll('th').length,
    h2: {size: cs(first('h2')).fontSize, weight: cs(first('h2')).fontWeight},
    listPadding: cs(first('ul, ol')).paddingLeft,
    rootFont: cs(root.firstElementChild).fontFamily,
    overflow: document.documentElement.scrollWidth > window.innerWidth,
  };
}
"""


@pytest.fixture(scope="module")
def page():
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # no browser installed
            pytest.skip(f"chromium not available: {exc}")
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        yield page
        browser.close()


def hex_to_rgb(value: str) -> str:
    value = value.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


def test_page_css_does_not_leak_into_the_post(page, sample):
    page.set_content(build_preview_html(render_post(sample)))
    result = page.evaluate(CHECK_JS)

    assert result["tds"], "sample has tables"
    leaked_colour = "rgb(103, 129, 151)"  # the page's td colour (#678197)
    assert all(td["color"] != leaked_colour for td in result["tds"])
    assert all("Malgun" in td["font"] for td in result["tds"])
    assert hex_to_rgb(DEFAULT.primary) in {td["bg"] for td in result["tds"]}  # header cell kept its own background
    assert result["thCount"] == 0
    assert result["h2"]["size"] == "20px"  # not the Bootstrap 30px
    assert result["listPadding"] == "22px"  # not the page's 0
    assert "Malgun" in result["rootFont"]
    assert result["overflow"] is False

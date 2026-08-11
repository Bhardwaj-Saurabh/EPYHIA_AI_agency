from workers.checks import check_site, format_rate_gbp

CATALOG = [
    {"name": "Folding chair", "day_rate": 150},
    {"name": "Marquee tent 6m x 9m", "day_rate": 14000},
]


def good_html() -> str:
    return """<!doctype html><html><head><meta name="viewport" content="width=device-width">
    <title>BrightSide</title></head><body>
    <h2>Folding chair</h2><p>£1.50/day</p>
    <h2>Marquee tent 6m x 9m</h2><p>£140/day</p>
    <p>hello@brightsideparty.example</p></body></html>"""


def test_rate_formatting():
    assert format_rate_gbp(150) == "£1.50"
    assert format_rate_gbp(14000) == "£140"
    assert format_rate_gbp(1200) == "£12"


def test_clean_page_passes():
    assert check_site(good_html(), CATALOG, "hello@brightsideparty.example") == []


def test_missing_item_and_price_flagged():
    html = good_html().replace("Marquee tent 6m x 9m", "Big tent").replace("£140/day", "")
    problems = check_site(html, CATALOG, "hello@brightsideparty.example")
    assert any("Marquee tent" in p and "does not appear" in p for p in problems)
    assert any("£140" in p for p in problems)


def test_fabrications_flagged():
    html = good_html().replace(
        "</body>", "<p>20% off! ★★★★★ testimonials, money-back guarantee</p></body>"
    )
    problems = check_site(html, CATALOG, "hello@brightsideparty.example")
    assert any("discount" in p for p in problems)
    assert any("star" in p for p in problems)
    assert any("testimonial" in p for p in problems)


def test_filler_and_viewport_flagged():
    html = "<html><body>Lorem ipsum TODO</body></html>"
    problems = check_site(html, [], None)
    assert any("lorem ipsum" in p for p in problems)
    assert any("viewport" in p for p in problems)

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


def test_marketing_true_prices_pass():
    from workers.checks import check_marketing_text

    text = "Chairs from £1.50/day and marquee tents at £140/day. hello@brightsideparty.example"
    assert check_marketing_text(text, CATALOG, "hello@brightsideparty.example") == []


def test_marketing_invented_price_flagged():
    from workers.checks import check_marketing_text

    text = "Tents from just £99/day!"
    problems = check_marketing_text(text, CATALOG, None)
    assert any("£99" in p and "not a catalog rate" in p for p in problems)


def test_marketing_fabrications_flagged():
    from workers.checks import check_marketing_text

    text = "20% off this week! Free delivery. Rated 5-star by our happy customers."
    problems = check_marketing_text(text, CATALOG, None)
    assert any("discount" in p for p in problems)
    assert any("delivery" in p for p in problems)
    assert any("star" in p for p in problems)


def test_marketing_wrong_email_flagged():
    from workers.checks import check_marketing_text

    text = "Contact us at info@wrongdomain.com"
    problems = check_marketing_text(text, CATALOG, "hello@brightsideparty.example")
    assert any("unknown email" in p for p in problems)


def test_booking_form_required_and_checked():
    catalog = [{"id": "abc-123", "name": "Chair", "day_rate": 150}]
    html = good_html().replace("Folding chair", "Chair").replace("£1.50/day", "£1.50/day")
    problems = check_site(html, catalog, None, require_booking_form=True)
    assert any("booking-form" in p for p in problems)
    assert any("/api/checkout" in p for p in problems)
    assert any("Chair" in p and "qty input" in p for p in problems)

    with_form = html.replace(
        "</body>",
        """<form id="booking-form">
        <input type="number" data-item-id="abc-123" min="0" value="0">
        <input type="date"><input type="date">
        </form><script>fetch(API_BASE + "/api/checkout")</script></body>""",
    )
    problems = check_site(with_form, catalog, None, require_booking_form=True)
    assert problems == []


def test_marketing_email_at_sentence_end_passes():
    from workers.checks import check_marketing_text

    text = "Questions? Write to hello@brightsideparty.example. We reply fast."
    assert check_marketing_text(text, CATALOG, "hello@brightsideparty.example") == []

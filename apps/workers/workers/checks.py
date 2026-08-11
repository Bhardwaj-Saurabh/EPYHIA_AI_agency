"""Deterministic grounding checks for generated sites (DESIGN.md section 11,
failure catalogue #7): non-LLM verification that the page tells the truth.
Pure functions - unit-testable without any service running."""

import re


def format_rate_gbp(day_rate_cents: int) -> str:
    """Catalog rates render as GBP: whole pounds without decimals, else 2dp."""
    pounds = day_rate_cents / 100
    if day_rate_cents % 100 == 0:
        return f"£{int(pounds)}"
    return f"£{pounds:.2f}"


def check_site(
    html: str,
    catalog: list[dict],
    business_email: str | None,
) -> list[str]:
    """Returns a list of problems; empty means the page passes."""
    problems: list[str] = []
    lower = html.lower()

    for marker in ("lorem ipsum", "todo", "placeholder text", "your text here", "[insert"):
        if marker in lower:
            problems.append(f"contains filler marker '{marker}'")

    if 'name="viewport"' not in lower:
        problems.append("missing viewport meta tag (mobile-first requirement)")

    for item in catalog:
        name = item["name"]
        if name.lower() not in lower:
            problems.append(f"catalog item '{name}' does not appear on the page")
        rate = format_rate_gbp(item["day_rate"])
        if rate not in html:
            problems.append(
                f"price {rate}/day for '{name}' does not appear exactly on the page"
            )

    if business_email and business_email.lower() not in lower:
        problems.append(f"business contact email '{business_email}' missing from the page")

    # Fabrication tripwires: things a rentals brief never contained.
    problems.extend(_fabrication_problems(lower, where="page"))

    return problems


def _fabrication_problems(lower_text: str, where: str) -> list[str]:
    problems = []
    for pattern, label in (
        (r"\b\d+%\s*off\b", "a percentage discount"),
        (r"★|⭐|5[- ]star", "star ratings / review symbols"),
        (r"testimonial", "testimonials"),
        (r"money[- ]back guarantee", "a money-back guarantee"),
        (r"free delivery|we deliver", "delivery (the brief says collection only)"),
    ):
        if re.search(pattern, lower_text):
            problems.append(f"{where} invents {label} that the brief never mentioned")
    return problems


_PRICE_RE = re.compile(r"£\s?(\d+(?:\.\d{1,2})?)")


def check_marketing_text(
    text: str,
    catalog: list[dict],
    business_email: str | None,
) -> list[str]:
    """Grounding check for one marketing artifact (DESIGN.md sec. 5.8 / 11):
    every price mentioned must be a real catalog rate, and nothing may be
    fabricated. Artifacts don't have to mention everything - but what they
    mention must be true."""
    problems: list[str] = []
    lower = text.lower()

    for marker in ("lorem ipsum", "todo", "[insert", "placeholder"):
        if marker in lower:
            problems.append(f"artifact contains filler marker '{marker}'")

    valid_prices = {format_rate_gbp(item["day_rate"]).lstrip("£") for item in catalog}
    for match in _PRICE_RE.finditer(text):
        raw = match.group(1)
        normalized = raw.rstrip("0").rstrip(".") if "." in raw else raw
        if raw not in valid_prices and normalized not in valid_prices:
            problems.append(f"artifact mentions price £{raw} which is not a catalog rate")

    if business_email:
        # Any email mentioned must be the real one. Strip sentence-ending
        # punctuation the regex may have swallowed.
        for m in re.finditer(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
            found = m.group(0).rstrip(".,;:").lower()
            if found != business_email.lower():
                problems.append(f"artifact mentions unknown email '{found}'")

    problems.extend(_fabrication_problems(lower, where="artifact"))
    return problems

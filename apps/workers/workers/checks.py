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
    for pattern, label in (
        (r"\b\d+%\s*off\b", "a percentage discount"),
        (r"★|⭐|5[- ]star", "star ratings / review symbols"),
        (r"testimonial", "testimonials"),
        (r"money[- ]back guarantee", "a money-back guarantee"),
    ):
        if re.search(pattern, lower):
            problems.append(f"page invents {label} that the brief never mentioned")

    return problems

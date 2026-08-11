"""Deterministic pieces of the video renderer (no ffmpeg, no R2)."""

from gate.executors.video import _hex, parse_storyboard, render_frame

STORYBOARD = """Shot 1 — 4 seconds
Visual: Warm Cream background.
On-screen text: “Introducing The Biscuit Barn”

Shot 2 — 3 seconds
Visual: Service cards.
On-screen text: “Standard kennel £22/day” / “Large kennel £28/day”

Shot 3 — 4 seconds
Visual: Closing card.
On-screen text: “Book online”
"""


def test_parse_storyboard_shots_durations_and_split_lines():
    shots = parse_storyboard(STORYBOARD)
    assert [s["seconds"] for s in shots] == [4, 3, 4]
    assert shots[0]["lines"] == ["Introducing The Biscuit Barn"]
    assert shots[1]["lines"] == ["Standard kennel £22/day", "Large kennel £28/day"]


def test_parse_storyboard_clamps_duration_and_skips_textless_shots():
    shots = parse_storyboard(
        "Shot 1 — 45 seconds\nOn-screen text: “Way too long”\n\n"
        "Shot 2 — 4 seconds\nVisual: no text\n"
    )
    assert len(shots) == 1
    assert shots[0]["seconds"] == 8  # clamped


def test_hex_parses_and_falls_back():
    assert _hex("#355E4A", "#000000") == (0x35, 0x5E, 0x4A)
    assert _hex("not-a-colour", "#D6A756") == (0xD6, 0xA7, 0x56)
    assert _hex("", "#FFFFFF") == (255, 255, 255)


def test_render_frame_produces_correct_sizes():
    palette = {
        "primary": (53, 94, 74),
        "accent": (214, 167, 86),
        "surface": (255, 247, 232),
        "ink": (41, 45, 43),
    }
    shot = {"seconds": 4, "lines": ["Standard kennel £22/day", "Large kennel £28/day"]}
    for size in ((1920, 1080), (1080, 1920)):
        img = render_frame(size, shot, 1, 3, palette, "The Biscuit Barn", "example.com")
        assert img.size == size

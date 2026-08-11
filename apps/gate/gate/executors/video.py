"""/video-render (DESIGN.md sec. 4): deterministic storyboard-to-MP4 rendering
executed inside the gate - no external video API, no per-render cost.

The approved payload carries the exact storyboard text; each shot becomes a
brand-styled frame (Pillow), and ffmpeg (the static binary bundled by
imageio-ffmpeg) composites them into a landscape and a vertical cut with
crossfades. Both files land in R2 and are recorded as marketing_artifacts
rows carrying the approver's name - the render approval IS the human approval
for these two artifacts.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ..db import pool
from ..pipeline import ExecutorContext, ExecutorResult

FPS = 30
XFADE = 0.5  # seconds of crossfade between shots

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # debian (fly image)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # macOS (local dev)
    "/System/Library/Fonts/Helvetica.ttc",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def parse_storyboard(text: str) -> list[dict[str, Any]]:
    """'Shot N — Xs ... On-screen text: "A" / "B"' -> [{seconds, lines}]."""
    shots = []
    for block in re.split(r"(?im)^shot\s+\d+", text)[1:]:
        m = re.match(r"[^\d]*(\d+)\s*second", block)
        seconds = int(m.group(1)) if m else 4
        tm = re.search(r"On-screen text:\s*(.+)", block)
        if not tm:
            continue
        raw = tm.group(1).strip()
        lines = [seg.strip().strip('"“”') for seg in raw.split(" / ") if seg.strip().strip('"“”')]
        if lines:
            shots.append({"seconds": max(2, min(seconds, 8)), "lines": lines})
    return shots


def _hex(c: str, fallback: str) -> tuple[int, int, int]:
    c = c if re.fullmatch(r"#[0-9A-Fa-f]{6}", c or "") else fallback
    return tuple(int(c[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _mix(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_frame(
    size: tuple[int, int],
    shot: dict[str, Any],
    idx: int,
    total: int,
    palette: dict[str, tuple],
    business: str,
    footer: str,
) -> Image.Image:
    w, h = size
    primary, accent, surface, ink = (
        palette["primary"],
        palette["accent"],
        palette["surface"],
        palette["ink"],
    )
    first, last = idx == 0, idx == total - 1
    bg = primary if (first or last) else surface
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)

    # Soft vertical tint so the background is never a flat fill.
    tint = _mix(bg, accent if (first or last) else primary, 0.14)
    for y in range(h):
        t = y / h
        if t > 0.55:
            d.line([(0, y), (w, y)], fill=_mix(bg, tint, (t - 0.55) / 0.45))

    # Brand details: barn-roof line up top, accent underline mid-frame.
    roof_w, roof_h, cx = w // 5, h // 22, w // 2
    apex = h // 10
    d.line(
        [(cx - roof_w, apex + roof_h), (cx, apex), (cx + roof_w, apex + roof_h)],
        fill=accent,
        width=max(4, h // 180),
    )

    on_dark = first or last
    text_col = (255, 255, 255) if on_dark else ink
    sub_col = _mix(text_col, bg, 0.25)

    eyebrow_f = _font(h // 28)
    eb = business.upper()
    d.text(
        ((w - d.textlength(eb, font=eyebrow_f)) / 2, apex + roof_h + h // 30),
        eb,
        font=eyebrow_f,
        fill=accent if not on_dark else _mix(accent, (255, 255, 255), 0.25),
    )

    headline_f = _font(h // 11 if w > h else h // 16)
    sub_f = _font(h // 20 if w > h else h // 26)
    max_w = int(w * 0.82)
    blocks: list[tuple[str, Any, tuple]] = [(shot["lines"][0], headline_f, text_col)]
    for extra in shot["lines"][1:]:
        blocks.append((extra, sub_f, sub_col))

    wrapped: list[tuple[str, Any, tuple]] = []
    for text, font, col in blocks:
        for line in _wrap(d, text, font, max_w):
            wrapped.append((line, font, col))
    line_h = [int(f.size * 1.35) for _, f, _ in wrapped]
    block_h = sum(line_h)
    y = (h - block_h) / 2 + h * 0.02
    for (line, font, col), lh in zip(wrapped, line_h, strict=False):
        d.text(((w - d.textlength(line, font=font)) / 2, y), line, font=font, fill=col)
        if font is headline_f and line == wrapped[0][0]:
            underline_w = min(d.textlength(line, font=font), w * 0.3)
            d.line(
                [
                    ((w - underline_w) / 2, y + font.size * 1.18),
                    ((w + underline_w) / 2, y + font.size * 1.18),
                ],
                fill=accent,
                width=max(4, h // 200),
            )
        y += lh

    dot_r = max(4, h // 220)
    total_w = total * dot_r * 4
    for i in range(total):
        x = (w - total_w) / 2 + i * dot_r * 4 + dot_r * 2
        cy = h - h // 12
        fill = accent if i == idx else _mix(sub_col, bg, 0.5)
        d.ellipse([x - dot_r, cy - dot_r, x + dot_r, cy + dot_r], fill=fill)

    footer_f = _font(h // 34)
    d.text(
        ((w - d.textlength(footer, font=footer_f)) / 2, h - h // 12 + dot_r * 3),
        footer,
        font=footer_f,
        fill=sub_col,
    )
    return img


def _compose_mp4(frames: list[Path], durations: list[int], out: Path) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y"]
    for f, dur in zip(frames, durations, strict=False):
        cmd += ["-loop", "1", "-t", str(dur), "-i", str(f)]
    if len(frames) == 1:
        filt = f"[0:v]fps={FPS},format=yuv420p[v]"
    else:
        parts, prev, offset = [], "[0:v]", 0.0
        for i in range(1, len(frames)):
            offset += durations[i - 1] - XFADE
            label = "[v]" if i == len(frames) - 1 else f"[x{i}]"
            parts.append(
                f"{prev}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.2f}{label}"
            )
            prev = f"[x{i}]"
        parts[-1] = parts[-1][: -len("[v]")] + "[xf]"
        parts.append(f"[xf]fps={FPS},format=yuv420p[v]")
        filt = ";".join(parts)
    cmd += [
        "-filter_complex", filt, "-map", "[v]",
        "-c:v", "libx264", "-preset", "medium", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)


def _upload_r2(local: Path, key: str) -> None:
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    s3.upload_file(str(local), os.environ["R2_BUCKET"], key, ExtraArgs={"ContentType": "video/mp4"})


def video_render_executor(payload: Any, ctx: ExecutorContext) -> ExecutorResult:
    p = payload if isinstance(payload, dict) else {}
    run_id = p.get("runId")
    storyboard = p.get("storyboard")
    business = p.get("businessName")
    if not run_id or not storyboard or not business:
        raise ValueError("video_render needs runId, storyboard, businessName")

    shots = parse_storyboard(storyboard)
    if len(shots) < 3:
        raise ValueError(f"storyboard parsed to only {len(shots)} shots - not renderable")

    colors = p.get("brandColors") or []
    palette = {
        "primary": _hex(colors[0] if colors else "", "#355E4A"),
        "accent": _hex(colors[1] if len(colors) > 1 else "", "#D6A756"),
        "surface": _hex(colors[2] if len(colors) > 2 else "", "#FFF7E8"),
        "ink": _hex(colors[4] if len(colors) > 4 else "", "#292D2B"),
    }
    footer = p.get("siteUrl") or p.get("contactEmail") or business

    with pool.connection() as conn:
        approver = conn.execute(
            "SELECT approved_by FROM actions WHERE id = %s", (ctx.action_id,)
        ).fetchone()
        brand = conn.execute(
            """SELECT brand_document_id AS id FROM runs WHERE id = %s""", (run_id,)
        ).fetchone()
    approved_by = (approver or {}).get("approved_by") or "system"
    if not brand or not brand["id"]:
        raise ValueError("run has no brand document - videos must follow the approved brand")

    durations = [s["seconds"] for s in shots]
    keys = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for cut, size in (("landscape", (1920, 1080)), ("vertical", (1080, 1920))):
            frames = []
            for i, shot in enumerate(shots):
                f = tmpdir / f"{cut}-{i}.png"
                render_frame(size, shot, i, len(shots), palette, business, footer).save(f)
                frames.append(f)
            out = tmpdir / f"launch-{cut}.mp4"
            _compose_mp4(frames, durations, out)
            key = f"{ctx.tenant_id}/videos/{run_id[:8]}-launch-{cut}.mp4"
            _upload_r2(out, key)
            keys[cut] = (key, out.stat().st_size)

    with pool.connection() as conn:
        for cut, a_type, channel in (
            ("landscape", "VIDEO_LANDSCAPE", "landscape-16x9"),
            ("vertical", "VIDEO_VERTICAL", "social-vertical-9x16"),
        ):
            conn.execute(
                """INSERT INTO marketing_artifacts
                     (tenant_id, run_id, brand_document_id, artifact_type, sequence_number,
                      channel, r2_object_key, mime_type, self_review_status,
                      grounding_check_status, approval_status, approved_by, approved_at)
                   VALUES (%s,%s,%s,%s,1,%s,%s,'video/mp4','PASSED','PASSED',
                           'APPROVED',%s,now())
                   ON CONFLICT (run_id, artifact_type, sequence_number) DO UPDATE
                     SET r2_object_key = EXCLUDED.r2_object_key,
                         approved_by = EXCLUDED.approved_by, approved_at = now(),
                         updated_at = now()""",
                (ctx.tenant_id, run_id, brand["id"], a_type, channel, keys[cut][0], approved_by),
            )
        # The marketing deliverable is now complete (main.py left it honest at
        # AWAITING_VIDEO_RENDER when the text pack was approved).
        conn.execute(
            """UPDATE tasks SET status = 'DONE', updated_at = now()
                WHERE run_id = %s AND task_type = 'MARKETING_PACK'
                  AND status = 'AWAITING_VIDEO_RENDER'""",
            (run_id,),
        )

    sizes = f"{keys['landscape'][1] // 1024}KB/{keys['vertical'][1] // 1024}KB"
    return ExecutorResult(
        provider_reference=f"r2:{keys['landscape'][0]}|r2:{keys['vertical'][0]}|{sizes}",
        provider_cost_microdollars=0,
    )

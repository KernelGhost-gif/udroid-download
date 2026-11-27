#!/usr/bin/env python3
"""Fail the Android MATE smoke test when the visible desktop canvas is blank."""

from pathlib import Path
import sys

from PIL import Image


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} SCREENSHOT.png", file=sys.stderr)
        return 2

    image_path = Path(sys.argv[1])
    try:
        image = Image.open(image_path).convert("RGB")
    except Exception as exc:
        print(f"Could not decode screenshot {image_path}: {exc}", file=sys.stderr)
        return 2

    width, height = image.size
    if width < 320 or height < 480:
        print(f"Screenshot dimensions are implausible: {width}x{height}", file=sys.stderr)
        return 1

    # Ignore Android status/navigation bars and Termux:X11's extra-key rows.
    # This leaves the actual X11 desktop canvas, including the MATE panels.
    desktop = image.crop((
        int(width * 0.01),
        int(height * 0.08),
        int(width * 0.99),
        int(height * 0.86),
    ))
    pixels = list(desktop.getdata())
    total = len(pixels)
    visible = sum(1 for r, g, b in pixels if max(r, g, b) > 24)
    chromatic = sum(
        1 for r, g, b in pixels
        if max(r, g, b) > 35 and (max(r, g, b) - min(r, g, b)) > 14
    )
    visible_ratio = visible / total
    chromatic_ratio = chromatic / total

    print(
        "MATE desktop screenshot: "
        f"{width}x{height}, "
        f"non-dark={visible_ratio:.1%}, "
        f"colourful={chromatic_ratio:.1%}"
    )

    # The default Ubuntu MATE wallpaper is green and covers most of the
    # desktop. A black X11 canvas with only panels/cursor must fail this test.
    if visible_ratio < 0.20 or chromatic_ratio < 0.10:
        print(
            "FAIL: desktop canvas is mostly blank/black; Caja has not rendered "
            "the MATE desktop wallpaper.",
            file=sys.stderr,
        )
        return 1

    print("PASS: MATE desktop canvas contains rendered wallpaper/UI pixels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

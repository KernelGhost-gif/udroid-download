#!/usr/bin/env python3
"""Reject a blank MATE desktop or a missing Mutiny left-side Plank dock."""

from pathlib import Path
import sys

from PIL import Image


def ratio(image: Image.Image, predicate) -> float:
    pixels = list(image.getdata())
    return sum(1 for pixel in pixels if predicate(pixel)) / len(pixels)


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

    # The CI emulator is portrait and shows Android status/navigation rows and
    # Termux extra keys outside the X11 canvas. The desktop itself occupies the
    # centre band. A landscape X11 capture is already almost entirely desktop.
    if height > width:
        desktop = image.crop((
            0,
            int(height * 0.035),
            width,
            int(height * 0.68),
        ))
    else:
        desktop = image

    desktop_width, desktop_height = desktop.size
    pixels = list(desktop.getdata())
    total = len(pixels)
    visible = sum(1 for r, g, b in pixels if max(r, g, b) > 24)
    chromatic = sum(
        1 for r, g, b in pixels
        if max(r, g, b) > 35 and (max(r, g, b) - min(r, g, b)) > 14
    )
    visible_ratio = visible / total
    chromatic_ratio = chromatic / total

    # Mutiny uses Plank as a persistent vertical dock. The strip is deliberately
    # wider than the dock so the check remains robust at different resolutions.
    dock_strip = desktop.crop((0, 0, max(1, int(desktop_width * 0.08)), desktop_height))
    left_dock_ratio = ratio(dock_strip, lambda rgb: max(rgb) < 80)

    print(
        "MATE Mutiny screenshot: "
        f"{width}x{height}, "
        f"non-dark={visible_ratio:.1%}, "
        f"colourful={chromatic_ratio:.1%}, "
        f"left-dock-dark={left_dock_ratio:.1%}"
    )

    if visible_ratio < 0.20 or chromatic_ratio < 0.10:
        print(
            "FAIL: desktop canvas is mostly blank/black; Caja has not rendered "
            "the Ubuntu MATE wallpaper.",
            file=sys.stderr,
        )
        return 1

    if left_dock_ratio < 0.20:
        print(
            "FAIL: Mutiny's vertical Plank dock is not visibly rendered along "
            "the left edge of the desktop.",
            file=sys.stderr,
        )
        return 1

    print("PASS: the screenshot contains the wallpaper and a visible left-side Mutiny dock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

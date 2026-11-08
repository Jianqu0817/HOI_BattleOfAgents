"""Compose a generated mouth frame while preserving the rest of a sprite exactly."""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def compose_pair(source, generated, target, mouth_box, *, feather=1.2):
    """Keep source alpha and all pixels outside the supplied mouth rectangle."""
    with Image.open(source) as image:
        original = image.convert("RGBA")
    with Image.open(generated) as image:
        edited = image.convert("RGBA")
    if edited.size != original.size:
        raise ValueError("Generated frame must have the same canvas as the source")
    left, top, right, bottom = mouth_box
    if not (0 <= left < right <= original.width and 0 <= top < bottom <= original.height):
        raise ValueError("Mouth rectangle must lie inside the sprite")
    width, height = right - left, bottom - top
    blend = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(blend)
    draw.ellipse((4, 4, width - 4, height - 4), fill=255)
    blend = blend.filter(ImageFilter.GaussianBlur(feather))
    patch = Image.composite(edited.crop(mouth_box), original.crop(mouth_box), blend)
    result = original.copy()
    result.paste(patch, mouth_box)
    result.putalpha(original.getchannel("A"))
    before, after = np.asarray(original), np.asarray(result)
    outside = np.ones(before.shape[:2], dtype=bool)
    outside[top:bottom, left:right] = False
    if not np.array_equal(before[outside], after[outside]):
        raise RuntimeError("Compositing changed pixels outside the mouth")
    changed_pixels = int(np.any(before != after, axis=2).sum())
    if changed_pixels <= 20:
        raise ValueError("Generated mouth frame did not visibly change")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target)
    return {"outside_mouth_pixels_identical": True, "alpha_identical": True,
            "changed_pixels": changed_pixels, "mouth_box": list(mouth_box)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--generated", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mouth-box", nargs=4, type=int, required=True, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    args = parser.parse_args()
    print(compose_pair(args.source, args.generated, args.out, args.mouth_box))

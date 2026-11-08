"""Render static stage examples from layered art, existing sprites and a saved game frame."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "assets/stage"
SIZE = (3840, 2160)


def screen_geometry(name):
    background = cv2.imread(str(STAGE / "backdrops" / (name + ".png")))
    hsv = cv2.cvtColor(background, cv2.COLOR_BGR2HSV)
    binary = cv2.inRange(hsv, np.array([85, 25, 75]), np.array([120, 165, 240]))
    _, labels, stats, _ = cv2.connectedComponentsWithStats(binary)
    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, width, height, area = map(int, stats[label])
    if area < .08 * SIZE[0] * SIZE[1]:
        raise ValueError("Could not identify the blank screen")
    mask = Image.fromarray(np.where(labels == label, 255, 0).astype("uint8"))
    path = STAGE / "screen_masks" / (name + ".png")
    path.parent.mkdir(exist_ok=True)
    mask.save(path)
    return [x, y, width, height], mask


def put_screen(canvas, frame, bounds, mask):
    x, y, width, height = bounds
    pane = Image.new("RGB", (width, height), "#152737")
    fitted = ImageOps.contain(frame.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
    pane.paste(fitted, ((width - fitted.width)//2, (height - fitted.height)//2))
    layer = Image.new("RGBA", SIZE)
    layer.paste(pane, (x, y))
    layer.putalpha(mask)
    canvas.alpha_composite(layer)


def spotlight(center):
    layer = Image.new("RGBA", SIZE)
    draw = ImageDraw.Draw(layer)
    draw.polygon([(center-65, 0), (center+65, 0), (center+510, 2080), (center-510, 2080)], fill=(255, 233, 190, 21))
    draw.ellipse((center-420, 1970, center+420, 2110), fill=(255, 230, 178, 65))
    return layer.filter(ImageFilter.GaussianBlur(45))


def actor(canvas, path, *, center, height, bottom=2117, active=True, upper_body=False):
    with Image.open(ROOT / path) as original:
        sprite = original.convert("RGBA")
    if upper_body:
        sprite = sprite.crop((0, 0, sprite.width, 960))
    sprite = sprite.resize((round(sprite.width*height/sprite.height), height), Image.Resampling.LANCZOS)
    alpha = sprite.getchannel("A")
    rgb = sprite.convert("RGB")
    if not active:
        rgb = ImageEnhance.Color(rgb).enhance(.65)
        rgb = ImageEnhance.Brightness(rgb).enhance(.56)
    sprite = rgb.convert("RGBA")
    sprite.putalpha(alpha)
    canvas.alpha_composite(sprite, (center-sprite.width//2, bottom-height))


def render(frame_path=None):
    (STAGE / "examples").mkdir(exist_ok=True)
    (STAGE / "lighting").mkdir(exist_ok=True)
    target = STAGE / "examples/hoi4_saved_frame.png"
    source = Path(frame_path) if frame_path else target if target.exists() else ROOT / "exports/production_review_20261109/recording_check/paused_game_frame.png"
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    with Image.open(target) as image:
        frame = image.convert("RGB")
    geometry = {}
    for name in ("council", "briefing"):
        geometry[name] = screen_geometry(name)
    for side, center in (("left", 615), ("right", 3225)):
        spotlight(center).save(STAGE / "lighting" / (side + "_speaker.png"))
    examples = []
    for speaker in ("left", "right"):
        canvas = Image.open(STAGE / "backdrops/council.png").convert("RGBA")
        put_screen(canvas, frame, *geometry["council"])
        canvas.alpha_composite(Image.open(STAGE / "lighting" / (speaker + "_speaker.png")).convert("RGBA"))
        actor(canvas, "assets/characters/poses/gemini/cat_paws/excited.png", center=615, height=1858, active=speaker=="left")
        actor(canvas, "assets/characters/poses/claude/book_hold/neutral.png", center=3225, height=1858, active=speaker=="right")
        canvas.alpha_composite(Image.open(STAGE / "foregrounds/debate_podiums.png").convert("RGBA"), (0, 300))
        name = "council_" + speaker + "_speaking.png"
        canvas.convert("RGB").save(STAGE / "examples" / name)
        examples.append(name)
    canvas = Image.open(STAGE / "backdrops/briefing.png").convert("RGBA")
    put_screen(canvas, frame, *geometry["briefing"])
    canvas.alpha_composite(Image.open(STAGE / "lighting/right_speaker.png").convert("RGBA"))
    actor(canvas, "assets/characters/poses/qwen/fan_greeting/curious.png", center=3264, height=1793, bottom=2073)
    canvas.convert("RGB").save(STAGE / "examples/briefing_presenter.png")
    examples.append("briefing_presenter.png")
    canvas = Image.open(STAGE / "backdrops/speaker.png").convert("RGBA")
    canvas.alpha_composite(spotlight(1920))
    actor(canvas, "assets/characters/poses/claude/book_hold/amused.png", center=1920, height=1900, bottom=2150, upper_body=True)
    canvas.alpha_composite(Image.open(STAGE / "foregrounds/speaker_podium.png").convert("RGBA"), (0, 500))
    canvas.convert("RGB").save(STAGE / "examples/speaker_close.png")
    examples.append("speaker_close.png")
    layout = {"canvas": list(SIZE), "screen_fit": "contain; preserve full game UI with navy side bars when needed",
              "screen_bounds": {name: bounds for name, (bounds, _) in geometry.items()},
              "screen_masks": {name: f"screen_masks/{name}.png" for name in geometry},
              "actor_slots": {"council": {"left": {"center_x":615,"height":1858,"bottom":2117}, "right": {"center_x":3225,"height":1858,"bottom":2117}},
                              "briefing": {"center_x":3264,"height":1793,"bottom":2073}, "speaker": {"center_x":1920,"height":1900,"bottom":2150,"crop_height":960}},
              "foreground_offsets": {"debate_podiums": [0,300], "speaker_podium": [0,500]},
              "examples": examples,
              "map_example_notice": "Saved game screenshot used for a static layout preview, not a live feed."}
    (STAGE / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"examples": examples, "canvas": SIZE, "screen_bounds": layout["screen_bounds"]}, ensure_ascii=False))


def render_academy(frame_path=None):
    """Map-first lecture-room revision, keeping the presenter beside the screen."""
    (STAGE / "examples").mkdir(exist_ok=True)
    target = STAGE / "examples/hoi4_saved_frame.png"
    source = Path(frame_path) if frame_path else target if target.exists() else ROOT / "assets/stage/examples/hoi4_saved_frame.png"
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    bounds, mask = screen_geometry("map_focus")
    with Image.open(target) as image:
        frame = image.convert("RGB")
    background = Image.open(STAGE / "backdrops/map_focus.png").convert("RGBA")
    put_screen(background, frame, bounds, mask)
    background.convert("RGB").save(STAGE / "examples/screen_only.png")
    right = SIZE[0] - 30
    available = right - (bounds[0] + bounds[2] + 20)
    slots = {}
    for identity, filename, sprite_path, with_lectern in [
        ("qwen", "map_presenter.png", "assets/characters/poses/qwen/fan_greeting/curious.png", False),
        ("claude", "lectern_presenter.png", "assets/characters/poses/claude/book_hold/amused_talk.png", True),
    ]:
        with Image.open(ROOT / sprite_path) as sprite:
            bbox = sprite.getchannel("A").point(lambda value: 255 if value > 200 else 0).getbbox()
            height = min(1728, int(available * sprite.height / (bbox[2] - bbox[0])))
            scale = height / sprite.height
            left = round(right - bbox[2] * scale)
            center = left + round(sprite.width * scale) // 2
        canvas = background.copy()
        actor(canvas, sprite_path, center=center, height=height, bottom=2100)
        slots[identity] = {"center_x": center, "height": height, "bottom": 2100}
        if with_lectern:
            with Image.open(STAGE / "foregrounds/side_lectern.png") as image:
                foreground = image.convert("RGBA")
            box = foreground.getchannel("A").point(lambda value: 255 if value > 10 else 0).getbbox()
            furniture = foreground.crop(box)
            furniture.thumbnail((available, 756), Image.Resampling.LANCZOS)
            canvas.alpha_composite(furniture, (right-furniture.width, SIZE[1]-furniture.height))
        canvas.convert("RGB").save(STAGE / "examples" / filename)
    fit = ImageOps.contain(frame, (bounds[2], bounds[3]), Image.Resampling.LANCZOS).size
    previous = ImageOps.contain(frame, (2228, 1013), Image.Resampling.LANCZOS).size
    layout = {"canvas": list(SIZE), "theme": "academy", "screen_bounds": bounds,
              "screen_mask": "screen_masks/map_focus.png", "game_frame_fit": list(fit),
              "map_area_vs_previous_briefing": round(fit[0]*fit[1]/(previous[0]*previous[1]), 3),
              "actor_slots": slots, "screen_fit": "contain, preserve the complete screenshot",
              "notice": "Static examples use a previously saved paused game screenshot, not a new turn or live feed."}
    (STAGE / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(layout, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-frame", type=Path, help="Use a saved game screenshot instead of the bundled example")
    parser.add_argument("--theme", choices=("classic", "academy"), default="academy")
    arguments = parser.parse_args()
    if arguments.theme == "academy":
        STAGE = ROOT / "assets/stage/academy"
        render_academy(arguments.game_frame)
    else:
        render(arguments.game_frame)

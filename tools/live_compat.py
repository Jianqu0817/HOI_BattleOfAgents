"""Local Windows/Chinese profile for the pinned, unmodified Live Tools backend."""
import datetime
import re


def ai_receipt_from_reads(lines, known_tags):
    candidates = {(match[1].lower(), match[2].upper()) for text in lines
                  if (match := re.fullmatch(r"\s*(Enabled|Disabled)\s+A[Il1]\s+for\s*:\s*([A-Z0-9]{3})\s*", text, re.I))
                  and match[2].upper() in known_tags}
    if len(candidates) != 1:
        return None
    state, tag = next(iter(candidates))
    return state.capitalize() + " AI for: " + tag


def reread_ai_receipt(ocr, image, line, origin, known_tags):
    """Resolve only this physical log row, against every configured country equally."""
    resolved = ai_receipt_from_reads([line.text], known_tags)
    if resolved is not None:
        return resolved
    origin_x, origin_y = origin
    x, y, w, h = line.box
    reads = [line.text]
    for margin, region_h in ((8, 24), (8, 25), (9, 26), (10, 28)):
        region = [max(0, origin_x + x - 5), max(0, origin_y + y - margin), max(165, w + 16), max(region_h, h + 12)]
        # The 720p SWI glyphs merge at integer scales. 3.5x preserves all three
        # letters in both recorded console fixtures, without substituting a tag.
        for scale in (3, 3.5, 4, 5):
            detail = ocr.recognize(image, region=region, scale=scale, language="en-US")
            reads.extend(row.text for row in detail.lines)
    return ai_receipt_from_reads(reads, known_tags)


def read_profile_title(session, fallback, img=None, attempts=2):
    image = session.grab() if img is None else img
    rows = session.ocr.recognize(image, region=[360, 120, 560, 45], scale=2).lines
    for row in rows:
        if re.sub(r"\s+", "", row.text) == "联队详情":
            return "air_wing_details", row.text
    return fallback(session, img=image, attempts=attempts)


def army_card_count(text):
    text = (text or "").translate(str.maketrans({"／": "/", "⁄": "/"}))
    matches = {(int(a), int(b)) for a, b in re.findall(r"(\d{1,3})\s*/\s*(\d{1,3})", text)
               if 1 <= int(a) <= int(b) and 24 <= int(b) <= 999}
    return next(iter(matches)) if len(matches) == 1 else None


def chinese_console_frame(image):
    """Verify all four observed frame sides when the army panel fills the log background."""
    import numpy as np
    if image.shape[:2] != (720, 1280):
        return None
    pixels = image.astype(np.int16)
    mask = (np.abs(pixels - np.array([39, 36, 33])) <= 9).all(2) & (pixels[:, :, 0] >= pixels[:, :, 2])
    sides = [mask[165:177, 18:490].mean(1).max(), mask[503:515, 18:490].mean(1).max(),
             mask[177:503, 5:19].mean(0).max(), mask[177:503, 488:502].mean(0).max()]
    if min(sides) >= .8 and pixels[520:538, 18:490].mean() < 70:
        return [10, 169, 499, 510]
    return None


def completion_notice_title(text):
    value = re.sub(r"[^a-z\u4e00-\u9fff]", "", (text or "").lower())
    return value in {"国策完成", "科研完成", "研究完成", "nationalfocuscompleted", "researchcompleted", "researchfinished"}


def chinese_research_slots(lines):
    groups = {}
    for line in lines:
        index = int((line.box[1] - 290) // 100)
        if index >= 0:
            groups.setdefault(index, []).append(line)
    result = []
    for index, values in sorted(groups.items()):
        text = re.sub(r"\s+", "", " ".join(line.text for line in values))
        box = [12, 300 + index * 100, 500, 88]
        if "选择" in text and "科技" in text:
            result.append({"index": index + 1, "state": "idle", "box": box})
            continue
        days_line = next((line for line in values if re.search(r"\d+\s*[天日]", line.text)), None)
        if days_line:
            result.append({"index": index + 1, "state": "busy", "box": box,
                           "name": re.sub(r"\s+", "", " ".join(line.text for line in values if line is not days_line)),
                           "days": int(re.search(r"\d+", days_line.text)[0])})
    return result


def parse_chinese_date(text, lenient=False):
    match = re.search(r"(\d{1,2})\s*[:.;：]\s*(\d{2})\s*[,，]?\s*(\d{1,2})\s+(\d{1,2})\s*月\s*[,，]?\s*(\d{4})", text or "")
    if not match:
        return None
    hour, minute, day, month, year = map(int, match.groups())
    if not 0 <= hour <= 24 or minute != 0:
        return None
    try:
        datetime.date(year, month, day)
    except ValueError:
        return None
    return {"year": year, "month": month, "day": day, "hour": hour,
            "text": match.group(0), "iso": f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00",
            "hoi4": f"{year}.{month}.{day}.{hour}"}

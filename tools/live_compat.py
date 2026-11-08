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


def close_profile_panel(session, panel, fallback, pause_menu_open):
    """Esc closes the topmost diplomacy modal before the underlying side panel."""
    if panel != "diplomacy":
        return fallback(session)
    import time
    session.input.key("esc")
    time.sleep(.8)
    if pause_menu_open(session)[0]:
        session.input.key("esc")
        time.sleep(.6)
    return panel


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


def install():
    if getattr(install, "_installed", False):
        return
    import numpy as np
    from hoi4_live import config, platform
    from hoi4_live.perception import topbar
    from hoi4_live.perception import screen
    from hoi4_live.game import console
    from hoi4_live.game import panels, researchview, focusview, mapview, armyview, menu
    from hoi4_live.gamedata import loc
    from hoi4_live.platform.windows import WindowsPlatform
    from hoi4_live.platform import windows_capture, windows_input
    from hoi4_live.platform.windows_window import GameWindow

    profile = config.get().data.get("iron_profile", {})
    width, height = profile.get("render_size", [1280, 720])
    original_parse = topbar.parse_date
    topbar.parse_date = lambda text, lenient=False: parse_chinese_date(text, lenient) or original_parse(text, lenient)

    screen.PANELS[:0] = [("focus_tree", re.compile(r"国\s*策\s*树")),
                         ("diplomacy", re.compile(r"^\s*外\s*交\s*$")),
                         ("politics", re.compile(r"^\s*政\s*治\s*$")),
                         ("construction", re.compile(r"^\s*建\s*设\s*$")),
                         ("research", re.compile(r"科\s*研")), ("production", re.compile(r"生\s*产"))]
    panels.norm = lambda value: re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (value or "").lower().translate(panels._NORM))
    panels.NOTICE_OK_RX = re.compile(r"^\W*([o0]k|close|continue|好\s*的|确\s*定|关\s*闭)\W*$", re.I)
    original_read_title = panels.read_title
    def read_title(session, img=None, attempts=2):
        return read_profile_title(session, original_read_title, img=img, attempts=attempts)
    panels.read_title = read_title
    original_close_panel = panels.close
    def close_panel(session, name=None):
        return close_profile_panel(session, panels.current(session), original_close_panel, menu.esc_menu_open)
    panels.close = close_panel
    original_notice = panels.find_notice
    def completion_notice(session, img=None):
        notice = original_notice(session, img)
        return notice if notice and completion_notice_title(notice["title"]) else None
    panels.find_notice = completion_notice

    def dismiss_completions(session, max_count=4):
        import time
        titles = []
        for _ in range(max_count):
            notice = completion_notice(session)
            if not notice:
                break
            evidence = session.artifact(session.grab(), "iron_completion_notice")
            x, y, w, h = notice["ok"]
            session.input.click(x + w // 2, y + h // 2)
            time.sleep(.8)
            if not hasattr(session, "_iron_completion_notices"):
                session._iron_completion_notices = []
            session._iron_completion_notices.append({**notice, "screenshot": evidence})
            titles.append(notice["title"])
        return titles
    panels.dismiss_notices = dismiss_completions

    def chinese_army_cards(session, img=None):
        img = session.grab() if img is None else img
        h, _ = img.shape[:2]
        cards, others = [], []
        # At 720p group/plus cards need not be found. Do not discard first/last boxes.
        for cx in armyview.card_boxes(img):
            count = None
            for scale in (1.5, 2.5, 2, 3, 4):
                lines = session.ocr.recognize(img, region=[cx - 38, h - 30, 78, 29], scale=scale).lines
                count = army_card_count(" ".join(line.text for line in lines))
                if count:
                    break
            if count:
                cards.append({"x": cx, "divisions": count[0], "capacity": count[1]})
            else:
                others.append(cx)
        return {"armies": cards, "others": others}
    armyview.card_row = chinese_army_cards
    original_loc = loc.for_session
    def chinese_loc(session):
        if not hasattr(session, "_iron_localisation"):
            source = original_loc(session)
            session._iron_localisation = loc.Localisation(source.game_dir, source.mod_dirs, "simp_chinese", source.cache_dir)
        return session._iron_localisation
    loc.for_session = chinese_loc

    def read_slots(session, img=None):
        image = session.grab() if img is None else img
        rows = session.ocr.recognize(image, region=[0, 290, 520, min(image.shape[0] - 290, 760)], scale=2)
        return chinese_research_slots(rows.lines)
    researchview.read_slots = read_slots

    def find_detail(session, img=None):
        image = session.grab() if img is None else img
        w = image.shape[1]
        rows = session.ocr.recognize(image, region=[int(w * .12), 95, int(w * .76), 140], scale=2)
        return next((line.box for line in rows.lines if re.fullmatch(r"\W*研\s*[究发]\W*", line.text)), None)
    researchview.find_detail = find_detail

    def focus_detail(session, img=None):
        image = session.grab() if img is None else img
        rows = session.ocr.recognize(image, region=focusview._anchor_region(session), scale=2)
        return next((line.box for line in rows.lines if re.fullmatch(r"\W*开\s*始\W*", line.text)), None)
    focusview.find_detail = focus_detail

    def focus_search_state(session, img=None):
        image = session.grab() if img is None else img
        rows = session.ocr.recognize(image, region=focusview._search_strip(session), scale=2).lines
        state = {"found": None, "box": None, "lines": [line.text for line in rows]}
        for line in rows:
            match = re.search(r"(\d+)\s*/\s*(\d+)", line.text)
            if match:
                state["found"] = (int(match[1]), int(match[2]))
            elif "搜索" in re.sub(r"\s+", "", line.text):
                state["box"] = line.box
        return state
    focusview.search_state = focus_search_state

    def find_map_search(session):
        result = mapview._ocr_find(session)
        opened = any(re.sub(r"\s+", "", line.text) in ("查找", "前往首都") for line in result.lines)
        return opened, result
    mapview._find_open = find_map_search

    original_rect = screen.console_rect
    def console_bounds(image):
        # The 720p console's lower border is at y=512; upstream stops at 70%=504.
        padded = np.pad(image, ((0, 100), (0, 0), (0, 0)))
        padded[:120] = 0  # Exclude the top-bar band with the same frame colour.
        # Side panels behind the console can add a later border (e.g. y=568).
        # This profile's console output ends at y=512; its input is below it.
        padded[int(height * .725):] = 0
        return original_rect(padded) or chinese_console_frame(image)
    screen.console_rect = console_bounds

    original_open = console.open_console
    def focused_console(session):
        # A large debug tooltip can make the console toggle's image delta look
        # like an opened console. Clear the hover before testing its frame.
        import time
        session.input.move(600, 60)
        time.sleep(.35)
        mode = original_open(session)
        bounds = screen.console_rect(session.grab())
        if bounds is None:
            raise RuntimeError("IRON cannot locate the console input field")
        x0, y0, x1, y1 = bounds
        session.input.click(x0 + 30, y1 + 17)
        return mode
    console.open_console = focused_console

    def ascii_console_output(session, img=None):
        from hoi4_live.desktop import images
        image = session.grab() if img is None else img
        crop = images.crop(image, console.console_region(image))
        rows = session.ocr.recognize(console._bright_text(crop), scale=2.5, pad=16, language="en-US")
        lines = [line.text for line in rows.lines]
        origin_x, origin_y, _, _ = console.console_region(image)
        receipt_rows = [index for index, text in enumerate(lines) if re.match(r"\s*(Enabled|Disabled)\s+A[Il1]\s+for", text, re.I)]
        known_tags = getattr(session, "_iron_control_tags", ())
        if receipt_rows and known_tags:
            index = receipt_rows[-1]
            resolved = reread_ai_receipt(session.ocr, image, rows.lines[index], (origin_x, origin_y), known_tags)
            if resolved:
                lines[index] = resolved
        return lines, image
    # Engine command receipts are English even in the Chinese game UI.
    console.read_output = ascii_console_output

    original_run = console.run
    def focused_commands(session, commands, keep_open=False, read=True):
        values = [commands] if isinstance(commands, str) else list(commands)
        results = []
        for index, command in enumerate(values):
            from notifications import acknowledge_passive_news
            acknowledge_passive_news(session)
            panels.dismiss_notices(session, max_count=1)
            # Switching countries can recreate a focused widget behind the console.
            # Re-enter the known console input before sending the next command.
            results.extend(original_run(session, [command], keep_open=keep_open or index < len(values) - 1, read=read))
        return results
    console.run = focused_commands

    if profile.get("virtual_keys"):
        def virtual_key(name, up=False):
            scan, extended = windows_input.KEYS[name]
            code = windows_input.win32.user32.MapVirtualKeyW(scan, 1)
            flags = (windows_input.KEYEVENTF_KEYUP if up else 0) | (windows_input.KEYEVENTF_EXTENDEDKEY if extended else 0)
            event = windows_input.INPUT(type=windows_input.INPUT_KEYBOARD)
            event.u.ki = windows_input.KEYBDINPUT(code, scan, flags, 0, 0)
            return event
        windows_input._key = virtual_key

    class NativeCapture(windows_capture.Capturer):
        def _printwindow(self, hwnd):
            # Capture the client directly: unaware games draw their titlebar in
            # logical pixels, so cropping it by physical metrics loses HUD rows.
            import ctypes
            gdi = windows_capture.gdi
            user = windows_capture.win32.user32
            dc = user.GetWindowDC(hwnd)
            memory = gdi.CreateCompatibleDC(dc)
            bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
            previous = gdi.SelectObject(memory, bitmap)
            try:
                if not user.PrintWindow(hwnd, memory, 3):
                    return None
                header = windows_capture.BITMAPINFOHEADER(ctypes.sizeof(windows_capture.BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
                buffer = ctypes.create_string_buffer(width * height * 4)
                gdi.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0)
                return np.frombuffer(buffer.raw, np.uint8).reshape(height, width, 4)[..., 2::-1].copy()
            finally:
                gdi.SelectObject(memory, previous)
                gdi.DeleteObject(bitmap)
                gdi.DeleteDC(memory)
                user.ReleaseDC(hwnd, dc)

        def grab(self, method=None):
            image = super().grab(method)
            h, w = image.shape[:2]
            if (w, h) == (width, height):
                return image
            if w < width or h < height:
                raise RuntimeError("IRON render profile is larger than the captured client")
            if np.any(image[height:]) or np.any(image[:height, width:]):
                raise RuntimeError("IRON capture has content outside the configured native render area")
            return image[:height, :width].copy()

    class NativeInput(windows_input.Input):
        def move(self, x, y, glide=0.0):
            _, _, physical_w, physical_h = self.window.client()
            return super().move(x * physical_w / width, y * physical_h / height, glide)

        def drag(self, x0, y0, x1, y1, button="left", seconds=.6):
            import time
            self.move(x0, y0)
            time.sleep(.05)
            self.mouse_down(button)
            try:
                steps = max(10, int(seconds * 60))
                for index in range(1, steps + 1):
                    fraction = index / steps
                    self.move(x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction)
                    time.sleep(seconds / steps)
            finally:
                self.mouse_up(button)
            time.sleep(self.settle)

    class IronWindows(WindowsPlatform):
        def make_window(self, *args, **kwargs):
            class NativeWindow(GameWindow):
                def size(self):
                    return width, height
            return NativeWindow(*args, **kwargs)

        def make_capturer(self, *args, **kwargs):
            return NativeCapture(*args, **kwargs)

        def make_input(self, *args, **kwargs):
            return NativeInput(*args, **kwargs)

    platform.set_backend(IronWindows())
    install._installed = True

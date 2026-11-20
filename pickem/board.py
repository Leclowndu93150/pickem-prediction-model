import argparse
import io
import json
import math
import random
from datetime import datetime
from pathlib import Path

from curl_cffi import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .store import DATA

ASSETS = Path(__file__).resolve().parent / "assets"
WIDTH = 1840
HEIGHT = 1010
SCALE = 3
ROW_TOP = 76
ROW_STEP = 70.6
SLOT_RADIUS = 36

THEMES = {
    "default": {
        "background": ((34, 41, 51), (16, 20, 26)), "glow": (0.34, 0.42), "pattern": None,
        "frame": (190, 205, 225, 60), "box": (44, 52, 64, 225), "disc": (255, 255, 255, 12),
        "text": (236, 240, 245), "muted": (236, 240, 245, 120), "faint": (236, 240, 245, 60),
        "advance": (30, 122, 92, 255), "eliminate": (130, 38, 62, 255), "win": (62, 172, 86), "loss": (200, 52, 64),
        "accent": (58, 104, 164, 255), "accent_text": (196, 224, 255), "dot": (92, 214, 150),
        "panel_row": (0, 0, 0, 70), "badge": (12, 15, 19, 235),
    },
    "singapore": {
        "background": ((124, 62, 18), (38, 17, 9)), "glow": (0.3, 0.38), "pattern": (255, 170, 80),
        "frame": (255, 205, 150, 70), "box": (74, 33, 27, 222), "disc": (255, 235, 215, 13),
        "text": (246, 236, 222), "muted": (246, 236, 222, 125), "faint": (246, 236, 222, 62),
        "advance": (28, 125, 97, 255), "eliminate": (140, 33, 70, 255), "win": (58, 170, 78), "loss": (198, 49, 60),
        "accent": (60, 105, 165, 255), "accent_text": (190, 222, 255), "dot": (104, 222, 160),
        "panel_row": (20, 6, 2, 95), "badge": (30, 12, 8, 238),
    },
}
THEME_KEYWORDS = {"singapore": "singapore"}

BOXES = {
    (0, 0): (209, 240, 227, 607, 8), (1, 0): (465, 213, 227, 324, 4), (0, 1): (465, 548, 227, 325, 4),
    (2, 0): (720, 187, 227, 181, 2), (1, 1): (720, 380, 227, 324, 4), (0, 2): (720, 717, 227, 181, 2),
    (2, 1): (977, 284, 227, 253, 3), (1, 2): (977, 548, 227, 253, 3), (2, 2): (1233, 412, 227, 253, 3),
}
ARROWS = (
    (458, 403, True), (458, 727, False), (714, 298, True), (714, 488, False), (714, 638, True), (714, 820, False),
    (970, 215, True), (970, 325, False), (970, 468, True), (970, 655, False), (970, 737, True), (970, 858, False),
    (1227, 350, True), (1227, 458, False), (1227, 603, True), (1227, 740, False), (1482, 480, True), (1482, 597, False),
)
THREE_ZERO_SLOTS = ((1045, 229), (1133, 229))
ZERO_THREE_SLOTS = ((1045, 886), (1133, 886))
ADVANCE_SLOTS = ((1545, 362), (1633, 362), (1721, 362), (1545, 449), (1633, 449), (1721, 449))
OUT_2_3_SLOTS = ((1545, 624), (1633, 624), (1721, 624))
OUT_1_3_SLOTS = ((1297, 749), (1385, 749), (1473, 749))
LIST_COLUMNS = (68, 145)
LIST_TOP = 337
LIST_STEP = 64


def theme_for(event_name):
    lowered = event_name.lower()
    return next((theme for keyword, theme in THEME_KEYWORDS.items() if keyword in lowered), "default")


def load_logo(team_id, url):
    folder = DATA / "logos"
    cached = folder / f"{team_id}.png"
    if not cached.exists():
        if not url:
            return None
        body = requests.get(url, impersonate="chrome131", timeout=30).content
        if ".svg" in url.split("?")[0]:
            import cairosvg
            body = cairosvg.svg2png(bytestring=body, output_width=320)
        folder.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(body)
    image = Image.open(cached).convert("RGBA")
    return image.crop(image.getbbox())


class Canvas:
    def __init__(self, theme):
        self.theme = theme
        self.fonts = {}
        self.image = self._background()
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def _background(self):
        inner, outer = self.theme["background"]
        width, height = WIDTH * SCALE, HEIGHT * SCALE
        small = Image.new("RGB", (WIDTH // 8, HEIGHT // 8))
        pixels = small.load()
        cx, cy = self.theme["glow"]
        for y in range(small.height):
            for x in range(small.width):
                distance = min(1.0, math.hypot((x / small.width - cx) * 1.15, (y / small.height - cy) * 1.5) / 0.95)
                mix = distance ** 1.25
                pixels[x, y] = tuple(round(inner[i] + (outer[i] - inner[i]) * mix) for i in range(3))
        image = small.resize((width, height), Image.BICUBIC)
        if self.theme["pattern"]:
            image = Image.alpha_composite(image.convert("RGBA"), self._pattern(width, height)).convert("RGB")
        return image

    def _pattern(self, width, height):
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        rng = random.Random(7)
        light = self.theme["pattern"]
        step = 150 * SCALE
        for row in range(-1, height // step + 2):
            for column in range(-1, width // step + 2):
                cx = column * step + (step // 2 if row % 2 else 0) + rng.uniform(-24, 24) * SCALE
                cy = row * step + rng.uniform(-24, 24) * SCALE
                turn = rng.uniform(0, math.tau)
                size = rng.uniform(30, 46) * SCALE
                tone = light + (rng.choice((13, 17, 21)),) if rng.random() < 0.7 else (20, 8, 4, rng.choice((26, 34)))
                petals = rng.choice((5, 6, 8))
                for petal in range(petals):
                    angle = turn + petal * math.tau / petals
                    draw.polygon(self._petal(cx + math.cos(angle) * size * 0.62, cy + math.sin(angle) * size * 0.62, size * 0.62, size * 0.24, angle), fill=tone)
                draw.ellipse((cx - size * 0.16, cy - size * 0.16, cx + size * 0.16, cy + size * 0.16), fill=tone)
                for sprig in range(3):
                    angle = turn + 0.5 + sprig * math.tau / 3
                    reach = size * rng.uniform(1.5, 1.9)
                    draw.polygon(self._petal(cx + math.cos(angle) * reach, cy + math.sin(angle) * reach, size * 0.42, size * 0.13, angle + 0.6), fill=tone)
        return layer.filter(ImageFilter.GaussianBlur(1.1 * SCALE))

    @staticmethod
    def _petal(cx, cy, length, breadth, angle):
        points = []
        for step in range(20):
            t = step * math.tau / 20
            x, y = math.cos(t) * length, math.sin(t) * breadth
            points.append((cx + x * math.cos(angle) - y * math.sin(angle), cy + x * math.sin(angle) + y * math.cos(angle)))
        return points

    def font(self, size, weight, width, italic):
        key = (size, weight, width, italic)
        if key not in self.fonts:
            font = ImageFont.truetype(str(ASSETS / ("Saira-Italic.ttf" if italic else "Saira.ttf")), round(size * SCALE))
            font.set_variation_by_axes([weight, width])
            self.fonts[key] = font
        return self.fonts[key]

    def text(self, x, y, value, size, fill=None, weight=500, width=100, anchor="lm", tracking=0.0, italic=False):
        font = self.font(size, weight, width, italic)
        fill = fill or self.theme["text"]
        if not tracking:
            self.draw.text((x * SCALE, y * SCALE), value, font=font, fill=fill, anchor=anchor)
            return font.getlength(value) / SCALE
        advances = [font.getlength(character) + tracking * SCALE for character in value]
        total = sum(advances) - tracking * SCALE
        cursor = x * SCALE - (total / 2 if anchor[0] == "m" else total if anchor[0] == "r" else 0)
        for character, advance in zip(value, advances):
            self.draw.text((cursor, y * SCALE), character, font=font, fill=fill, anchor="l" + anchor[1])
            cursor += advance
        return total / SCALE

    def measure(self, value, size, weight=500, width=100, tracking=0.0, italic=False):
        font = self.font(size, weight, width, italic)
        return (font.getlength(value) + tracking * SCALE * (len(value) - 1)) / SCALE

    def box(self, x, y, w, h, fill, radius=7):
        self.draw.rounded_rectangle((x * SCALE, y * SCALE, (x + w) * SCALE, (y + h) * SCALE), radius * SCALE, fill=fill)

    def disc(self, cx, cy, radius, fill=None, outline=None, width=0):
        bounds = ((cx - radius) * SCALE, (cy - radius) * SCALE, (cx + radius) * SCALE, (cy + radius) * SCALE)
        self.draw.ellipse(bounds, fill=fill, outline=outline, width=round(width * SCALE))

    def dashed_circle(self, cx, cy, radius, color, width=2.0, dashes=22):
        bounds = ((cx - radius) * SCALE, (cy - radius) * SCALE, (cx + radius) * SCALE, (cy + radius) * SCALE)
        step = 360 / dashes
        for dash in range(dashes):
            self.draw.arc(bounds, dash * step, dash * step + step * 0.55, fill=color, width=round(width * SCALE))

    def dashed_box(self, x, y, w, h, radius, color, width=2.0, dash=9.0, gap=6.0):
        points = []
        corners = ((x + w - radius, y + radius, -90), (x + w - radius, y + h - radius, 0), (x + radius, y + h - radius, 90), (x + radius, y + radius, 180))
        for cx, cy, start in corners:
            for step in range(9):
                angle = math.radians(start + step * 90 / 8)
                points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
        points.append(points[0])
        travelled = 0.0
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            length = math.hypot(x2 - x1, y2 - y1)
            position = 0.0
            while position < length:
                phase = travelled % (dash + gap)
                run = min(length - position, (dash - phase) if phase < dash else (dash + gap - phase))
                if phase < dash:
                    a, b = position / length, (position + run) / length
                    self.draw.line(((x1 + (x2 - x1) * a) * SCALE, (y1 + (y2 - y1) * a) * SCALE, (x1 + (x2 - x1) * b) * SCALE, (y1 + (y2 - y1) * b) * SCALE), fill=color, width=round(width * SCALE))
                position += run
                travelled += run

    def arrow(self, cx, cy, up):
        angle = math.radians(-21 if up else 24)
        shape = ((-23, -9.5), (7, -9.5), (23, 0), (7, 9.5), (-23, 9.5))
        color = self.theme["win"] if up else self.theme["loss"]
        points = [((cx + x * math.cos(angle) - y * math.sin(angle)) * SCALE, (cy + x * math.sin(angle) + y * math.cos(angle)) * SCALE) for x, y in shape]
        self.draw.polygon(points, fill=color)
        shade = [points[0], points[1], points[2], ((cx - 23 * math.cos(angle)) * SCALE, (cy - 23 * math.sin(angle)) * SCALE)]
        self.draw.polygon(shade, fill=(255, 255, 255, 22))

    def logo(self, image, cx, cy, size, opacity=1.0):
        if image is None:
            return False
        ratio = min(size * SCALE / image.width, size * SCALE / image.height)
        fitted = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.LANCZOS)
        mask = fitted.getchannel("A")
        if opacity < 1.0:
            mask = mask.point(lambda value: round(value * opacity))
        self.image.paste(fitted, (round(cx * SCALE - fitted.width / 2), round(cy * SCALE - fitted.height / 2)), mask)
        return True

    def monogram(self, name, cx, cy, size, opacity=1.0):
        letters = "".join(word[0] for word in name.split()[:2]).upper() or "?"
        self.text(cx, cy, letters, size * 0.5, self.theme["text"][:3] + (round(255 * opacity),), 700, 75, "mm")

    def team(self, team, cx, cy, size, opacity=1.0):
        if not self.logo(team.get("image"), cx, cy, size, opacity):
            self.monogram(team["team"], cx, cy, size, opacity)

    def finish(self):
        return self.image.resize((WIDTH, HEIGHT), Image.LANCZOS)


def records_from(matches):
    records = {}
    for match in matches:
        if match["winner"] is None:
            continue
        loser = match["b"] if match["winner"] == match["a"] else match["a"]
        won, lost = records.get(match["winner"], (0, 0))
        records[match["winner"]] = (won + 1, lost)
        won, lost = records.get(loser, (0, 0))
        records[loser] = (won, lost + 1)
    return records


def pick_state(kind, record):
    won, lost = record
    if kind == "three_zero":
        return True if (won, lost) == (3, 0) else False if lost > 0 else None
    if kind == "zero_three":
        return True if (won, lost) == (0, 3) else False if won > 0 else None
    if won == 3:
        return lost > 0
    return False if lost == 3 else None


def percent(value):
    return f"{100 * value:.0f}%"


def day(text):
    moment = datetime.strptime(text[:10], "%Y-%m-%d")
    return f"{moment.day} {moment:%b %Y}"


def draw_header(canvas, result):
    theme = canvas.theme
    canvas.draw.rounded_rectangle((14 * SCALE, 18 * SCALE, (WIDTH - 14) * SCALE, (HEIGHT - 16) * SCALE), 3 * SCALE, outline=theme["frame"], width=SCALE)
    labels = [f"STAGE {number}" for number in range(1, result.get("stage_count", 1) + 1)] + ["PLAYOFFS"]
    active = result.get("stage_number", 1) - 1
    x = 42
    for index, label in enumerate(labels):
        dotted = index < len(labels) - 1
        inner = canvas.measure(label, 17, 600, 100, 1.1) + (13 if dotted else 0)
        if index == active:
            canvas.box(x, 38, inner + 26, 34, theme["accent"], 4)
        color = theme["accent_text"] if index == active else theme["text"]
        cursor = x + 13
        if dotted:
            canvas.draw.rectangle(((cursor - 4) * SCALE, 52 * SCALE, (cursor + 2) * SCALE, 58 * SCALE), fill=theme["dot"])
            cursor += 9
        canvas.text(cursor, 55, label, 17, color, 600, 100, "lm", 1.1)
        x += inner + 26 + 8
    canvas.draw.line((16 * SCALE, 92 * SCALE, 880 * SCALE, 92 * SCALE), fill=theme["faint"], width=SCALE)
    canvas.disc(43, 130, 10, outline=theme["text"], width=2.2)
    canvas.draw.line((43 * SCALE, 130 * SCALE, 43 * SCALE, 124 * SCALE), fill=theme["text"], width=round(2.2 * SCALE))
    canvas.draw.line((43 * SCALE, 130 * SCALE, 48 * SCALE, 133 * SCALE), fill=theme["text"], width=round(2.2 * SCALE))
    canvas.text(67, 130, f"Model forecast of {day(result['generated'])}, from matches up to {day(result['last_match'])}", 22, weight=400)


def draw_panel(canvas, result):
    theme = canvas.theme
    picks = result["picks"]
    canvas.text(1274, 52, result["event"].upper(), 21, weight=700, width=75, tracking=0.6)
    rows = (
        (f"{result['goal']} or more correct", percent(picks["p_goal"])),
        ("Expected correct, of 10", f"{picks['expected_correct']:.1f}"),
        (f"Seeding ticket, {result['goal']} or more", percent(result["seed_picks"]["p_goal"])),
        ("Simulated stages", f"{result['sims']:,}"),
    )
    for index, (label, value) in enumerate(rows):
        y = 74 + index * 39
        if index % 2 == 0:
            canvas.draw.rectangle((1273 * SCALE, y * SCALE, 1736 * SCALE, (y + 36) * SCALE), fill=theme["panel_row"])
        canvas.text(1286, y + 18, label, 15.5, weight=600)
        canvas.draw.line((1612 * SCALE, y * SCALE, 1612 * SCALE, (y + 36) * SCALE), fill=theme["panel_row"], width=SCALE)
        canvas.text(1674, y + 18, value, 15.5, weight=700, anchor="mm")


def draw_list(canvas, teams):
    canvas.text(106, 262, "TEAMS", 13, weight=700, width=100, anchor="mm", tracking=1.0)
    canvas.text(106, 279, "BY SEED", 13, weight=700, width=100, anchor="mm", tracking=1.0)
    for index, team in enumerate(teams):
        canvas.team(team, LIST_COLUMNS[index % 2], LIST_TOP + (index // 2) * LIST_STEP, 50)


def draw_boxes(canvas, result, by_name):
    theme = canvas.theme
    buckets = {}
    for match in result.get("matches", ()):
        buckets.setdefault(tuple(match["record"]), []).append(match)
    for record, (x, y, w, h, rows) in BOXES.items():
        canvas.box(x, y, w, h, theme["box"])
        canvas.text(x + w - 17, y + 21, f"{record[0]}-{record[1]}", 21, weight=700, width=88, anchor="rm", italic=True)
        listed = buckets.get(record, [])
        for row in range(rows):
            cy = y + ROW_TOP + row * ROW_STEP
            left, right = x + 50, x + w - 52
            canvas.disc(left, cy, 27, fill=theme["disc"])
            canvas.disc(right, cy, 27, fill=theme["disc"])
            if row >= len(listed):
                canvas.text(left, cy, "?", 23, theme["muted"], 500, anchor="mm")
                canvas.text(right, cy, "?", 23, theme["muted"], 500, anchor="mm")
                canvas.text((left + right) / 2, cy, "-", 20, theme["faint"], 500, anchor="mm")
                continue
            match = listed[row]
            decided = match["winner"] is not None
            canvas.team(by_name[match["a"]], left, cy, 40, 0.3 if decided and match["winner"] != match["a"] else 1.0)
            canvas.team(by_name[match["b"]], right, cy, 40, 0.3 if decided and match["winner"] != match["b"] else 1.0)
            if decided:
                canvas.text((left + right) / 2, cy, "-", 20, theme["faint"], 500, anchor="mm")
            else:
                share = round(100 * match["p_a"])
                canvas.text((left + right) / 2 - 3, cy, str(share), 15, theme["text"] if share >= 50 else theme["muted"], 600, 88, "rm")
                canvas.text((left + right) / 2 + 3, cy, str(100 - share), 15, theme["text"] if share < 50 else theme["muted"], 600, 88, "lm")
    for cx, cy, up in ARROWS:
        canvas.arrow(cx, cy, up)


def draw_slot(canvas, team, cx, cy, chance=None, state=None):
    theme = canvas.theme
    if team is None:
        canvas.dashed_circle(cx, cy, SLOT_RADIUS, theme["text"])
        return
    canvas.disc(cx, cy, SLOT_RADIUS, fill=(0, 0, 0, 46), outline=theme["text"], width=2.0)
    canvas.team(team, cx, cy, 48)
    if chance is not None:
        canvas.box(cx - 21, cy + SLOT_RADIUS - 15, 42, 19, theme["badge"], 9.5)
        canvas.text(cx, cy + SLOT_RADIUS - 6, percent(chance), 12.5, weight=700, width=95, anchor="mm")
    if state is not None:
        bx, by = cx + SLOT_RADIUS - 8, cy - SLOT_RADIUS + 8
        canvas.disc(bx, by, 10.5, fill=theme["win"] if state else theme["loss"], outline=theme["text"], width=1.6)
        marks = ((-4.5, 0.3, -1.4, 3.6), (-1.4, 3.6, 4.6, -3.4)) if state else ((-3.6, -3.6, 3.6, 3.6), (-3.6, 3.6, 3.6, -3.6))
        for x1, y1, x2, y2 in marks:
            canvas.draw.line(((bx + x1) * SCALE, (by + y1) * SCALE, (bx + x2) * SCALE, (by + y2) * SCALE), fill=(255, 255, 255), width=round(2.4 * SCALE))


def draw_out_slot(canvas, team, cx, cy):
    theme = canvas.theme
    canvas.disc(cx, cy, SLOT_RADIUS, fill=theme["disc"])
    if team is None:
        canvas.text(cx, cy, "?", 23, theme["faint"], 500, anchor="mm")
    else:
        canvas.team(team, cx, cy, 46, 0.75)


def draw_outcomes(canvas, result, by_name):
    theme = canvas.theme
    picks = result["picks"]
    records = records_from(result.get("matches", ()))
    title = dict(size=17, weight=600, width=88, anchor="mm", tracking=1.5)

    def chance(name, kind):
        odds = by_name[name]["odds"]
        return odds["3-0"] if kind == "three_zero" else odds["0-3"] if kind == "zero_three" else odds["3-1"] + odds["3-2"]

    def fill(kind, slots):
        names = picks[kind]
        for index, (cx, cy) in enumerate(slots):
            name = names[index] if index < len(names) else None
            if name is None:
                draw_slot(canvas, None, cx, cy)
            else:
                draw_slot(canvas, by_name[name], cx, cy, chance(name, kind), pick_state(kind, records.get(name, (0, 0))))

    canvas.box(977, 157, 227, 114, theme["advance"])
    canvas.text(1090, 175, "3-0 (ADVANCE)", **title)
    fill("three_zero", THREE_ZERO_SLOTS)
    canvas.box(977, 814, 227, 114, theme["eliminate"])
    canvas.text(1090, 832, "0-3 (ELIMINATED)", **title)
    fill("zero_three", ZERO_THREE_SLOTS)
    canvas.box(1233, 294, 545, 106, theme["advance"])
    canvas.box(1490, 294, 288, 226, theme["advance"])
    canvas.draw.rectangle((1483 * SCALE, 300 * SCALE, 1500 * SCALE, 400 * SCALE), fill=theme["advance"])
    canvas.text(1361, 337, "3-1  3-2", **title)
    canvas.text(1361, 359, "(ADVANCE)", **title)
    fill("advance", ADVANCE_SLOTS)
    canvas.box(1490, 552, 286, 114, theme["box"])
    canvas.text(1633, 570, "2-3 (ELIMINATED)", **title)
    canvas.box(1233, 677, 305, 114, theme["box"])
    canvas.text(1385, 695, "1-3 (ELIMINATED)", **title)
    for final, slots in (((2, 3), OUT_2_3_SLOTS), ((1, 3), OUT_1_3_SLOTS)):
        out = [name for name, record in records.items() if record == final]
        for index, (cx, cy) in enumerate(slots):
            draw_out_slot(canvas, by_name[out[index]] if index < len(out) else None, cx, cy)
    canvas.dashed_box(1493, 885, 275, 58, 7, theme["text"])
    canvas.text(1630.5, 914, f"{percent(picks['p_goal'])} CHANCE OF {result['goal']}+ CORRECT", 19, weight=600, width=80, anchor="mm", tracking=0.8)


def render(result, theme=None):
    canvas = Canvas(THEMES[theme or theme_for(result["event"])])
    teams = [dict(team, image=load_logo(team["team_id"], team.get("logo"))) for team in result["teams"]]
    by_name = {team["team"]: team for team in teams}
    draw_header(canvas, result)
    draw_panel(canvas, result)
    draw_list(canvas, teams)
    draw_boxes(canvas, result, by_name)
    draw_outcomes(canvas, result, by_name)
    return canvas.finish()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("forecast")
    parser.add_argument("--theme", choices=sorted(THEMES))
    parser.add_argument("--out")
    args = parser.parse_args()
    source = Path(args.forecast)
    result = json.loads(source.read_text(encoding="utf-8"))
    render(result, args.theme).save(args.out or source.with_suffix(".png"), optimize=True)


if __name__ == "__main__":
    main()

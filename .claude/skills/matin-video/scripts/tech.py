"""tech.py — «tech» көрінісі: қара фон, жалбыз-көк неон, Oswald капс, терминал, таймлайн, телефон,
тізім, жарқыл экраны, глитч ауысулар. Жоспарда "theme": {"look": "tech"} болса қосылады.

Сахнаға қосымша өрістер:
  "viz": "laptop" | "phone" | "terminal" | "list" | "flash"  (hook/cta өз түрімен)
  "term": "> connect skill_01 ... OK"         — жоғарғы терминал жолы (латынша)
  "lines": ["> claude edit reel.mp4", ...]      — viz=terminal ішіндегі жолдар (латынша)
  "strike": {"word": "CAPCUT", "at": "CapCut"}  — viz=terminal: қызыл сызықпен өшірілетін сөз
  "items": [["REELS", "Reels"], ...]            — viz=list: [экрандағы жазу, айтылғанда шығатын сөз]
  "num": "10", "unit": "МИНУТ"                  — viz=flash
"""
import math
import os
import re

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import montazh as M

W, H = M.W, M.H
FD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
_F = {}
CAP_Y = 400          # жоғарғы субтитр ортасы
PURPLE = (139, 108, 255)
RED = (255, 59, 92)
WHITE = (255, 255, 255)


def font(px, w=700, mono=False):
    k = (px, w, mono)
    if k not in _F:
        f = ImageFont.truetype(os.path.join(FD, "JetBrainsMono.ttf" if mono else "Oswald.ttf"), px)
        try:
            f.set_variation_by_axes([w])
        except Exception:
            pass
        _F[k] = f
    return _F[k]


def layer(im):
    a = np.asarray(im, dtype=np.float32)
    return a[:, :, :3][:, :, ::-1].copy(), a[:, :, 3:] / 255.0


def blend(fr, L, x, y, al=1.0):
    rgb, a = L
    h, w = a.shape[:2]
    x, y = int(round(x)), int(round(y))
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0 or al <= 0.003:
        return
    aa = a[y0 - y:y1 - y, x0 - x:x1 - x] * al
    sub = fr[y0:y1, x0:x1].astype(np.float32)
    fr[y0:y1, x0:x1] = (sub * (1 - aa) + rgb[y0 - y:y1 - y, x0 - x:x1 - x] * aa).astype(np.uint8)


def glow(img, rad=14, strength=1.0):
    g = img.filter(ImageFilter.GaussianBlur(rad))
    if strength != 1.0:
        g.putalpha(g.split()[3].point(lambda v: min(255, int(v * strength))))
    return Image.alpha_composite(g, img)


def norm(w):
    return re.sub(r"[^\w]", "", w.lower())


def word_time(items, key, default):
    k = norm(key)
    for it in items:
        if norm(it["w"]).startswith(k):
            return it["s"]
    return default


def up(w):
    return M.disp(w).upper()


# ---------------------------------------------------------------- суреттер
def render_caption(items, shown, acc, dark=False, px=80):
    """Жоғарғы субтитр: Oswald капс, маңызды сөз — түсті, айтылмағаны күңгірт."""
    f = font(px, 700)
    sp = f.getlength(" ")
    ws = [f.getlength(up(it["w"])) for it in items]
    lines, cur, cw = [], [], 0
    for i, w in enumerate(ws):
        if cur and cw + sp + w > 900:
            lines.append(cur)
            cur, cw = [], 0
        cur.append(i)
        cw += (sp if len(cur) > 1 else 0) + w
    if cur:
        lines.append(cur)
    lh = int(px * 1.18)
    cwid = 980
    img = Image.new("RGBA", (cwid, lh * len(lines) + 40), (0, 0, 0, 0))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d, g = ImageDraw.Draw(img), ImageDraw.Draw(gl)
    y = 14
    base = (12, 14, 16) if dark else WHITE
    for ln in lines:
        tw = sum(ws[i] for i in ln) + sp * (len(ln) - 1)
        x = (cwid - tw) / 2
        for i in ln:
            on = i < shown
            col = (acc if not dark else (12, 14, 16)) if items[i]["a"] else base
            al = 255 if on else 80
            if items[i]["a"] and on and not dark:
                g.text((x, y), up(items[i]["w"]), font=f, fill=acc + (200,))
            if items[i]["a"] and dark:
                d.rectangle([x - 8, y + px * 0.12, x + ws[i] + 8, y + px * 1.22], fill=(12, 14, 16, al))
                col = WHITE
            d.text((x, y), up(items[i]["w"]), font=f, fill=col + (al,))
            x += ws[i] + sp
        y += lh
    if not dark:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sh.paste((0, 0, 0, 170), (0, 0), img.split()[3])
        img = Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(9)), img)
        img = Image.alpha_composite(gl.filter(ImageFilter.GaussianBlur(16)), img)
    return img


def render_hook(items, shown, acc):
    """Үлкен тік мәтін: әр сөз айтылғанда шығады, маңызды сөз — үлкен әрі түсті."""
    rows, cur = [], []
    for i, it in enumerate(items):
        if it["a"]:
            if cur:
                rows.append(cur)
            rows.append([i])
            cur = []
        else:
            cur.append(i)
            if len(" ".join(up(items[j]["w"]) for j in cur)) > 14:
                rows.append(cur)
                cur = []
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        txt = " ".join(up(items[j]["w"]) for j in r)
        big = items[r[0]]["a"]
        px = 210 if big else 120
        while font(px).getlength(txt) > 900:
            px -= 6
        out.append((r, txt, px, big))
    hh = sum(int(p * 1.12) for _, _, p, _ in out) + 40
    img = Image.new("RGBA", (1000, hh), (0, 0, 0, 0))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d, g = ImageDraw.Draw(img), ImageDraw.Draw(gl)
    y = 10
    for r, txt, px, big in out:
        f = font(px)
        sp = f.getlength(" ")
        tw = f.getlength(txt)
        x = (1000 - tw) / 2
        for j in r:
            w = up(items[j]["w"])
            on = j < shown
            col = acc if big else WHITE
            if big and on:
                g.text((x, y), w, font=f, fill=acc + (255,))
            d.text((x, y - px * 0.08), w, font=f, fill=col + (255 if on else 45,))
            x += f.getlength(w) + sp
        y += int(px * 1.12)
    return Image.alpha_composite(gl.filter(ImageFilter.GaussianBlur(26)), img)


def render_mono(txt, px=30, col=(150, 165, 170)):
    f = font(px, 500, mono=True)
    img = Image.new("RGBA", (int(f.getlength(txt)) + 10, int(px * 1.5)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, 0), txt, font=f, fill=col + (255,))
    return img


def render_laptop(acc):
    img = Image.new("RGBA", (920, 640), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([90, 20, 830, 500], 22, fill=(16, 20, 24, 255), outline=(120, 130, 140, 255), width=6)
    d.polygon([(30, 540), (890, 540), (850, 600), (70, 600)], fill=(60, 66, 74, 255))
    d.rectangle([30, 520, 890, 545], fill=(95, 102, 110, 255))
    d.rounded_rectangle([390, 525, 530, 540], 6, fill=(40, 44, 50, 255))
    return img


def render_phone(acc):
    img = Image.new("RGBA", (520, 900), (0, 0, 0, 0))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(gl).rounded_rectangle([50, 30, 470, 870], 64, outline=acc + (255,), width=16)
    img = Image.alpha_composite(img, gl.filter(ImageFilter.GaussianBlur(24)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([50, 30, 470, 870], 64, fill=(10, 13, 15, 255), outline=acc + (255,), width=7)
    d.rounded_rectangle([200, 52, 320, 82], 15, fill=(0, 0, 0, 255))
    return img


def render_terminal(acc, n_lines):
    hgt = 110 + n_lines * 66 + 40
    img = Image.new("RGBA", (900, hgt), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 899, hgt - 1], 26, fill=(12, 16, 18, 235), outline=acc + (120,), width=3)
    d.rounded_rectangle([0, 0, 899, 70], 26, fill=(24, 30, 34, 255))
    d.rectangle([0, 40, 899, 70], fill=(24, 30, 34, 255))
    for k, c in enumerate(((255, 95, 87), (255, 189, 46), (40, 200, 64))):
        d.ellipse([30 + k * 42, 22, 56 + k * 42, 48], fill=c + (255,))
    f = font(30, 500, mono=True)
    d.text((450 - f.getlength("claude — montazh") / 2, 18), "claude — montazh", font=f, fill=(140, 150, 155, 255))
    return img


def render_line(txt, acc):
    f = font(36, 500, mono=True)
    img = Image.new("RGBA", (860, 60), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 0
    if txt.startswith("[ok]"):
        d.text((0, 6), "[ok]", font=f, fill=acc + (255,))
        x = f.getlength("[ok] ")
        txt = txt[5:]
    d.text((x, 6), txt, font=f, fill=(225, 232, 235, 255))
    return img


def render_strike_word(word, px=230):
    f = font(px)
    tw = f.getlength(word)
    img = Image.new("RGBA", (int(tw) + 60, int(px * 1.25)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((30, -px * 0.08), word, font=f, fill=(235, 238, 240, 255))
    return img


def render_row(idx, label, acc, active):
    img = Image.new("RGBA", (880, 112), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = acc + (255,) if active else (255, 255, 255, 14)
    d.rounded_rectangle([0, 6, 879, 104], 20, fill=fill, outline=acc + (150 if not active else 255,), width=3)
    fm = font(36, 600, mono=True)
    num = "%02d" % idx
    tc = (10, 12, 14, 255) if active else acc + (255,)
    d.text((34, 30), num, font=fm, fill=tc)
    d.line([(115, 30), (115, 82)], fill=(tc[:3] + (120,)), width=2)
    f = font(64)
    lab = label.upper()
    while f.getlength(lab) > 700:
        f = font(f.size - 4)
    d.text((145, 54 - f.size * 0.68), lab, font=f, fill=((10, 12, 14) if active else WHITE) + (255,))
    return img


def render_flash(num, unit):
    img = Image.new("RGBA", (1000, 900), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(520)
    while f.getlength(num) > 940:
        f = font(f.size - 20)
    d.text(((1000 - f.getlength(num)) / 2, -60), num, font=f, fill=(10, 12, 14, 255))
    f2 = font(130)
    d.text(((1000 - f2.getlength(unit)) / 2, 640), unit, font=f2, fill=(10, 12, 14, 255))
    return img


def render_card(text, acc):
    words = text.replace("*", "").upper().split()
    f = font(68)
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.getlength(t) > 860 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    lh = 82
    img = Image.new("RGBA", (960, lh * len(lines) + 30), (0, 0, 0, 0))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d, g = ImageDraw.Draw(img), ImageDraw.Draw(gl)
    for k, l in enumerate(lines):
        col = acc if k == len(lines) - 1 else WHITE
        x = (960 - f.getlength(l)) / 2
        if col == acc:
            g.text((x, 6 + k * lh), l, font=f, fill=acc + (220,))
        d.text((x, 6 + k * lh - 8), l, font=f, fill=col + (255,))
    return Image.alpha_composite(gl.filter(ImageFilter.GaussianBlur(18)), img)


def render_button(label, acc):
    f = font(70)
    lab = label.upper()
    tw = f.getlength(lab)
    bw, bh = int(tw + 150), 136
    img = Image.new("RGBA", (bw + 100, bh + 100), (0, 0, 0, 0))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(gl).rounded_rectangle([50, 50, 50 + bw, 50 + bh], 68, fill=acc + (230,))
    img = Image.alpha_composite(img, gl.filter(ImageFilter.GaussianBlur(26)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([50, 50, 50 + bw, 50 + bh], 68, fill=acc + (255,))
    d.text((50 + (bw - tw) / 2, 50 + bh / 2 - 70 * 0.66), lab, font=f, fill=(10, 12, 14, 255))
    return img


def render_arrow():
    img = Image.new("RGBA", (120, 110), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([50, 0, 70, 55], fill=WHITE + (255,))
    d.polygon([(14, 50), (106, 50), (60, 106)], fill=WHITE + (255,))
    return img


def render_icon(sym, acc, r=46):
    img = Image.new("RGBA", (r * 2 + 40, r * 2 + 40), (0, 0, 0, 0))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(gl).ellipse([20, 20, 20 + 2 * r, 20 + 2 * r], outline=acc + (255,), width=8)
    img = Image.alpha_composite(img, gl.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(img)
    d.ellipse([20, 20, 20 + 2 * r, 20 + 2 * r], fill=(14, 20, 22, 255), outline=acc + (255,), width=4)
    f = font(42)
    d.text((20 + r - f.getlength(sym) / 2, 20 + r - 30), sym, font=f, fill=WHITE + (255,))
    return img


# ---------------------------------------------------------------- рендерер
class TechRenderer:
    def __init__(self, plan, tl, base_scene_cls):
        self.plan, self.tl = plan, tl
        th = plan.get("theme", {})
        self.acc = M.hex2rgb(th.get("accent", "#35E0B5"))
        self.accb = np.array(self.acc[::-1], np.float32)
        self.scenes = []
        for sc, t in zip(plan["scenes"], tl["scenes"]):
            items = t["words"]
            self.scenes.append({"sc": sc, "tl": t, "items": items, "kind": sc.get("kind", "say"),
                                "viz": sc.get("viz", sc.get("kind")),
                                "chunks": M.chunk_words([dict(w) for w in items], 4, 24), "cache": {}})
        self.speech_end = tl["scenes"][-1]["speech_e"] if tl["scenes"] else tl["dur"]
        # фон
        sw, sh = 270, 480
        gy, gx = np.mgrid[0:sh, 0:sw].astype(np.float32)
        self.gx, self.gy, self.sw, self.sh = gx, gy, sw, sh
        r = np.sqrt(((gx - sw / 2) / sw) ** 2 + ((gy - sh * 0.48) / sh) ** 2)
        self.vig = np.clip(1.15 - r * 1.35, 0.25, 1)[:, :, None]
        rng = np.random.default_rng(7)
        self.grain = (rng.standard_normal((H // 2, W // 2)) * 6).astype(np.float32)
        self.grain = np.repeat(np.repeat(self.grain, 2, 0), 2, 1)[:, :, None]
        dots = np.zeros((H, W), np.float32)
        dots[30::60, 30::60] = 1
        dots = cv2.dilate(dots, np.ones((3, 3), np.uint8))
        self.dots = dots[:, :, None] * 0.07
        self.scan = np.ones((H, 1, 1), np.float32)
        self.scan[::4] = 0.93
        self.wave = rng.random(400).astype(np.float32)

    # ---- кэш
    def get(self, s, key, maker):
        c = s["cache"]
        if key not in c:
            c[key] = layer(maker())
        return c[key]

    def background(self, t, flash=0.0):
        base = np.array([13, 11, 9], np.float32)
        img = np.ones((self.sh, self.sw, 3), np.float32) * base
        for k, (ax, ay, sp, rr, a) in enumerate(((0.5, 0.42, 0.05, 110, 0.20), (0.2, 0.85, 0.07, 90, 0.10))):
            cx = self.sw * (ax + 0.15 * math.sin(t * sp * 6.28 + k))
            cy = self.sh * (ay + 0.06 * math.cos(t * sp * 5 + k))
            g = np.exp(-((self.gx - cx) ** 2 + (self.gy - cy) ** 2) / (2 * rr * rr))[:, :, None] * a
            img = img * (1 - g) + self.accb * 0.55 * g
        img = img * self.vig
        if flash > 0:
            img = img * (1 - flash) + self.accb * flash * (0.92 + 0.08 * self.vig)
        big = cv2.resize(img, (W, H), interpolation=cv2.INTER_LINEAR)
        big = big + self.dots * (255 - big) * (1 - flash)
        sh = int(t * 37) % 200
        gr = np.roll(self.grain, sh * 2, axis=0)
        return np.clip((big + gr) * self.scan, 0, 255).astype(np.uint8)

    def scene_at(self, t):
        for s in self.scenes:
            if s["tl"]["s"] <= t < s["tl"]["e"]:
                return s
        for s in self.scenes:
            if s["tl"]["s"] - 0.12 <= t <= s["tl"]["e"] + 0.22:
                return s
        return None

    # ---- динамикалық графика (cv2)
    def draw_timeline(self, fr, x, y, w, h, t, prog):
        """Монтаж таймлайны: дауыс толқыны, клиптер, графика, дыбыс; ойнату сызығы жүреді."""
        acc = tuple(int(v) for v in self.acc[::-1])
        pur = PURPLE[::-1]
        red = RED[::-1]
        th = h // 4
        cv2.rectangle(fr, (x, y), (x + w, y + h), (20, 24, 26), -1)
        for k in range(4):
            yy = y + k * th
            cv2.line(fr, (x, yy), (x + w, yy), (40, 46, 50), 1)
            cy = yy + th // 2
            if k == 0:
                for i in range(0, w, 5):
                    a = self.wave[(i // 5 + int(t * 8)) % 400]
                    hh = int((0.25 + 0.75 * a) * th * 0.42)
                    cv2.line(fr, (x + i, cy - hh), (x + i, cy + hh), acc, 2)
            elif k == 1:
                i = 0
                while i < w:
                    cw = 70 + int(self.wave[i % 400] * 90)
                    cv2.rectangle(fr, (x + i + 3, yy + 8), (x + min(w, i + cw) - 3, yy + th - 8), (90, 120, 60), -1)
                    cv2.rectangle(fr, (x + i + 3, yy + 8), (x + min(w, i + cw) - 3, yy + th - 8), acc, 2)
                    i += cw
            elif k == 2:
                i = 20
                while i < w - 30:
                    bw = 30 + int(self.wave[(i * 3) % 400] * 40)
                    cv2.rectangle(fr, (x + i, yy + 10), (x + min(w - 2, i + bw), yy + th - 10), pur, -1)
                    i += bw + 25
            else:
                for i in range(0, w, 4):
                    a = self.wave[(i // 4 * 7) % 400]
                    hh = int((0.15 + 0.6 * a) * th * 0.4)
                    cv2.line(fr, (x + i, cy - hh), (x + i, cy + hh), red, 1)
        px = x + int(w * prog)
        cv2.line(fr, (px, y - 12), (px, y + h + 12), (80, 80, 255), 3)
        cv2.rectangle(fr, (x, y), (x + w, y + h), (55, 62, 66), 2)

    def glitch(self, fr, k, seed):
        """k 0..1: кесінді жылжу + түс бөлінуі."""
        if k <= 0.01:
            return fr
        rng = np.random.default_rng(seed)
        out = fr.copy()
        for _ in range(int(6 + 10 * k)):
            y0 = int(rng.integers(0, H - 40))
            hh = int(rng.integers(8, 90))
            dx = int(rng.integers(-60, 60) * k)
            out[y0:y0 + hh] = np.roll(fr[y0:y0 + hh], dx, axis=1)
        sh = int(14 * k)
        out[:, :, 2] = np.roll(out[:, :, 2], sh, axis=1)
        out[:, :, 0] = np.roll(out[:, :, 0], -sh, axis=1)
        return out

    def caption(self, fr, s, t, al, dark=False, y=CAP_Y):
        for ci, ch in enumerate(s["chunks"]):
            nxt = s["chunks"][ci + 1]["s"] if ci + 1 < len(s["chunks"]) else 1e9
            if ch["s"] - 0.05 <= t < min(ch["e"] + 0.1, nxt - 0.03):
                shown = max(1, sum(1 for w in ch["items"] if w["s"] <= t + 0.04))
                im = self.get(s, ("cap", ci, shown, dark), lambda: render_caption(ch["items"], shown, self.acc, dark))
                k = M.ease((t - ch["s"] + 0.05) / 0.14)
                blend(fr, im, (W - im[1].shape[1]) / 2, y - im[1].shape[0] / 2 + (1 - k) * 18, al * k)

    def frame(self, t):
        s = self.scene_at(t)
        flash = 0.0
        if s and s["viz"] == "flash":
            st, en = s["tl"]["s"], s["tl"]["e"]
            flash = M.ease((t - st + 0.1) / 0.12) * (1 - M.ease((t - en) / 0.2))
        fr = self.background(t, flash)
        if s is None:
            return fr
        sc, st, en = s["sc"], s["tl"]["s"], s["tl"]["e"]
        lt = t - st
        al = M.ease((lt + 0.12) / 0.25) * (1 - M.ease((t - en) / 0.22))
        acc = self.acc
        viz = s["viz"]
        # жоғарғы терминал жолы
        term = sc.get("term")
        if term:
            n = int(max(0, lt) * 45)
            txt = term[:n] + ("_" if int(t * 3) % 2 == 0 else " ")
            im = self.get(s, ("term", n, txt[-1]), lambda: render_mono(txt, 30, (120, 200, 180) if not flash else (10, 40, 30)))
            blend(fr, im, 80, 238, al)
        if viz == "hook":
            shown = max(1, sum(1 for w in s["items"] if w["s"] <= t + 0.04))
            im = self.get(s, ("hook", shown), lambda: render_hook(s["items"], shown, acc))
            sc_ = 1 + 0.03 * M.clamp(lt / 4, 0, 1)
            rgb, a = im
            if abs(sc_ - 1) > 0.002:
                sz = (int(a.shape[1] * sc_), int(a.shape[0] * sc_))
                rgb, a = cv2.resize(rgb, sz), cv2.resize(a, sz)[:, :, None]
            blend(fr, (rgb, a), (W - a.shape[1]) / 2, 860 - a.shape[0] / 2, al)
        elif viz == "laptop":
            self.caption(fr, s, t, al)
            im = self.get(s, "laptop", lambda: render_laptop(acc))
            k = M.ease(lt / 0.45)
            x0, y0 = (W - 920) // 2, 640 + int((1 - k) * 60)
            blend(fr, im, x0, y0, al * k)
            if al * k > 0.5:
                self.draw_timeline(fr, x0 + 120, y0 + 60, 680, 400, t, M.clamp(lt / max(1, en - st), 0, 1))
            big = sc.get("big")
            if big:
                bi = self.get(s, "big", lambda: render_strike_word(big, 120))
                blend(fr, bi, (W - bi[1].shape[1]) / 2, 1290, al * M.ease((lt - 0.3) / 0.3))
        elif viz == "phone":
            self.caption(fr, s, t, al)
            im = self.get(s, "phone", lambda: render_phone(acc))
            k = M.ease(lt / 0.5)
            x0, y0 = (W - 520) // 2, 560 + int((1 - k) * 120)
            blend(fr, im, x0, y0, al * k)
            if al * k > 0.5:
                # экранда: «видео» блогы + субтитр жолағы + кішкене таймлайн
                cv2.rectangle(fr, (x0 + 80, y0 + 120), (x0 + 440, y0 + 560), (34, 40, 44), -1)
                c = (x0 + 260, y0 + 300)
                cv2.circle(fr, c, 60, (70, 78, 84), -1)
                cv2.ellipse(fr, (c[0], c[1] + 170), (110, 80), 0, 180, 360, (70, 78, 84), -1)
                bw = int(220 * (0.6 + 0.4 * math.sin(t * 3)))
                cv2.rectangle(fr, (c[0] - bw // 2, y0 + 470), (c[0] + bw // 2, y0 + 500),
                              tuple(int(v) for v in self.acc[::-1]), -1)
                self.draw_timeline(fr, x0 + 80, y0 + 600, 360, 220, t, M.clamp(lt / max(1, en - st), 0, 1))
        elif viz == "terminal":
            self.caption(fr, s, t, al)
            lines = sc.get("lines", [])
            im = self.get(s, "term_win", lambda: render_terminal(acc, len(lines)))
            k = M.ease(lt / 0.35)
            x0, y0 = 90, 600 + int((1 - k) * 40)
            blend(fr, im, x0, y0, al * k)
            per = min(0.6, max(0.3, (en - st - 2.2) / max(1, len(lines))))
            for i, ln in enumerate(lines):
                t0 = 0.3 + i * per
                if lt < t0:
                    break
                n = min(len(ln), int((lt - t0) * 70))
                li = self.get(s, ("ln", i, n), lambda ln=ln, n=n: render_line(ln[:n], acc))
                blend(fr, li, x0 + 36, y0 + 100 + i * 66, al)
            stk = sc.get("strike")
            if stk:
                ts = word_time(s["items"], stk.get("at", stk["word"]), st + 1.5)
                k2 = M.ease((t - ts + 0.1) / 0.25)
                wi = self.get(s, "stk", lambda: render_strike_word(stk["word"]))
                wx, wy = (W - wi[1].shape[1]) / 2, y0 + im[1].shape[0] + 60
                blend(fr, wi, wx, wy + (1 - k2) * 30, al * k2)
                k3 = M.ease((t - ts - 0.35) / 0.25)
                if k3 > 0:
                    x1 = int(wx + 20)
                    x2 = int(x1 + (wi[1].shape[1] - 40) * k3)
                    yy = int(wy + wi[1].shape[0] * 0.48)
                    cv2.line(fr, (x1, yy), (x2, yy - 12), RED[::-1], 22)
        elif viz == "list":
            self.caption(fr, s, t, al)
            items = sc.get("items", [])
            times = [word_time(s["items"], it[1], st + 0.5 + i * 0.6) for i, it in enumerate(items)]
            n = len(items)
            gap = min(128, int(820 / max(1, n)))
            top = 600 + (5 - n) * 30 if n < 5 else 600
            last = max([i for i, tt in enumerate(times) if tt <= t + 0.05] or [-1])
            for i, (it, tt) in enumerate(zip(items, times)):
                k = M.ease((t - tt + 0.08) / 0.22)
                if k <= 0:
                    continue
                act = i == last
                im = self.get(s, ("row", i, act), lambda it=it, i=i, act=act: render_row(i + 1, it[0], acc, act))
                blend(fr, im, (W - 880) / 2 + (1 - k) * 160, top + i * gap, al * k)
        elif viz == "flash":
            dark = flash > 0.5
            self.caption(fr, s, t, al, dark=dark, y=CAP_Y)
            num, unit = str(sc.get("num", "10")), sc.get("unit", "")
            k = M.ease(lt / 0.7)
            try:
                cur = str(int(round(int(num) * k)))
            except ValueError:
                cur = num
            im = self.get(s, ("fl", cur), lambda: render_flash(cur, unit))
            sc_ = 1.08 - 0.08 * M.ease(lt / 0.5)
            rgb, a = im
            sz = (int(a.shape[1] * sc_), int(a.shape[0] * sc_))
            rgb, a = cv2.resize(rgb, sz), cv2.resize(a, sz)[:, :, None]
            blend(fr, (rgb, a), (W - sz[0]) / 2, 600, al)
            # айналатын сақина
            ang = int((lt * 120) % 360)
            cv2.ellipse(fr, (W // 2, 1040), (400, 400), 0, ang, ang + 250, (10, 12, 14), 4, cv2.LINE_AA)
        elif viz == "cta":
            self.caption(fr, s, t, al)
            # орбита: ортада телефон, айналада белгішелер
            cx, cy, R = W // 2, 700, 140
            k = M.ease(lt / 0.5)
            accb = tuple(int(v) for v in self.acc[::-1])
            cv2.circle(fr, (cx, cy), R, (60, 70, 72), 2, cv2.LINE_AA)
            cv2.circle(fr, (cx, cy), 92, accb, 5, cv2.LINE_AA)
            cv2.rectangle(fr, (cx - 28, cy - 50), (cx + 28, cy + 50), WHITE, 5)
            cv2.circle(fr, (cx, cy + 36), 5, WHITE, -1)
            syms = sc.get("icons", ["CC", "3D", "Aa", "FX", "4K"])
            for i, sy in enumerate(syms):
                a_ = lt * 0.6 + i * 2 * math.pi / len(syms)
                ic = self.get(s, ("ic", i), lambda sy=sy: render_icon(sy, acc, 40))
                blend(fr, ic, cx + R * math.cos(a_) - ic[1].shape[1] / 2, cy + R * math.sin(a_) - ic[1].shape[0] / 2,
                      al * k)
            cd = self.get(s, "card", lambda: render_card(sc.get("card", ""), acc))
            blend(fr, cd, (W - cd[1].shape[1]) / 2, 900 + (1 - k) * 30, al * k)
            bt = self.get(s, "btn", lambda: render_button(sc.get("button", "Подробнее"), acc))
            pk = M.ease((lt - 0.5) / 0.3)
            pulse = 1 + 0.04 * math.sin(lt * 6.0)
            rgb, a = bt
            sz = (int(a.shape[1] * pulse), int(a.shape[0] * pulse))
            rgb, a = cv2.resize(rgb, sz), cv2.resize(a, sz)[:, :, None]
            by = 900 + cd[1].shape[0] - 20
            blend(fr, (rgb, a), (W - sz[0]) / 2, by + (bt[1].shape[0] - sz[1]) / 2, al * pk)
            ar = self.get(s, "arrow", render_arrow)
            bounce = abs(math.sin(lt * 4.0)) * 22
            blend(fr, ar, (W - 120) / 2, min(by + bt[1].shape[0] - 40, M.SAFE_B - 130) + bounce, al * pk)
        else:
            self.caption(fr, s, t, al, y=900)
        # глитч ауысу: сахна басы мен соңында
        gk = max(1 - M.clamp((t - st + 0.06) / 0.16, 0, 1) if t >= st - 0.06 else 0,
                 M.clamp((t - en) / 0.16, 0, 1) if t > en else 0)
        if gk > 0.02:
            fr = self.glitch(fr, gk, int(t * 30))
        return fr


# ---------------------------------------------------------------- дыбыс
def sfx_tech(plan, tl, path, bed=True):
    import wave
    sr = 48000
    n = int((tl["dur"] + 1) * sr)
    out = np.zeros(n, np.float32)
    rng = np.random.default_rng(11)

    def add(i, s):
        i = max(0, int(i))
        L = min(len(s), n - i)
        if L > 0:
            out[i:i + L] += s[:L]

    def blip(t, f0=1800, vol=0.12, d=0.06):
        x = np.arange(int(d * sr)) / sr
        add(t * sr, np.sign(np.sin(2 * np.pi * f0 * x)) * np.exp(-x * 60) * vol)

    def glitchs(t, vol=0.18):
        L = int(0.16 * sr)
        nz = rng.standard_normal(L)
        gate = np.repeat(rng.random(L // 400 + 1) > 0.45, 400)[:L]
        add((t - 0.05) * sr, nz * gate * np.exp(-np.arange(L) / sr * 14) * vol)

    def whoosh(t, vol=0.13):
        L = int(0.55 * sr)
        x = np.linspace(0, 1, L)
        s = np.convolve(rng.standard_normal(L), np.ones(24) / 24, "same") * np.exp(-((x - 0.6) ** 2) / 0.035)
        add((t - 0.35) * sr, s * vol * 3)

    def impact(t, vol=0.5):
        L = int(0.7 * sr)
        x = np.arange(L) / sr
        f = 120 * np.exp(-x * 6) + 38
        add(t * sr, np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-x * 5) * vol)

    def clicks(t0, t1, vol=0.05):
        t = t0
        while t < t1:
            L = int(0.012 * sr)
            add(t * sr, rng.standard_normal(L) * np.exp(-np.arange(L) / sr * 400) * vol)
            t += 0.045 + rng.random() * 0.04

    for sc, t in zip(plan["scenes"], tl["scenes"]):
        st, en = t["s"], t["e"]
        whoosh(st)
        glitchs(st)
        viz = sc.get("viz", sc.get("kind"))
        if sc.get("term"):
            clicks(st, st + len(sc["term"]) / 45.0)
        if viz == "flash":
            impact(st)
        if viz == "terminal":
            per = min(0.6, max(0.3, (en - st - 2.2) / max(1, len(sc.get("lines", [])))))
            for i, ln in enumerate(sc.get("lines", [])):
                clicks(st + 0.3 + i * per, st + 0.3 + i * per + len(ln) / 70.0, 0.04)
            if sc.get("strike"):
                ts = word_time(t["words"], sc["strike"].get("at", sc["strike"]["word"]), st + 1.5)
                impact(ts + 0.35, 0.3)
        if viz == "list":
            for i, it in enumerate(sc.get("items", [])):
                blip(word_time(t["words"], it[1], st + 0.5 + i * 0.6), 1500 + i * 150)
        if viz == "cta":
            blip(st + 0.55, 1200, 0.15, 0.1)
        for w in t["words"]:
            if w.get("a"):
                blip(w["s"], 2200, 0.06, 0.04)
    if bed:  # қараңғы фон-музыка: дрон + баяу соққы
        T = np.arange(n) / sr
        drone = (np.sin(2 * np.pi * 55 * T) * 0.5 + np.sin(2 * np.pi * 82.4 * T) * 0.25
                 + np.sin(2 * np.pi * 110 * T + np.sin(T * 0.7) * 2) * 0.15)
        drone *= 0.06 * (0.75 + 0.25 * np.sin(T * 2 * np.pi / 8))
        out += drone.astype(np.float32)
        beat = 60 / 96
        L = int(0.35 * sr)
        x = np.arange(L) / sr
        kick = np.sin(2 * np.pi * np.cumsum(90 * np.exp(-x * 20) + 45) / sr) * np.exp(-x * 9) * 0.22
        hat = rng.standard_normal(int(0.03 * sr)) * np.exp(-np.arange(int(0.03 * sr)) / sr * 160) * 0.025
        b = 0.0
        while b < tl["dur"] - 1.0:
            add(b * sr, kick)
            add((b + beat / 2) * sr, hat)
            b += beat
        fade = np.clip((tl["dur"] + 0.6 - T) / 1.5, 0, 1).astype(np.float32)
        out *= np.where(T > tl["dur"] - 1.5, fade, 1).astype(np.float32)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(out, -0.9, 0.9) * 32767).astype(np.int16).tobytes())


# ---------------------------------------------------------------- обложка
def render_cover(plan, tl, out):
    """Обложка: қара фон, неон телефон, үлкен заголовок (Reels торында 3:4 ортасына сыяды)."""
    cv = plan.get("cover", {})
    r = TechRenderer(plan, tl, None)
    acc = r.acc
    fr = r.background(2.0)
    ph = layer(render_phone(acc))
    x0, y0 = (W - 520) // 2, 700
    blend(fr, ph, x0, y0, 0.9)
    r.draw_timeline(fr, x0 + 80, y0 + 600, 360, 220, 1.0, 0.45)
    cv2.rectangle(fr, (x0 + 80, y0 + 120), (x0 + 440, y0 + 560), (34, 40, 44), -1)
    c = (x0 + 260, y0 + 300)
    cv2.circle(fr, c, 60, (70, 78, 84), -1)
    cv2.ellipse(fr, (c[0], c[1] + 170), (110, 80), 0, 180, 360, (70, 78, 84), -1)
    cv2.rectangle(fr, (c[0] - 100, y0 + 470), (c[0] + 100, y0 + 500), tuple(int(v) for v in acc[::-1]), -1)
    # заголовок
    lines = cv.get("lines", [["НОУТБУКСЫЗ", False], ["CLAUDE МОНТАЖ", True]])
    img = Image.new("RGBA", (1040, 600), (0, 0, 0, 0))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d, g = ImageDraw.Draw(img), ImageDraw.Draw(gl)
    y = 0
    for txt, hi in lines:
        px = 190
        while font(px).getlength(txt) > 980:
            px -= 6
        f = font(px)
        x = (1040 - f.getlength(txt)) / 2
        if hi:
            g.text((x, y - px * 0.08), txt, font=f, fill=acc + (255,))
        d.text((x, y - px * 0.08), txt, font=f, fill=(acc if hi else WHITE) + (255,))
        y += int(px * 1.08)
    img = img.crop((0, 0, 1040, y + 20))
    gl = gl.crop((0, 0, 1040, y + 20))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 200), (0, 0), img.split()[3])
    img = Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14)), img)
    img = Image.alpha_composite(gl.filter(ImageFilter.GaussianBlur(30)), img)
    blend(fr, layer(img), (W - 1040) / 2, 330)
    sub = cv.get("sub")
    if sub:
        f = font(64, 600)
        tw = int(f.getlength(sub)) + 80
        b = Image.new("RGBA", (tw, 100), (0, 0, 0, 0))
        bd = ImageDraw.Draw(b)
        bd.rounded_rectangle([0, 0, tw - 1, 99], 50, fill=(10, 12, 14, 230), outline=acc + (255,), width=4)
        bd.text((40, 50 - 64 * 0.68), sub, font=f, fill=WHITE + (255,))
        blend(fr, layer(b), (W - tw) / 2, 1560 - 100)
    term = cv.get("term")
    if term:
        blend(fr, layer(render_mono(term, 34, (120, 200, 180))), 90, 250)
    cv2.imwrite(out, fr, [cv2.IMWRITE_JPEG_QUALITY, 93])
    return out

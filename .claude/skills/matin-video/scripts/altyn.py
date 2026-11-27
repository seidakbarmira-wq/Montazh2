"""altyn.py — «алтын сызық» стилі: қою көк фон, алтын line-art иконкалар (сызылып шығады),
бұрыштарда ою-өрнек, жоғарыда бөлім чиптері, сөз-сөзімен шығатын субтитр + сары маркер.

Жоспарда: "look": "altyn". Сахна өрістері (matin.py-ге қосымша):
  icon   — иконка аты (ICONS ішінен), label — иконка үстіндегі шағын жазу
  chip   — қай бөлім белсенді (plan["chips"] индексі)
  bg     — "navy" (әдепкі) | "warm" | "teal" | "night"
  kind   — hook | say | features | places | cta
  items  — features/places үшін: [{"icon", "label", "word"}] (word айтылғанда иконка жанады)
"""
import math
import os
import re
import wave

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import montazh as M

W, H, FPS = M.W, M.H, M.FPS
MONO = "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Bold.ttf"
GOLD_BGR = (58, 183, 242)
RED_BGR = (80, 80, 240)
WHITE_BGR = (245, 240, 235)

_F = {}


def font(px, weight=800, mono=False):
    key = (px, weight, mono)
    if key not in _F:
        path = MONO if mono and os.path.exists(MONO) else M.FONT
        f = ImageFont.truetype(path, px)
        if not mono:
            try:
                f.set_variation_by_axes([weight])
            except Exception:
                pass
        _F[key] = f
    return _F[key]


def norm(w):
    return re.sub(r"[^\w]", "", w.lower())


# ---------------------------------------------------------------- иконкалар (бірлік координаттар 0..1)
def _arc(cx, cy, rx, ry=None, a0=0, a1=360, n=None):
    ry = rx if ry is None else ry
    n = n or max(12, int(abs(a1 - a0) / 4))
    a = np.radians(np.linspace(a0, a1, n))
    return np.stack([cx + rx * np.cos(a), cy + ry * np.sin(a)], 1)


def _rrect(x0, y0, x1, y1, r=0.03):
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        pts.append(_arc(cx, cy, r, a0=a0, a1=a0 + 90, n=8))
    p = np.concatenate(pts)
    return np.concatenate([p, p[:1]])


def _ln(*pts):
    return np.array(pts, np.float64)


def _closed(*pts):
    p = np.array(pts, np.float64)
    return np.concatenate([p, p[:1]])


def _spark(cx, cy, R, k=0.22):
    """Төрт сәулелі жұлдыз (ИИ белгісі)."""
    pts = []
    for i in range(4):
        a = math.radians(i * 90 - 90)
        b = math.radians(i * 90 - 45)
        pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
        pts.append((cx + R * k * math.cos(b), cy + R * k * math.sin(b)))
    return _closed(*pts)


def _arrow(x0, y0, x1, y1, h=0.05):
    a = math.atan2(y1 - y0, x1 - x0)
    l = (x1 - h * math.cos(a - 0.5), y1 - h * math.sin(a - 0.5))
    r = (x1 - h * math.cos(a + 0.5), y1 - h * math.sin(a + 0.5))
    return [_ln((x0, y0), (x1, y1)), _ln(l, (x1, y1), r)]


def _bezier(p0, p1, p2, n=30):
    t = np.linspace(0, 1, n)[:, None]
    p0, p1, p2 = map(np.array, (p0, p1, p2))
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t * t * p2


def icon_strokes(name):
    """[(points, color, width)] — color: g алтын, r қызыл, w ақ."""
    g, S = "g", []
    if name == "laptop_x":
        S += [(_rrect(0.2, 0.26, 0.8, 0.64, 0.025), g, 1.0), (_rrect(0.24, 0.30, 0.76, 0.60, 0.01), g, 0.5),
              (_closed((0.1, 0.68), (0.9, 0.68), (0.84, 0.75), (0.16, 0.75)), g, 1.0), (_ln((0.44, 0.715), (0.56, 0.715)), g, 0.6)]
        S += [(_arc(0.5, 0.5, 0.42, a0=-135, a1=225), "r", 1.2), (_ln((0.2, 0.2), (0.8, 0.8)), "r", 1.2)]
    elif name == "phone_ai":
        S += [(_rrect(0.3, 0.06, 0.7, 0.94, 0.06), g, 1.0), (_ln((0.45, 0.115), (0.55, 0.115)), g, 0.8),
              (_spark(0.5, 0.40, 0.15), g, 1.0), (_spark(0.615, 0.25, 0.05), g, 0.6), (_spark(0.39, 0.55, 0.035), g, 0.5),
              (_rrect(0.35, 0.68, 0.65, 0.78, 0.01), g, 0.6)]
        for x in (0.41, 0.47, 0.56):
            S.append((_ln((x, 0.68), (x, 0.78)), g, 0.4))
        S.append((_ln((0.5, 0.65), (0.5, 0.81)), "w", 0.5))
        S.append((_ln((0.44, 0.885), (0.56, 0.885)), g, 0.6))
    elif name == "link":
        S += [(_arc(0.19, 0.27, 0.14), g, 1.0), (_spark(0.19, 0.27, 0.08), g, 0.8),
              (_arc(0.81, 0.27, 0.14), g, 1.0), (_ln((0.77, 0.22), (0.73, 0.27), (0.77, 0.32)), g, 0.8),
              (_ln((0.85, 0.22), (0.89, 0.27), (0.85, 0.32)), g, 0.8), (_ln((0.83, 0.205), (0.79, 0.335)), g, 0.6),
              (_rrect(0.37, 0.235, 0.53, 0.305, 0.035), g, 0.8), (_rrect(0.47, 0.235, 0.63, 0.305, 0.035), g, 0.8),
              (_bezier((0.19, 0.42), (0.22, 0.62), (0.39, 0.66)), g, 0.6),
              (_bezier((0.81, 0.42), (0.78, 0.62), (0.61, 0.66)), g, 0.6),
              (_rrect(0.39, 0.52, 0.61, 0.96, 0.035), g, 1.0), (_ln((0.47, 0.55), (0.53, 0.55)), g, 0.6),
              (_ln((0.44, 0.75), (0.56, 0.75)), g, 0.5), (_ln((0.44, 0.80), (0.53, 0.80)), g, 0.5)]
    elif name == "subs":
        S += [(_rrect(0.1, 0.2, 0.9, 0.8, 0.04), g, 1.0), (_closed((0.45, 0.33), (0.45, 0.49), (0.58, 0.41)), g, 0.8),
              (_ln((0.24, 0.62), (0.76, 0.62)), g, 1.2), (_ln((0.33, 0.70), (0.67, 0.70)), g, 1.2)]
    elif name == "anim":
        S += [(_bezier((0.12, 0.8), (0.45, -0.05), (0.88, 0.72), 40), g, 0.5),
              (_arc(0.18, 0.72, 0.06), g, 0.8), (_arc(0.5, 0.36, 0.085), g, 1.0), (_arc(0.82, 0.62, 0.11), g, 1.0),
              (_ln((0.08, 0.88), (0.92, 0.88)), g, 0.6)]
    elif name == "fx":
        S += [(_spark(0.48, 0.5, 0.36, 0.2), g, 1.0), (_spark(0.8, 0.22, 0.1), g, 0.7), (_spark(0.2, 0.8, 0.07), g, 0.6),
              (_arc(0.48, 0.5, 0.06), g, 0.6)]
    elif name == "music":
        S += [(_ln((0.38, 0.72), (0.38, 0.24), (0.76, 0.16), (0.76, 0.64)), g, 1.0), (_ln((0.38, 0.33), (0.76, 0.25)), g, 1.0),
              (_arc(0.3, 0.73, 0.09, 0.065, -20, 340), g, 1.0), (_arc(0.68, 0.65, 0.09, 0.065, -20, 340), g, 1.0)]
    elif name == "trans":
        S += [(_rrect(0.1, 0.18, 0.56, 0.58, 0.03), g, 0.9), (_rrect(0.44, 0.38, 0.9, 0.78, 0.03), g, 0.9)]
        S += [(p, g, 0.8) for p in _arrow(0.16, 0.9, 0.84, 0.9, 0.06)]
    elif name == "house":
        S += [(_ln((0.1, 0.5), (0.5, 0.14), (0.9, 0.5)), g, 1.0), (_ln((0.2, 0.42), (0.2, 0.88), (0.8, 0.88), (0.8, 0.42)), g, 1.0),
              (_rrect(0.43, 0.62, 0.57, 0.88, 0.02), g, 0.8), (_rrect(0.26, 0.54, 0.37, 0.65, 0.01), g, 0.6),
              (_rrect(0.63, 0.54, 0.74, 0.65, 0.01), g, 0.6), (_ln((0.66, 0.3), (0.66, 0.18), (0.74, 0.18), (0.74, 0.37)), g, 0.6)]
    elif name == "road":
        S += [(_ln((0.06, 0.92), (0.44, 0.3)), g, 1.0), (_ln((0.94, 0.92), (0.56, 0.3)), g, 1.0),
              (_ln((0.0, 0.3), (1.0, 0.3)), g, 0.4), (_arc(0.5, 0.2, 0.08, a0=180, a1=360), g, 0.7)]
        for y0, y1 in ((0.34, 0.39), (0.46, 0.54), (0.63, 0.75), (0.84, 0.98)):
            S.append((_ln((0.5, y0), (0.5, y1)), g, 0.6 + y0))
    elif name == "pin":
        S += [(_arc(0.5, 0.6, 0.32), g, 1.0), (_arc(0.5, 0.6, 0.14, 0.32), g, 0.5), (_ln((0.18, 0.6), (0.82, 0.6)), g, 0.5),
              (_arc(0.5, 0.6, 0.30, 0.1, 180, 360), g, 0.4)]
        top = np.concatenate([_arc(0.5, 0.2, 0.12, a0=150, a1=390), [(0.5, 0.47)], _arc(0.5, 0.2, 0.12, a0=150, a1=151, n=2)[:1]])
        S += [(top, g, 1.1), (_arc(0.5, 0.2, 0.04), g, 0.7)]
    elif name == "lesson":
        S += [(_rrect(0.06, 0.14, 0.94, 0.72, 0.04), g, 1.0), (_closed((0.43, 0.29), (0.43, 0.53), (0.62, 0.41)), g, 1.0),
              (_ln((0.13, 0.64), (0.87, 0.64)), g, 0.5), (_ln((0.13, 0.64), (0.42, 0.64)), "w", 0.9), (_arc(0.42, 0.64, 0.02), "w", 0.9),
              (_ln((0.2, 0.95), (0.36, 0.95), (0.36, 0.88), (0.52, 0.88), (0.52, 0.81), (0.68, 0.81), (0.68, 0.76)), g, 0.8)]
        S += [(p, g, 0.8) for p in _arrow(0.72, 0.95, 0.86, 0.79, 0.04)]
    return S


def draw_icon(strokes, size, prog, prog_r=None, lw=7):
    """Сызықтарды progress бойынша сызу (draw-on). Қайтарады: uint8 BGR қабат (жарқылымен)."""
    prog_r = prog if prog_r is None else prog_r
    full = int(size * 1.3)
    off = (full - size) / 2
    lay = np.zeros((full, full, 3), np.uint8)
    pad = size * 0.04
    groups = {"main": [s for s in strokes if s[1] != "r"], "red": [s for s in strokes if s[1] == "r"]}
    for gname, items in groups.items():
        p = prog if gname == "main" else prog_r
        if p <= 0 or not items:
            continue
        lens = [np.sum(np.linalg.norm(np.diff(s[0], axis=0), axis=1)) for s in items]
        total = sum(lens)
        budget = total * min(1.0, p)
        for (pts, col, wsc), L in zip(items, lens):
            if budget <= 0:
                break
            q = pts * (size - 2 * pad) + pad + off
            if budget < L:
                cum = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
                k = int(np.searchsorted(cum, budget))
                k = max(1, min(k, len(pts) - 1))
                u = (budget - cum[k - 1]) / max(1e-9, cum[k] - cum[k - 1])
                tip = q[k - 1] + (q[k] - q[k - 1]) * u
                q = np.concatenate([q[:k], tip[None]])
            budget -= L
            c = {"g": GOLD_BGR, "r": RED_BGR, "w": WHITE_BGR}[col]
            cv2.polylines(lay, [np.round(q * 4).astype(np.int32)], False, c, max(2, int(lw * wsc)), cv2.LINE_AA, shift=2)
    glow = cv2.GaussianBlur(lay, (0, 0), size / 40)
    glow2 = cv2.GaussianBlur(lay, (0, 0), size / 12)
    return cv2.add(cv2.add(lay, cv2.convertScaleAbs(glow, alpha=1.3)), cv2.convertScaleAbs(glow2, alpha=0.7))


# ---------------------------------------------------------------- мәтін
def text_block(items, shown, px, maxw, hl=(), align="center", hide_future=True, weight=800):
    f = font(px, weight)
    sp = f.getlength(" ")
    words = [it["w"] for it in items]
    ws = [f.getlength(w) for w in words]
    lines, cur, cw = [], [], 0
    for i, w in enumerate(ws):
        if cur and cw + sp + w > maxw:
            lines.append(cur)
            cur, cw = [], 0
        cur.append(i)
        cw += (sp if len(cur) > 1 else 0) + w
    if cur:
        lines.append(cur)
    # жолдарды теңестіру: соңғы жолда 1 сөз қалмасын
    if len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) >= 3:
        lines[-1].insert(0, lines[-2].pop())
    lh = int(px * 1.22)
    cwid = maxw + 60
    img = Image.new("RGBA", (cwid, lh * len(lines) + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = 20
    for ln in lines:
        tw = sum(ws[i] for i in ln) + sp * (len(ln) - 1)
        x = (cwid - tw) / 2 if align == "center" else 30
        for i in ln:
            on = i < shown
            if on or not hide_future:
                a = 255 if on else 70
                if items[i].get("a") or i in hl:
                    d.rounded_rectangle([x - 10, y + px * 0.12, x + ws[i] + 10, y + px * 1.2], 10,
                                        fill=(242, 183, 58, a))
                    d.text((x, y), words[i], font=f, fill=(18, 16, 24, a))
                else:
                    d.text((x, y), words[i], font=f, fill=(255, 255, 255, a))
            x += ws[i] + sp
        y += lh
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 170), (0, 0), img.split()[3])
    return Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(8)), img)


def mono_label(text, px=30, col=(242, 183, 58), spacing=0.18, a=255):
    f = font(px, mono=True)
    tw = sum(f.getlength(c) for c in text) + px * spacing * (len(text) - 1)
    img = Image.new("RGBA", (int(tw) + 20, int(px * 1.5)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 10
    for c in text:
        d.text((x, 2), c, font=f, fill=col + (a,))
        x += f.getlength(c) + px * spacing
    return img


def to_rgba(im):
    arr = np.asarray(im, dtype=np.float32)
    return arr[:, :, :3][:, :, ::-1].copy(), arr[:, :, 3:] / 255.0


def chips_img(chips, active):
    f = font(22, mono=True)
    hgt, padx, gap = 40, 16, 10
    parts = []
    for i, c in enumerate(chips):
        t = "%02d %s" % (i + 1, c)
        parts.append((t, f.getlength(t) + 2 * padx))
    tot = sum(w for _, w in parts) + gap * (len(parts) - 1)
    img = Image.new("RGBA", (int(tot) + 8, hgt + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 4
    for i, (t, w) in enumerate(parts):
        box = [x, 4, x + w, 4 + hgt]
        if i == active:
            d.rounded_rectangle(box, hgt // 2, fill=(242, 183, 58, 255))
            d.text((x + padx, 11), t, font=f, fill=(18, 16, 24, 255))
        elif i < active:
            d.rounded_rectangle(box, hgt // 2, outline=(242, 183, 58, 200), width=2)
            d.text((x + padx, 11), t, font=f, fill=(242, 183, 58, 220))
        else:
            d.rounded_rectangle(box, hgt // 2, outline=(255, 255, 255, 70), width=2)
            d.text((x + padx, 11), t, font=f, fill=(255, 255, 255, 90))
        x += w + gap
    return img


def button_img(label):
    f = font(64, 900)
    tw = f.getlength(label)
    bw, bh = int(tw + 140), 130
    img = Image.new("RGBA", (bw + 100, bh + 100), (0, 0, 0, 0))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(gl).rounded_rectangle([50, 50, 50 + bw, 50 + bh], 65, fill=(242, 183, 58, 200))
    img = Image.alpha_composite(img, gl.filter(ImageFilter.GaussianBlur(26)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([50, 50, 50 + bw, 50 + bh], 65, fill=(242, 183, 58, 255))
    d.text((50 + (bw - tw) / 2, 50 + (bh - 64 * 1.25) / 2), label, font=f, fill=(18, 16, 24, 255))
    return img


# ---------------------------------------------------------------- ою-өрнек рамка
def ornament_layer():
    lay = np.zeros((H, W, 3), np.uint8)
    c = tuple(int(v * 0.42) for v in GOLD_BGR)
    m = 26
    cv2.rectangle(lay, (m, m), (W - m, H - m), c, 2, cv2.LINE_AA)
    cv2.rectangle(lay, (m + 10, m + 10), (W - m - 10, H - m - 10), tuple(int(v * 0.4) for v in c), 1, cv2.LINE_AA)

    def horn(cx, cy, sx, sy):
        # қошқар мүйіз: екі спираль
        for mir in (1, -1):
            th = np.linspace(0, 3.2 * math.pi, 120)
            r = 34 * np.exp(-0.16 * th)
            x = cx + sx * mir * (r * np.cos(th) + 30)
            y = cy + sy * (-r * np.sin(th) + 8)
            pts = np.stack([x, y], 1)
            cv2.polylines(lay, [np.round(pts * 4).astype(np.int32)], False, c, 2, cv2.LINE_AA, shift=2)
        cv2.line(lay, (int(cx), int(cy)), (int(cx), int(cy + sy * 50)), c, 2, cv2.LINE_AA)
    for cx, cy, sx, sy in ((m + 70, m + 62, 1, 1), (W - m - 70, m + 62, -1, 1), (m + 70, H - m - 62, 1, -1),
                           (W - m - 70, H - m - 62, -1, -1)):
        horn(cx, cy, sx * 0.9, sy * 0.9)
    return cv2.GaussianBlur(lay, (0, 0), 0.6)


# ---------------------------------------------------------------- фон
BGS = {
    "navy": ((10, 22, 52), (2, 6, 18), (60, 110, 200)),
    "warm": ((48, 20, 8), (12, 5, 3), (230, 110, 30)),
    "teal": ((18, 110, 125), (4, 40, 50), (120, 230, 235)),
    "night": ((8, 10, 32), (60, 40, 85), (245, 160, 80)),
}


class Renderer:
    def __init__(self, plan, tl):
        self.plan, self.tl = plan, tl
        self.scenes = []
        chips = plan.get("chips", [])
        self.chips = chips
        for sc, t in zip(plan["scenes"], tl["scenes"]):
            s = {"sc": sc, "tl": t, "kind": sc.get("kind", "say"), "items": t["words"], "cache": {}}
            hl = set()
            for it in sc.get("items", []):
                key = norm(it["word"])[:5]
                for i, w in enumerate(t["words"]):
                    if norm(w["w"]).startswith(key):
                        it["_t"] = w["s"]
                        hl.add(i)
                        break
                else:
                    it["_t"] = t["s"] + 0.5
            s["hl"] = hl
            s["chunks"] = M.chunk_words([dict(w, _i=i) for i, w in enumerate(t["words"])], 6, 30)
            kws = [w["s"] for w in t["words"] if w.get("a")]
            s["kw_t"] = kws[0] if kws else t["s"] + 1.0
            self.scenes.append(s)
        self.speech_end = tl["scenes"][-1]["speech_e"]
        self.orn = ornament_layer()
        self.sw, self.sh = 216, 384
        gy, gx = np.mgrid[0:self.sh, 0:self.sw].astype(np.float32)
        self.gx, self.gy = gx / self.sw, gy / self.sh
        rng = np.random.default_rng(7)
        self.dust = rng.random((70, 4))
        self.stars = rng.random((140, 3))
        self.grain = np.repeat(np.repeat((rng.standard_normal((H // 2, W // 2)) * 4).astype(np.float32), 2, 0), 2, 1)[:, :, None]
        self.icon_final = {}
        self.wm = to_rgba(mono_label(plan["watermark"], 22, (255, 255, 255), 0.05, 120)) if plan.get("watermark") else None
        self._bgcache = {}

    # ---- фон
    def bg_small(self, kind, t):
        top, bot, glow = [np.array(c[::-1], np.float32) for c in BGS.get(kind, BGS["navy"])]
        gx, gy = self.gx, self.gy
        if kind == "night":
            yy = gy[:, :, None]
            hor = 0.78
            img = top * (1 - np.clip(yy / hor, 0, 1)[..., :1]) + bot * np.clip(yy / hor, 0, 1)
            g = np.exp(-((yy - hor) ** 2) / 0.003) * 0.9
            img = img * (1 - g) + glow * g
            hills = 0.80 + 0.025 * np.sin(gx * 7 + 1) + 0.015 * np.sin(gx * 17)
            hm = np.clip((gy - hills) * self.sh / 1.5, 0, 1)[:, :, None]
            img = img * (1 - hm) + np.array([22, 12, 8], np.float32) * hm
            return img
        yy = gy[:, :, None]
        img = top * (1 - yy) + bot * yy
        cx, cy = 0.5 + 0.06 * math.sin(t * 0.4), 0.42 + 0.04 * math.cos(t * 0.33)
        d2 = (gx - cx) ** 2 + ((gy - cy) * 1.6) ** 2
        k = 0.55 if kind != "teal" else 0.85
        g = (np.exp(-d2 / 0.09) * k)[:, :, None]
        img = img * (1 - g) + glow * g
        if kind == "teal":  # айналатын сәулелер
            ang = np.arctan2(gy - 0.42, (gx - 0.5) * 0.56) + t * 0.12
            rays = (np.cos(ang * 18) > 0.55).astype(np.float32) * np.clip(0.9 - np.sqrt(d2) * 1.2, 0, 1) * 0.22
            img = img * (1 - rays[:, :, None]) + np.array([235, 250, 250], np.float32) * rays[:, :, None]
        vig = np.clip(1.15 - np.sqrt((gx - 0.5) ** 2 + (gy - 0.5) ** 2) * 1.1, 0.35, 1)[:, :, None]
        return img * vig

    def background(self, t):
        cur = 0
        for i, s in enumerate(self.scenes):
            if t >= s["tl"]["s"] - 0.3:
                cur = i
        kind = self.scenes[cur]["sc"].get("bg", "navy")
        img = self.bg_small(kind, t)
        st = self.scenes[cur]["tl"]["s"]
        if cur > 0:
            pk = self.scenes[cur - 1]["sc"].get("bg", "navy")
            if pk != kind and t < st + 0.3:
                k = M.ease((t - (st - 0.3)) / 0.6)
                img = self.bg_small(pk, t) * (1 - k) + img * k
        big = cv2.resize(img, (W, H), interpolation=cv2.INTER_CUBIC)
        fr = np.clip(big + self.grain, 0, 255).astype(np.uint8)
        night = kind == "night"
        if night:
            for x, y, ph in self.stars:
                if y < 0.7:
                    b = 0.5 + 0.5 * math.sin(t * 2.2 + ph * 20)
                    cv2.circle(fr, (int(x * W), int(y * H * 0.95)), 2, (int(200 + 55 * b),) * 3, -1, cv2.LINE_AA)
            # ай
            cv2.circle(fr, (860, 330), 46, (210, 235, 250), -1, cv2.LINE_AA)
            cv2.circle(fr, (884, 314), 44, tuple(int(v) for v in img[int(314 / H * self.sh), int(884 / W * self.sw)]),
                       -1, cv2.LINE_AA)
        else:
            for x, y, sp, ph in self.dust:
                yy = (y - t * 0.012 * (0.5 + sp)) % 1.0
                xx = x + 0.01 * math.sin(t * 0.7 + ph * 9)
                b = 0.4 + 0.6 * abs(math.sin(t * 1.3 + ph * 11))
                c = tuple(int(v * 0.55 * b) for v in GOLD_BGR)
                cv2.circle(fr, (int(xx * W), int(yy * H)), 2 if sp < 0.7 else 3, c, -1, cv2.LINE_AA)
        return cv2.add(fr, self.orn)

    # ---- көмекшілер
    def blend(self, fr, rgba, x, y, alpha=1.0):
        rgb, a = rgba
        h, w = a.shape[:2]
        x, y = int(x), int(y)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0 or alpha <= 0.003:
            return
        sa = a[y0 - y:y1 - y, x0 - x:x1 - x] * alpha
        reg = fr[y0:y1, x0:x1].astype(np.float32)
        fr[y0:y1, x0:x1] = (reg * (1 - sa) + rgb[y0 - y:y1 - y, x0 - x:x1 - x] * sa).astype(np.uint8)

    def add(self, fr, lay, cx, cy, alpha=1.0):
        h, w = lay.shape[:2]
        x, y = int(cx - w / 2), int(cy - h / 2)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0 or alpha <= 0.003:
            return
        src = lay[y0 - y:y1 - y, x0 - x:x1 - x]
        if alpha < 0.999:
            src = cv2.convertScaleAbs(src, alpha=alpha)
        fr[y0:y1, x0:x1] = cv2.add(fr[y0:y1, x0:x1], src)

    def icon(self, name, size, t0, t, dur=1.1, t_red=None, dim=False):
        p = M.clamp((t - t0) / dur, 0, 1)
        pr = M.clamp((t - t_red) / 0.45, 0, 1) if t_red is not None else p
        p = M.ease(p) if p < 1 else 1.0
        pr = M.ease(pr) if pr < 1 else 1.0
        key = (name, size, dim)
        if p >= 1 and pr >= 1 and key in self.icon_final:
            return self.icon_final[key]
        if dim:
            lay = draw_icon(icon_strokes(name), size, 1.0, 1.0, lw=max(4, size // 70))
            lay = cv2.convertScaleAbs(cv2.cvtColor(cv2.cvtColor(lay, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR), alpha=0.22)
            self.icon_final[key] = lay
            return lay
        lay = draw_icon(icon_strokes(name), size, p, pr, lw=max(5, size // 70))
        if p >= 1 and pr >= 1:
            self.icon_final[key] = lay
        return lay

    def punch(self, s, t):
        ks = [w["s"] for i, w in enumerate(s["items"]) if (w.get("a") or i in s["hl"]) and w["s"] <= t]
        if not ks:
            return 1.0, None
        return 1 + 0.09 * math.exp(-(t - ks[-1]) * 7), t - ks[-1]

    def add_scaled(self, fr, lay, cx, cy, alpha, sc=1.0):
        if abs(sc - 1) > 0.004:
            lay = cv2.resize(lay, None, fx=sc, fy=sc, interpolation=cv2.INTER_LINEAR)
        self.add(fr, lay, cx, cy, alpha)

    def ring(self, fr, cx, cy, r0, since, col=GOLD_BGR, dur=0.9):
        """Кеңейіп сөнетін толқын."""
        if since is None or since < 0 or since > dur:
            return
        u = since / dur
        r = int(r0 * (0.55 + 0.75 * u))
        a = (1 - u) ** 1.6
        sz = 2 * r + 40
        lay = np.zeros((sz, sz, 3), np.uint8)
        c = tuple(int(v * a) for v in col)
        cv2.circle(lay, (sz // 2, sz // 2), r, c, 4, cv2.LINE_AA)
        lay = cv2.add(lay, cv2.GaussianBlur(lay, (0, 0), 6))
        self.add(fr, lay, cx, cy, 1.0)

    def orbit(self, fr, cx, cy, R, t, alpha, n=3):
        """Иконка айналасында ұшқындар айналады."""
        if "spark" not in self.icon_final:
            self.icon_final["spark"] = draw_icon([(_spark(0.5, 0.5, 0.45), "g", 1.0)], 64, 1.0, lw=4)
        sp = self.icon_final["spark"]
        for k in range(n):
            a = t * 0.7 + k * 2 * math.pi / n
            tw = 0.55 + 0.45 * math.sin(t * 3 + k * 2)
            self.add(fr, sp, cx + R * math.cos(a), cy + R * 0.42 * math.sin(a), alpha * tw)

    def cached(self, s, key, maker):
        if key not in s["cache"]:
            s["cache"][key] = to_rgba(maker())
        return s["cache"][key]

    def subtitles(self, fr, s, t, cy, al):
        for ci, ch in enumerate(s["chunks"]):
            nxt = s["chunks"][ci + 1]["s"] if ci + 1 < len(s["chunks"]) else 1e9
            if ch["s"] - 0.05 <= t < min(ch["e"] + 0.25, nxt - 0.03):
                its = ch["items"]
                shown = max(1, sum(1 for w in its if w["s"] <= t + 0.03))
                hl = {k for k, w in enumerate(its) if w["_i"] in s["hl"]}
                im = self.cached(s, ("sub", ci, shown), lambda: text_block(its, shown, 60, 860, hl))
                k2 = M.ease((t - ch["s"] + 0.05) / 0.15)
                self.blend(fr, im, (W - im[1].shape[1]) / 2, cy - im[1].shape[0] / 2 + (1 - k2) * 12, al)

    # ---- кадр
    def frame(self, t):
        fr = self.background(t)
        cur = None
        for s in self.scenes:
            if s["tl"]["s"] - 0.2 <= t <= s["tl"]["e"] + 0.25:
                cur = s
        if cur is None:
            for s in self.scenes:
                if t > s["tl"]["e"]:
                    cur = s
            if cur is None or t > self.tl["dur"]:
                return fr
        s = cur
        sc, st, en = s["sc"], s["tl"]["s"], s["tl"]["e"]
        last = s is self.scenes[-1]
        fin = M.ease((t - st + 0.2) / 0.35)
        fout = 1.0 if last else 1 - M.ease((t - en) / 0.25)
        al = fin * fout
        dy = (1 - fin) * 40
        # чиптер
        if self.chips:
            ci = sc.get("chip", 0)
            im = self.cached(s, "chips", lambda: chips_img(self.chips, ci))
            self.blend(fr, im, M.SAFE_L - 4, 236, 1.0)
        if self.wm:
            self.blend(fr, self.wm, 60, 1530, 1.0)
        kind = s["kind"]
        if kind == "hook":
            shown = max(1, sum(1 for w in s["items"] if w["s"] <= t + 0.03))
            im = self.cached(s, ("hook", shown), lambda: text_block(s["items"], shown, 84, 880))
            self.blend(fr, im, (W - im[1].shape[1]) / 2, 330 + dy, al)
            top = 330 + im[1].shape[0]
            if sc.get("label"):
                lb = self.cached(s, "label", lambda: mono_label(sc["label"], 30, (255, 110, 100) if sc["label"].startswith("✕") else (242, 183, 58)))
                self.blend(fr, lb, (W - lb[1].shape[1]) / 2, top + 30, al * M.ease((t - s["kw_t"]) / 0.3))
            lay = self.icon(sc["icon"], 520, st + 0.1, t, 1.2, t_red=s["kw_t"])
            pk, since = self.punch(s, t)
            fl = 8 * math.sin(t * 2.2)
            self.add_scaled(fr, lay, W / 2, top + 330 + dy + fl, al, pk)
            self.ring(fr, W / 2, top + 330, 330, t - s["kw_t"], RED_BGR, 0.8)
            self.ring(fr, W / 2, top + 330, 260, t - s["kw_t"] - 0.15, RED_BGR, 0.8)
        elif kind == "say":
            if sc.get("label"):
                lb = self.cached(s, "label", lambda: mono_label(sc["label"], 30))
                self.blend(fr, lb, (W - lb[1].shape[1]) / 2, 430 + dy, al)
                cv2.line(fr, (int(W / 2 - 60), 485 + int(dy)), (int(W / 2 + 60), 485 + int(dy)),
                         tuple(int(v * 0.6 * al) for v in GOLD_BGR), 2, cv2.LINE_AA)
            lay = self.icon(sc["icon"], 560, st + 0.05, t, 1.3)
            breathe = 0.85 + 0.15 * math.sin((t - st) * 2.4) if t - st > 1.3 else 1.0
            pk, since = self.punch(s, t)
            fl = 10 * math.sin((t - st) * 2.0)
            self.add_scaled(fr, lay, W / 2, 800 + dy + fl, al * breathe, pk)
            self.ring(fr, W / 2, 800, 380, since)
            if t - st > 1.0:
                self.orbit(fr, W / 2, 800, 360, t, al * M.ease((t - st - 1.0) / 0.5))
            if sc["icon"] == "link" and t - st > 1.2:  # мәлімет ағыны
                for k in range(6):
                    u = ((t - st) * 0.6 + k / 6) % 1.0
                    for p0, p1, p2 in (((0.19, 0.42), (0.22, 0.62), (0.39, 0.66)), ((0.81, 0.42), (0.78, 0.62), (0.61, 0.66))):
                        x = (1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0]
                        y = (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1]
                        px = 540 - 280 + (x * 0.92 + 0.04) * 560
                        py = 800 - 280 + (y * 0.92 + 0.04) * 560 + dy
                        cv2.circle(fr, (int(px), int(py)), 6, (200, 240, 255), -1, cv2.LINE_AA)
            self.subtitles(fr, s, t, 1270, al)
        elif kind in ("features", "places"):
            its = sc["items"]
            if kind == "features":
                size = 220
                pos = [(230, 620), (540, 620), (850, 620), (385, 930), (695, 930)]
            else:
                size = 270
                pos = [(215, 760), (540, 760), (865, 760)]
            for it, (x, y) in zip(its, pos):
                on = t >= it["_t"] - 0.1
                y2 = y + dy
                if not on:
                    self.add(fr, self.icon(it["icon"], size, 0, 0, dim=True), x, y2, al)
                    lab = self.cached(s, ("lab0", it["label"]), lambda it=it: mono_label(it["label"], 24, (255, 255, 255), 0.1, 80))
                else:
                    pop = M.ease((t - it["_t"] + 0.1) / 0.3)
                    bnc = 1 + 0.16 * math.exp(-(t - it["_t"]) * 6) * math.cos((t - it["_t"]) * 14) if t > it["_t"] else 1.0
                    fl = 6 * math.sin(t * 2.4 + x * 0.01)
                    self.add_scaled(fr, self.icon(it["icon"], size, it["_t"] - 0.1, t, 0.6), x, y2 - (1 - pop) * 20 + fl, al, bnc)
                    self.ring(fr, x, y2, size * 0.75, t - it["_t"])
                    lab = self.cached(s, ("lab1", it["label"]), lambda it=it: mono_label(it["label"], 24, (242, 183, 58), 0.1))
                lw_ = lab[1].shape[1]
                if lw_ > size + 10 and " " in it["label"]:  # ұзын жазу — екі жол
                    a, b = it["label"].rsplit(" ", 1)
                    for j, part in enumerate((a, b)):
                        l2 = self.cached(s, ("lab", on, part), lambda part=part, on=on: mono_label(
                            part, 24, (242, 183, 58) if on else (255, 255, 255), 0.1, 255 if on else 80))
                        self.blend(fr, l2, x - l2[1].shape[1] / 2, y2 + size / 2 + 8 + j * 36, al)
                else:
                    self.blend(fr, lab, x - lw_ / 2, y2 + size / 2 + 8, al)
            self.subtitles(fr, s, t, 1270, al)
        elif kind == "cta":
            card = sc.get("card") or sc["text"]
            if "card_items" not in s:
                s["card_items"] = []
                for m_ in re.finditer(r"\*([^*]+)\*|(\S+)", card):
                    if m_.group(1):
                        s["card_items"] += [{"w": w, "a": True} for w in m_.group(1).split()]
                    else:
                        s["card_items"].append({"w": m_.group(2), "a": False})
            n = len(s["card_items"])
            shown = n if t - st > 0.9 else max(1, int(n * (t - st + 0.1) / 0.9))
            im = self.cached(s, ("card", shown), lambda: text_block(s["card_items"], shown, 86, 860))
            self.blend(fr, im, (W - im[1].shape[1]) / 2, 360 + dy, al)
            bt = self.cached(s, "btn", lambda: button_img(sc.get("button", "Подробнее")))
            pk = M.ease((t - st - 0.8) / 0.35)
            pulse = 1 + 0.04 * math.sin((t - st) * 5.5)
            rgb, a = bt
            sz = (int(a.shape[1] * pulse), int(a.shape[0] * pulse))
            rgb2, a2 = cv2.resize(rgb, sz), cv2.resize(a, sz)[:, :, None]
            by = 880
            if t - st > 1.0:
                self.ring(fr, W / 2, by, 300, (t - st - 1.0) % 1.3, GOLD_BGR, 1.1)
            self.blend(fr, (rgb2, a2), (W - sz[0]) / 2, by - sz[1] / 2, al * pk)
            bounce = abs(math.sin((t - st) * 4.0)) * 20
            ar = draw_icon([(p, "g", 1.0) for p in _arrow(0.5, 0.1, 0.5, 0.9, 0.3)], 120, 1.0, lw=9)
            self.add(fr, ar, W / 2, by + 150 + bounce, al * pk)
            self.subtitles(fr, s, t, 1270, al)
        return self.fx(fr, t)

    def fx(self, fr, t):
        """Хук эффектілері: басында және маңызды сөзде жарқыл + сілкініс."""
        h0 = self.scenes[0]
        if h0["kind"] != "hook":
            return fr
        for t0, amp, fl in ((0.0, 0, 0.55), (h0["kw_t"], 20, 0.45)):
            d = t - t0
            if 0 <= d < 0.6:
                f = fl * math.exp(-d * 9)
                if f > 0.01:
                    fr = cv2.addWeighted(fr, 1 - f, np.full_like(fr, 255), f, 0)
                if amp:
                    e = amp * math.exp(-d * 6)
                    mt = np.float32([[1, 0, e * math.sin(d * 75)], [0, 1, e * 0.6 * math.cos(d * 60)]])
                    fr = cv2.warpAffine(fr, mt, (W, H), borderMode=cv2.BORDER_REFLECT)
        return fr


# ---------------------------------------------------------------- фон музыкасы (жұмсақ pad)
def make_pad(dur, path):
    sr = 48000
    n = int(dur * sr)
    t = np.arange(n) / sr
    out = np.zeros(n, np.float32)
    # Am – F – C – G, әр аккорд 4 сек
    chords = [(220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (130.81, 196.0, 261.63), (196.0, 246.94, 293.66)]
    seg = 4.0
    for k in range(int(dur / seg) + 1):
        f3 = chords[k % 4]
        i0 = int(k * seg * sr)
        i1 = min(n, int((k + 1) * seg * sr + 1.0 * sr))
        if i0 >= n:
            break
        tt = t[i0:i1] - k * seg
        env = np.clip(tt / 0.8, 0, 1) * np.clip((seg + 1.0 - tt) / 1.0, 0, 1)
        s = np.zeros_like(tt)
        for f in f3:
            for det in (-0.6, 0.6):
                s += np.sin(2 * np.pi * (f + det) * tt) * 0.5 + np.sin(2 * np.pi * (f * 2 + det) * tt) * 0.12
        s += np.sin(2 * np.pi * f3[0] / 2 * tt) * 0.6
        out[i0:i1] += (s * env).astype(np.float32)
    # жұмсақ ырғақ (kick)
    for b in np.arange(0, dur, 0.5):
        i = int(b * sr)
        L = min(int(0.25 * sr), n - i)
        if L <= 0:
            break
        x = np.arange(L) / sr
        out[i:i + L] += (np.sin(2 * np.pi * (55 + 60 * np.exp(-x * 30)) * x) * np.exp(-x * 14) * 1.2).astype(np.float32)
    fade = np.clip((dur - t) / 2.0, 0, 1) * np.clip(t / 1.0, 0, 1)
    out = out * fade / (np.abs(out).max() + 1e-6) * 0.35
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((out * 32767).astype(np.int16).tobytes())

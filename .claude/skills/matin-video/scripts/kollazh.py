"""kollazh.py — «газет-коллаж» стилі (theme.style = "kollazh").

Фон: газет қиындылары. Әр сахна — жаңа жыртық қағаз парақ төменнен шығады.
Парақта: тақырып (ақ скотч-жазу `label` немесе қиылған әріптер `ransom`),
ортада сурет (`art`), қолжазба жазба (`note`), төменде қағаз жолақта субтитр.

art түрлері:
  {"type": "phone"}                                   — телефон + айналасында «?»
  {"type": "cards", "items": [{"icon","t","s","at"}], "join": "+"|"→"}  — 1–4 карточка
  {"type": "link",  "items": [card, card]}            — екі карточка сыммен жалғанады
  {"type": "folder", "t": "ЖОБА"}                     — үлкен папка, ішінде парақтар
  {"type": "fork", "a": "ЖОБА", "b": "СІЗДІКІ"}       — папка көшірмесі
  {"type": "stamp", "icon": "code", "t": "КОД", "stamp": "КЕРЕК ЕМЕС"}
  {"type": "button", "t": "Подробнее"}                — батырма + көрсеткі
icon: code, chat, clapper, folder, robot, browser, app, phone, star
"at": сол сөз айтылғанда карточка шығады (сөздің басы жеткілікті).
"""
import math, os, random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import montazh as M

HERE = os.path.dirname(os.path.abspath(__file__))
FD = os.path.join(os.path.dirname(HERE), "fonts")
W, H = M.W, M.H
INK = (34, 32, 30)
RED = (182, 40, 34)
BROWN = (176, 128, 84)
ORANGE = (204, 132, 78)
PAPERS = [(233, 226, 208), (205, 219, 226), (240, 238, 232)]
SHEET = (60, 232, 960, 1290)  # x, y, w, h
_F = {}


def font(name, px, wght=None):
    k = (name, px, wght)
    if k not in _F:
        f = ImageFont.truetype(os.path.join(FD, name + ".ttf"), int(px))
        if wght:
            try:
                f.set_variation_by_axes([wght])
            except Exception:
                pass
        _F[k] = f
    return _F[k]


def oswald(px, w=700):
    return font("Oswald", px, w)


def hand(px, w=700):
    return font("Caveat", px, w)


def ease(x):
    return M.ease(x)


def back(x):  # серпімді шығу
    x = M.clamp(x, 0, 1)
    c = 1.9
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


# ---------------------------------------------------------------- қағаз
def noise2(h, w, cell, rng):
    import cv2
    a = rng.standard_normal((max(2, h // cell + 2), max(2, w // cell + 2))).astype(np.float32)
    return cv2.resize(a, (w, h), interpolation=cv2.INTER_CUBIC)


def torn_mask(w, h, rng, amp=5.0):
    """Шеттері сәл жыртық тікбұрыш маскасы."""
    m = np.zeros((h, w), np.float32)
    pad = int(amp * 2 + 2)
    def edge(n):
        e = np.cumsum(rng.standard_normal(n)) * 0.35
        e = e - np.linspace(e[0], e[-1], n)
        return np.clip(e + rng.standard_normal(n) * 0.9, -amp, amp) + amp
    top, bot = edge(w), edge(w)
    lef, rig = edge(h), edge(h)
    yy = np.arange(h)[:, None]
    xx = np.arange(w)[None, :]
    m = ((yy >= top[None, :]) & (yy <= h - 1 - bot[None, :]) &
         (xx >= lef[:, None]) & (xx <= w - 1 - rig[:, None])).astype(np.float32)
    return m


def paper(w, h, color, seed, amp=5.0, shadow=True, grain=6.0):
    """Қағаз RGBA (көлеңкесімен). Қайтарады PIL Image, ығысу (pad)."""
    rng = np.random.default_rng(seed)
    pad = 36 if shadow else 0
    base = np.ones((h, w, 3), np.float32) * np.array(color, np.float32)
    mott = noise2(h, w, 70, rng) * 7 + noise2(h, w, 18, rng) * 3 + rng.standard_normal((h, w)) * grain * 0.3
    base += mott[:, :, None]
    # бүктелген іздер
    if w > 500:
        for fx in (w // 2,):
            base[:, fx - 2:fx + 2] -= 4
        for fy in (h // 3, 2 * h // 3):
            base[fy - 2:fy + 2, :] -= 4
    mask = torn_mask(w, h, rng, amp)
    out = np.zeros((h + pad * 2, w + pad * 2, 4), np.float32)
    if shadow:
        import cv2
        sm = np.zeros(out.shape[:2], np.float32)
        sm[pad + 8:pad + 8 + h, pad + 4:pad + 4 + w] = mask
        sm = cv2.GaussianBlur(sm, (0, 0), 12) * 0.55
        out[:, :, 3] = sm * 255
    reg = out[pad:pad + h, pad:pad + w]
    a = mask[:, :, None]
    reg[:, :, :3] = base * a + reg[:, :, :3] * (1 - a)
    reg[:, :, 3] = np.maximum(reg[:, :, 3], mask * 255)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA"), pad


def rotate(img, deg):
    return img.rotate(deg, resample=Image.BICUBIC, expand=True)


# ---------------------------------------------------------------- газет фон
WORDS = ("жаңа өмір қала тарих ғылым білім жастар еңбек ел ауыл мектеп мәдениет өнер спорт жоба "
         "бүгін ертең күн жыл адам қоғам кітап ұстаз дала тау өзен әлем жұмыс бағдарлама "
         "жиналыс ақпарат туралы бойынша жаңалық басқарма").split()
HEADS = ["ҒЫЛЫМ", "СПОРТ", "ӨНЕР ЖӘНЕ", "ТЕХНИКА", "ӨМІР", "ҚАЛА ТЫНЫСЫ", "РАСЫ", "ЖАҢАЛЫҚ", "МӘДЕНИЕТ"]


def newspaper_bg(path):
    if os.path.exists(path):
        return Image.open(path).convert("RGB")
    rng = random.Random(11)
    BW, BH = W + 160, H + 160
    img = Image.new("RGB", (BW, BH), (150, 140, 122))
    ft = font("PTSerifBold", 15)
    fh = font("PTSerifBold", 30)
    for k in range(70):
        w, h = rng.randint(220, 420), rng.randint(200, 420)
        tone = rng.randint(196, 222)
        cl = Image.new("RGBA", (w, h), (tone, tone - 6, tone - 22, 255))
        d = ImageDraw.Draw(cl)
        y = 12
        if rng.random() < 0.5:
            d.text((12, y), rng.choice(HEADS), font=fh, fill=(40, 38, 34, 255))
            y += 42
        cols = 2 if w > 300 else 1
        cw = (w - 24 - 10 * (cols - 1)) // cols
        photo = rng.random() < 0.35
        for c in range(cols):
            x0 = 12 + c * (cw + 10)
            yy = y
            if photo and c == 0:
                ph = min(h - yy - 20, rng.randint(90, 150))
                d.rectangle([x0, yy, x0 + cw, yy + ph], fill=(118, 112, 102, 255))
                cx, cy = x0 + cw // 2, yy + ph // 2
                d.ellipse([cx - 16, cy - 30, cx + 16, cy + 2], fill=(70, 66, 60, 255))
                d.rectangle([cx - 28, cy + 6, cx + 28, yy + ph], fill=(70, 66, 60, 255))
                yy += ph + 8
            while yy < h - 18:
                line = ""
                while ft.getlength(line + " w") < cw - 10:
                    line += " " + rng.choice(WORDS)
                d.text((x0, yy), line.strip(), font=ft, fill=(72, 68, 62, 255))
                yy += 18
        cl = cl.rotate(rng.uniform(-6, 6), resample=Image.BICUBIC, expand=True)
        sh = Image.new("RGBA", cl.size, (0, 0, 0, 0))
        sh.putalpha(cl.split()[3].point(lambda v: v * 0.45))
        sh = sh.filter(ImageFilter.GaussianBlur(5))
        x, y = rng.randint(-150, BW - 100), rng.randint(-150, BH - 100)
        img.paste(Image.new("RGB", sh.size, (0, 0, 0)), (x + 3, y + 5), sh)
        img.paste(cl, (x, y), cl)
    arr = np.asarray(img, np.float32)
    yy, xx = np.mgrid[0:BH, 0:BW].astype(np.float32)
    v = 1 - 0.38 * (((xx - BW / 2) / (BW / 2)) ** 2 + ((yy - BH / 2) / (BH / 2)) ** 2)
    arr = arr * 0.80 * v[:, :, None]
    arr = arr * np.array([1.0, 0.97, 0.9])
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7))
    img.save(path, quality=92)
    return img


# ---------------------------------------------------------------- жазулар
def label(text, px=66, sub=None, seed=1):
    """Ақ скотч-жазу: қара бас әріп + (қаласаңыз) астында кішкентай тег."""
    f = oswald(px)
    tw = f.getlength(text)
    w, h = int(tw + 56), int(px * 1.45)
    pp, pad = paper(w, h, (241, 238, 230), seed, amp=2.5, grain=3)
    d = ImageDraw.Draw(pp)
    d.text((pad + 28, pad + h / 2), text, font=f, fill=INK + (255,), anchor="lm")
    out = rotate(pp, random.Random(seed).uniform(-1.6, 1.6))
    if sub:
        fs = font("Rubik", 30, 600)
        sw = int(fs.getlength(sub) + 30)
        sp, spad = paper(sw, 48, (236, 230, 214), seed + 5, amp=1.5, shadow=True, grain=2)
        ImageDraw.Draw(sp).text((spad + 15, spad + 24), sub, font=fs, fill=INK + (255,), anchor="lm")
        c = Image.new("RGBA", (max(out.width, sp.width), out.height + sp.height - 50), (0, 0, 0, 0))
        c.alpha_composite(out, ((c.width - out.width) // 2, 0))
        c.alpha_composite(sp, ((c.width - sp.width) // 2, out.height - 50))
        out = c
    return out


RANSOM_STY = [((24, 22, 20), (245, 242, 235)), ((246, 243, 236), (24, 22, 20)), ((178, 38, 32), (250, 246, 240)),
              ((214, 196, 160), (30, 28, 26)), ((236, 232, 222), (178, 38, 32)), ((150, 108, 70), (250, 246, 240))]
RANSOM_F = [("Oswald", 700), ("PTSerifBold", None), ("Rubik", 900), ("Oswald", 500)]


def ransom_letters(text, px=120, seed=3, maxw=860):
    """Әр әріп — бөлек қиылған қағаз. Қайтарады [(img, x, y)], жалпы өлшем."""
    rng = random.Random(seed)
    lines = text.split("\n")
    out, y, totw = [], 0, 0
    while True:
        ok = True
        tmp, y = [], 0
        for li, line in enumerate(lines):
            row, x = [], 0
            for ch in line:
                if ch == " ":
                    x += px * 0.35
                    continue
                bg, fg = rng.choice(RANSOM_STY)
                fn, wt = rng.choice(RANSOM_F)
                if ch in "ӘҒҚҢӨҰҮҺІ":
                    fn, wt = rng.choice([("Oswald", 700), ("PTSerifBold", None)])
                f = font(fn, int(px * rng.uniform(0.9, 1.0) * (0.92 if fn == "Rubik" else 1.0)), wt)
                bb = f.getbbox(ch)
                cw, chh = int(bb[2] - bb[0] + px * 0.28), int(px * 1.12)
                pp, pad = paper(cw, chh, bg, rng.randint(0, 9999), amp=2.0, shadow=True, grain=3)
                ImageDraw.Draw(pp).text((pad + cw / 2, pad + chh / 2), ch, font=f, fill=fg + (255,), anchor="mm")
                pp = rotate(pp, rng.uniform(-7, 7))
                row.append([pp, x, y + rng.uniform(-5, 5)])
                x += pp.width - 2 * pad - px * 0.07
            if x > maxw:
                ok = False
            tmp.append((row, x))
            y += px * 1.18
        if ok or px < 60:
            break
        px = int(px * 0.88)
        rng = random.Random(seed)
    for row, x in tmp:
        totw = max(totw, x)
    for row, x in tmp:
        off = (totw - x) / 2
        for r in row:
            out.append((r[0], r[1] + off, r[2]))
    return out, totw, y


# ---------------------------------------------------------------- иконкалар
def icon(name, s=150):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = s / 150.0
    def R(*v):
        return [int(x * k) for x in v]
    if name == "code":
        d.rounded_rectangle(R(10, 22, 140, 128), int(14 * k), fill=(40, 38, 36))
        d.rectangle(R(10, 22, 140, 44), fill=(70, 66, 62))
        for i, c in enumerate(((214, 90, 70), (226, 180, 80), (120, 170, 100))):
            d.ellipse(R(20 + i * 16, 28, 30 + i * 16, 38), fill=c)
        d.text((s / 2, 88 * k), "</>", font=oswald(54 * k), fill=(246, 240, 228), anchor="mm")
    elif name == "chat":
        d.rounded_rectangle(R(12, 24, 138, 108), int(16 * k), fill=ORANGE)
        d.polygon(R(36, 104, 36, 136, 66, 104), fill=ORANGE)
        for i in range(3):
            d.ellipse(R(44 + i * 24, 58, 60 + i * 24, 74), fill=(40, 34, 30))
    elif name == "clapper":
        d.rectangle(R(18, 62, 132, 130), fill=(36, 34, 32))
        d.polygon(R(14, 30, 128, 14, 134, 44, 20, 60), fill=(36, 34, 32))
        for i in range(5):
            x = 24 + i * 22
            d.polygon(R(x, 28 - i * 3, x + 11, 26 - i * 3, x + 16, 56 - i * 3, x + 5, 58 - i * 3), fill=(240, 236, 226))
        for i in range(3):
            d.rectangle(R(32, 78 + i * 16, 118 - i * 20, 86 + i * 16), fill=(240, 236, 226))
    elif name == "folder":
        d.rounded_rectangle(R(10, 30, 70, 56), int(8 * k), fill=(186, 140, 80))
        d.rounded_rectangle(R(10, 42, 140, 128), int(10 * k), fill=(212, 166, 100))
        d.rectangle(R(10, 56, 140, 62), fill=(192, 146, 84))
    elif name == "robot":
        d.line(R(75, 14, 75, 34), fill=(70, 66, 62), width=int(6 * k))
        d.ellipse(R(66, 6, 84, 24), fill=RED)
        d.rounded_rectangle(R(22, 34, 128, 120), int(20 * k), fill=(150, 160, 166))
        d.rounded_rectangle(R(36, 52, 114, 96), int(12 * k), fill=(40, 38, 36))
        d.ellipse(R(50, 64, 66, 80), fill=(120, 210, 230))
        d.ellipse(R(84, 64, 100, 80), fill=(120, 210, 230))
        d.rectangle(R(10, 64, 22, 92), fill=(120, 128, 134))
        d.rectangle(R(128, 64, 140, 92), fill=(120, 128, 134))
    elif name == "browser":
        d.rounded_rectangle(R(8, 20, 142, 130), int(10 * k), fill=(246, 244, 238), outline=(60, 56, 52), width=int(5 * k))
        d.rectangle(R(10, 22, 140, 44), fill=(60, 56, 52))
        d.rounded_rectangle(R(52, 28, 132, 38), int(5 * k), fill=(240, 236, 226))
        d.rectangle(R(22, 56, 76, 98), fill=(204, 132, 78))
        for i in range(4):
            d.rectangle(R(86, 58 + i * 12, 128, 64 + i * 12), fill=(150, 144, 136))
        d.rectangle(R(22, 108, 128, 116), fill=(150, 144, 136))
    elif name == "app":
        d.rounded_rectangle(R(16, 16, 134, 134), int(28 * k), fill=(60, 96, 140))
        for i in range(2):
            for j in range(2):
                c = [(240, 200, 90), (230, 110, 90), (130, 200, 150), (240, 236, 226)][i * 2 + j]
                d.rounded_rectangle(R(34 + j * 46, 34 + i * 46, 70 + j * 46, 70 + i * 46), int(9 * k), fill=c)
    elif name == "phone":
        d.rounded_rectangle(R(38, 6, 112, 144), int(16 * k), fill=(36, 34, 32))
        d.rounded_rectangle(R(45, 18, 105, 128), int(8 * k), fill=(226, 222, 212))
        d.rounded_rectangle(R(62, 10, 88, 14), 2, fill=(90, 86, 80))
        d.rounded_rectangle(R(52, 32, 98, 58), int(6 * k), fill=ORANGE)
        for i in range(3):
            d.rectangle(R(52, 70 + i * 14, 98 - i * 12, 78 + i * 14), fill=(150, 144, 136))
    elif name == "star":
        pts = []
        for i in range(10):
            r = 66 if i % 2 == 0 else 28
            a = -math.pi / 2 + i * math.pi / 5
            pts += [75 * k + r * k * math.cos(a), 78 * k + r * k * math.sin(a)]
        d.polygon(pts, fill=(238, 190, 60), outline=(150, 110, 30))
    return img


def card(ic, title, sub=None, seed=5, w=300, h=300, color=(222, 217, 205)):
    pp, pad = paper(w, h, color, seed, amp=3, grain=4)
    d = ImageDraw.Draw(pp)
    isz = int(h * 0.46)
    pp.alpha_composite(icon(ic, isz), (pad + (w - isz) // 2, pad + int(h * 0.08)))
    f = oswald(int(h * 0.15))
    while f.getlength(title) > w - 30:
        f = oswald(f.size - 3)
    d.text((pad + w / 2, pad + h * 0.66), title, font=f, fill=INK + (255,), anchor="mm")
    if sub:
        fs = font("Rubik", int(h * 0.075), 700)
        while fs.getlength(sub.upper()) > w - 30:
            fs = font("Rubik", fs.size - 2, 700)
        d.text((pad + w / 2, pad + h * 0.82), sub.upper(), font=fs, fill=(60, 56, 52, 255), anchor="mm")
    return rotate(pp, random.Random(seed).uniform(-3, 3))


def note(text, seed=7, px=58):
    f = hand(px)
    w = int(f.getlength(text) + 50)
    pp, pad = paper(w, int(px * 1.35), (243, 240, 232), seed, amp=2.5, grain=3)
    ImageDraw.Draw(pp).text((pad + 25, pad + px * 0.68), text, font=f, fill=INK + (255,), anchor="lm")
    return rotate(pp, random.Random(seed).uniform(-4, 4))


def badge(num, s=110):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, s - 2, s - 2], fill=BROWN)
    d.text((s / 2, s / 2), str(num), font=oswald(int(s * 0.6)), fill=(30, 26, 22), anchor="mm")
    return img


def stamp(text, seed=9):
    f = oswald(78)
    tw = f.getlength(text)
    w, h = int(tw + 70), 130
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([5, 5, w - 5, h - 5], 14, outline=RED + (235,), width=9)
    d.text((w / 2, h / 2), text, font=f, fill=RED + (235,), anchor="mm")
    a = np.asarray(img).copy()
    rng = np.random.default_rng(seed)
    holes = rng.random(a.shape[:2]) < 0.03
    a[holes, 3] = (a[holes, 3] * 0.3).astype(np.uint8)
    return rotate(Image.fromarray(a), -11)


def arrow_down(s=260):
    img = Image.new("RGBA", (s, s + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(s * 0.5 + 18 * math.sin(i / 10 * 3), 8 + i * (s - 40) / 10) for i in range(11)]
    d.line(pts, fill=INK + (255,), width=10, joint="curve")
    x, y = pts[-1]
    d.line([(x - 40, y - 34), (x, y + 6), (x + 40, y - 34)], fill=INK + (255,), width=10, joint="curve")
    return img


def sdisp(w):
    return M.disp(w.rstrip("—–")) or w


def subtitle(items, shown):
    """Қолжазба субтитр қағаз жолақта: айтылған сөз — қою, келесі — бозғыл, маңызды — қызыл."""
    px = 70
    while True:
        f = hand(px)
        sp = f.getlength(" ")
        tw = sum(f.getlength(sdisp(it["w"])) for it in items) + sp * (len(items) - 1)
        if tw <= 760 or px <= 46:
            break
        px -= 4
    w, h = int(tw + 70), int(px * 1.4)
    pp, pad = paper(w, h, (246, 244, 238), 21 + len(items), amp=2.5, grain=3)
    d = ImageDraw.Draw(pp)
    x = pad + 35
    for i, it in enumerate(items):
        t = sdisp(it["w"])
        col = RED if it.get("a") else INK
        al = 255 if i < shown else 70
        d.text((x, pad + h * 0.52), t, font=f, fill=col + (al,), anchor="lm")
        if it.get("a") and i < shown:
            lw = f.getlength(t)
            d.line([(x, pad + h * 0.86), (x + lw, pad + h * 0.80)], fill=RED + (200,), width=5)
        x += f.getlength(t) + sp
    return rotate(pp, -1.2)


# ---------------------------------------------------------------- рендер
def chunks(words, maxw=4, maxc=24):
    """Субтитр бөліктері: сөйлем шекарасын ешқашан араластырмайды."""
    out, cur = [], []
    for i, w in enumerate(words):
        if cur:
            prev = words[i - 1]
            n = sum(len(x["w"]) + 1 for x in cur)
            if (len(cur) >= maxw or n + len(w["w"]) > maxc or prev["w"][-1:] in ".?!…—"
                    or w["w"] in ("—",) or w["s"] - prev["e"] > 0.45 or (prev["w"][-1:] in ",;:" and len(cur) >= 2)):
                out.append(cur)
                cur = []
        cur.append(dict(w))
    if cur:
        out.append(cur)
    res = []
    for k, c in enumerate(out):
        s, e = c[0]["s"], c[-1]["e"]
        nxt = out[k + 1][0]["s"] if k + 1 < len(out) else e + 0.6
        res.append({"s": s, "e": min(max(e + 0.15, s + 0.8), nxt - 0.02) if nxt > s + 0.1 else e, "items": c})
    return res

def to_arr(im):
    a = np.asarray(im, dtype=np.float32)
    return a[:, :, :3][:, :, ::-1].copy(), a[:, :, 3:] / 255.0


class KollazhRenderer:
    def __init__(self, plan, tl):
        self.plan, self.tl = plan, tl
        self.scenes = list(zip(plan["scenes"], tl["scenes"]))
        os.makedirs(".matin", exist_ok=True)
        bg = newspaper_bg(os.path.join(".matin", "kollazh_bg.jpg"))
        self.bg = np.asarray(bg)[:, :, ::-1].copy()
        self.cache = {}
        self.chunks = []
        for sc, t in self.scenes:
            self.chunks.append(chunks(t["words"]))
        self.dur = tl["dur"]

    def C(self, key, maker):
        if key not in self.cache:
            v = maker()
            self.cache[key] = to_arr(v) if isinstance(v, Image.Image) else v
        return self.cache[key]

    def blend(self, fr, rgba, cx, cy, alpha=1.0, scale=1.0):
        import cv2
        rgb, a = rgba
        if abs(scale - 1) > 0.01:
            sz = (max(1, int(a.shape[1] * scale)), max(1, int(a.shape[0] * scale)))
            rgb = cv2.resize(rgb, sz)
            a = cv2.resize(a, sz)[:, :, None]
        h, w = a.shape[:2]
        x, y = int(cx - w / 2), int(cy - h / 2)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0 or alpha <= 0.003:
            return
        sa = a[y0 - y:y1 - y, x0 - x:x1 - x] * alpha
        reg = fr[y0:y1, x0:x1].astype(np.float32)
        fr[y0:y1, x0:x1] = (reg * (1 - sa) + rgb[y0 - y:y1 - y, x0 - x:x1 - x] * sa).astype(np.uint8)

    def word_time(self, k, stem, default):
        t = self.scenes[k][1]
        stem = stem.lower()
        for w in t["words"]:
            if M.norm(w["w"]).startswith(M.norm(stem)):
                return w["s"]
        return default

    # --- бір парақ және оның мазмұны
    def draw_scene(self, fr, k, t, dy, subs=True):
        sc, tl = self.scenes[k]
        st, en = tl["s"], tl["speech_e"]
        X, Y, SW, SH = SHEET
        sheet = self.C(("sheet", k), lambda: paper(SW, SH, PAPERS[k % len(PAPERS)], 100 + k, amp=6)[0])
        self.blend(fr, sheet, X + SW / 2, Y + SH / 2 + dy)
        oy = dy
        # тақырып
        ty = Y + 115 + oy
        if sc.get("num"):
            bd = self.C(("badge", k), lambda: badge(sc["num"]))
            p = back((t - st) / 0.35)
            self.blend(fr, bd, X + 95, ty, M.clamp(p * 3, 0, 1), 0.6 + 0.4 * p)
        if sc.get("ransom"):
            letters = self.C(("ransom", k), lambda: ransom_letters(sc["ransom"], 118, 30 + k))
            lets, tw, th = letters
            n = len(lets)
            per = min(0.07, 0.6 / max(1, n))
            for i, (im, lx, ly) in enumerate(lets):
                tt = (t - st - 0.02 - i * per) / 0.18
                if tt <= 0:
                    continue
                a = self.C(("rl", k, i), lambda im=im: im)
                p = back(tt)
                self.blend(fr, a, W / 2 - tw / 2 + lx + im.width / 2 - 20, ty - 20 + ly + im.height / 2 - 30,
                           M.clamp(tt * 3, 0, 1), 1.5 - 0.5 * p)
            title_h = th
        elif sc.get("label"):
            lb = self.C(("label", k), lambda: label(sc["label"], 66, sc.get("label_sub"), 40 + k))
            p = back((t - st + 0.1) / 0.35)
            self.blend(fr, lb, W / 2 + (30 if sc.get("num") else 0), ty + lb[1].shape[0] / 2 - 50,
                       M.clamp(p * 3, 0, 1), 1.25 - 0.25 * p)
            title_h = lb[1].shape[0]
        else:
            title_h = 0
        # сурет
        art = sc.get("art")
        if art:
            self.draw_art(fr, k, art, t, st, en, oy)
        if sc.get("note"):
            nt = self.C(("note", k), lambda: note(sc["note"], 60 + k))
            tn = sc.get("note_at")
            t0 = self.word_time(k, tn, st + 0.8) if tn else st + 0.8
            p = back((t - t0) / 0.35)
            if p > 0:
                self.blend(fr, nt, W / 2 + 30, Y + 990 + oy, M.clamp(p * 3, 0, 1), 1.2 - 0.2 * p)
        # субтитр
        if subs:
            chs = self.chunks[k]
            for ci, ch in enumerate(chs):
                nxt = chs[ci + 1]["s"] if ci + 1 < len(chs) else 1e9
                if ch["s"] - 0.05 <= t < min(max(ch["e"], ch["s"] + 0.6) + 0.25, nxt - 0.03):
                    shown = max(1, sum(1 for w in ch["items"] if w["s"] <= t + 0.04))
                    im = self.C(("sub", k, ci, shown), lambda ch=ch, shown=shown: subtitle(ch["items"], shown))
                    p = ease((t - ch["s"] + 0.05) / 0.14)
                    self.blend(fr, im, W / 2, Y + SH - 150 + (1 - p) * 20 + oy, p)

    def draw_art(self, fr, k, art, t, st, en, oy):
        X, Y, SW, SH = SHEET
        cy = Y + 560 + oy
        typ = art.get("type")
        if typ == "phone":
            ph = self.C(("ph", k), lambda: icon("phone", 520))
            p = back((t - st) / 0.45)
            bob = math.sin(t * 2.2) * 8
            self.blend(fr, ph, W / 2, cy + bob, M.clamp(p * 3, 0, 1), 0.7 + 0.3 * p)
            qs = [(-300, -170, 0.25, -14), (290, -120, 0.45, 12), (-250, 190, 0.65, 10), (270, 210, 0.85, -8)]
            for i, (qx, qy, d0, rot) in enumerate(qs):
                q = self.C(("q", k, i), lambda i=i, rot=rot: rotate(ransom_letters("?", 130 - i * 10, 70 + i)[0][0][0], rot))
                pq = back((t - st - d0) / 0.3)
                if pq > 0:
                    self.blend(fr, q, W / 2 + qx, cy + qy + math.sin(t * 3 + i) * 6, M.clamp(pq * 3, 0, 1),
                               0.5 + 0.5 * pq)
        elif typ in ("cards", "link"):
            items = art["items"]
            n = len(items)
            if n == 4:
                cw, ch = 330, 300
                pos = [(-185, -165), (185, -165), (-185, 175), (185, 175)]
            elif n == 3:
                cw, ch = 262, 290
                pos = [(-300, 0), (0, 0), (300, 0)]
            elif n == 2:
                cw, ch = 320, 320
                pos = [(-200, -60 if typ == "link" else 0), (200, -60 if typ == "link" else 0)]
            else:
                cw, ch = 440, 440
                pos = [(0, 0)]
            times = []
            for i, it in enumerate(items):
                d0 = st + 0.3 + i * 0.45
                t0 = self.word_time(k, it["at"], d0) - 0.1 if it.get("at") else d0
                times.append(t0)
                c = self.C(("card", k, i), lambda it=it, i=i: card(it["icon"], it["t"], it.get("s"), 200 + k * 10 + i, cw, ch))
                p = back((t - t0) / 0.4)
                if p > 0:
                    self.blend(fr, c, W / 2 + pos[i][0], cy + pos[i][1] + (1 - p) * 40, M.clamp(p * 3, 0, 1),
                               0.8 + 0.2 * p)
            if art.get("join") and n >= 2 and typ == "cards":
                for i in range(n - 1):
                    jx = W / 2 + (pos[i][0] + pos[i + 1][0]) / 2
                    jy = cy + (pos[i][1] + pos[i + 1][1]) / 2
                    j = self.C(("join", k), lambda: ransom_letters(art["join"], 90, 90 + k)[0][0][0])
                    pj = back((t - times[i + 1] + 0.15) / 0.3)
                    if pj > 0:
                        self.blend(fr, j, jx, jy, M.clamp(pj * 3, 0, 1), 0.6 + 0.4 * pj)
            if typ == "link" and n == 2:
                self.draw_cable(fr, k, cy, pos, ch, t, max(times) + 0.35)
        elif typ == "folder":
            p = back((t - st - 0.2) / 0.45)
            f = self.C(("fold", k), lambda: self.big_folder(art.get("t", "ЖОБА")))
            self.blend(fr, f, W / 2, cy + (1 - p) * 50, M.clamp(p * 3, 0, 1), 0.8 + 0.2 * p)
        elif typ == "fork":
            fa = self.C(("fa", k), lambda: card("folder", art.get("a", "ЖОБА"), art.get("as"), 300 + k, 300, 300))
            fb = self.C(("fb", k), lambda: card("folder", art.get("b", "СІЗДІКІ"), art.get("bs"), 310 + k, 300, 300,
                                                 (238, 226, 196)))
            pa = back((t - st - 0.2) / 0.4)
            self.blend(fr, fa, W / 2 - 210, cy + 60, M.clamp(pa * 3, 0, 1), 0.8 + 0.2 * pa)
            t1 = self.word_time(k, art.get("at", "Fork"), st + 1.0)
            pb = ease((t - t1) / 0.6)
            if pb > 0:
                self.blend(fr, fb, W / 2 - 210 + 420 * pb, cy + 60 + math.sin(pb * math.pi) * -90, M.clamp(pb * 4, 0, 1))
                ar = self.C(("forkar", k), lambda: self.curved_arrow())
                self.blend(fr, ar, W / 2, cy - 160, M.clamp((pb - 0.5) * 3, 0, 1))
        elif typ == "stamp":
            c = self.C(("sc", k), lambda: card(art.get("icon", "code"), art.get("t", "КОД"), art.get("s"), 400 + k, 420, 420))
            p = back((t - st - 0.2) / 0.4)
            self.blend(fr, c, W / 2, cy, M.clamp(p * 3, 0, 1), 0.8 + 0.2 * p)
            t1 = self.word_time(k, art.get("at", ""), st + 1.2) if art.get("at") else st + 1.2
            q = (t - t1) / 0.22
            if q > 0:
                s = self.C(("stp", k), lambda: stamp(art.get("stamp", "КЕРЕК ЕМЕС")))
                self.blend(fr, s, W / 2 + 10, cy + 170, M.clamp(q * 2, 0, 1), 1 + 0.8 * (1 - ease(q)))
        elif typ == "button":
            p = back((t - st - 0.3) / 0.4)
            b = self.C(("btn", k), lambda: self.button(art.get("t", "Подробнее")))
            pulse = 1 + 0.04 * math.sin((t - st) * 6) if p >= 1 else 0.7 + 0.3 * p
            self.blend(fr, b, W / 2, cy - 60, M.clamp(p * 3, 0, 1), pulse)
            a = self.C(("arr", k), lambda: arrow_down())
            pa = ease((t - st - 0.8) / 0.3)
            self.blend(fr, a, W / 2, cy + 230 + abs(math.sin((t - st) * 4)) * 26, pa)

    def draw_cable(self, fr, k, cy, pos, ch, t, t0):
        import cv2
        p = ease((t - t0) / 0.7)
        if p <= 0:
            return
        x1, x2 = W / 2 + pos[0][0], W / 2 + pos[1][0]
        y0 = cy + pos[0][1] + ch / 2 + 10
        plug = self.C(("plug",), lambda: self.plug())
        for x in (x1, x2):
            self.blend(fr, plug, x, y0 + 18)
        pts = []
        N = 60
        for i in range(N + 1):
            u = i / N
            # U-подобный сым
            x = x1 + (x2 - x1) * u
            y = y0 + 40 + math.sin(u * math.pi) * 190
            pts.append((x, y))
        m = int(N * p) + 1
        a = np.array(pts[:m], np.int32)
        if len(a) >= 2:
            cv2.polylines(fr, [a + [0, 5]], False, (40, 50, 60), 16, cv2.LINE_AA)
            cv2.polylines(fr, [a], False, (84, 128, 176)[::-1] if False else (84, 128, 176), 14, cv2.LINE_AA)
            cv2.polylines(fr, [a], False, (110, 160, 206), 5, cv2.LINE_AA)
        if p >= 1:
            ps = back((t - t0 - 0.7) / 0.35)
            st_ = self.C(("star",), lambda: icon("star", 120))
            self.blend(fr, st_, (x1 + x2) / 2, y0 + 40 + 190, M.clamp(ps * 3, 0, 1), 0.4 + 0.6 * ps)

    def plug(self):
        img = Image.new("RGBA", (44, 60), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([12, 0, 16, 14], fill=(120, 120, 120))
        d.rectangle([28, 0, 32, 14], fill=(120, 120, 120))
        d.rounded_rectangle([4, 12, 40, 44], 6, fill=(170, 170, 166), outline=(110, 110, 106), width=2)
        d.rectangle([16, 44, 28, 60], fill=(176, 128, 84))
        return img

    def big_folder(self, title):
        w, h = 560, 470
        img = Image.new("RGBA", (w + 80, h + 80), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([40, 40, 260, 110], 18, fill=(186, 140, 80))
        for i, (dx, rot) in enumerate(((150, -8), (260, 4), (370, 10))):
            pp, pad = paper(170, 230, (246, 244, 238), 500 + i, amp=2, grain=2)
            dd = ImageDraw.Draw(pp)
            for j in range(7):
                dd.rectangle([pad + 20, pad + 30 + j * 26, pad + 150 - (j % 3) * 25, pad + 38 + j * 26], fill=(150, 144, 136))
            pp = rotate(pp, rot)
            img.alpha_composite(pp, (dx - pp.width // 2 + 40, 40))
        front = Image.new("RGBA", img.size, (0, 0, 0, 0))
        fd = ImageDraw.Draw(front)
        fd.rounded_rectangle([40, 150, 40 + w, 40 + h], 22, fill=(212, 166, 100))
        fd.rectangle([40, 150, 40 + w, 166], fill=(192, 146, 84))
        img.alpha_composite(front)
        lb = label(title, 60, None, 77)
        img.alpha_composite(lb, ((img.width - lb.width) // 2, 300))
        return img

    def curved_arrow(self):
        img = Image.new("RGBA", (420, 160), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pts = [(40 + i * 34, 120 - math.sin(i / 10 * math.pi) * 90) for i in range(11)]
        d.line(pts, fill=INK + (255,), width=9, joint="curve")
        x, y = pts[-1]
        d.line([(x - 44, y - 30), (x, y), (x - 10, y - 50)], fill=INK + (255,), width=9, joint="curve")
        f = hand(56)
        d.text((210, 70), "Fork", font=f, fill=RED + (255,), anchor="mm")
        return img

    def button(self, text):
        f = oswald(92)
        tw = f.getlength(text.upper())
        w, h = int(tw + 140), 190
        pp, pad = paper(w, h, (28, 26, 24), 801, amp=3, grain=4)
        d = ImageDraw.Draw(pp)
        d.text((pad + w / 2, pad + h / 2 - 4), text.upper(), font=f, fill=(246, 242, 234, 255), anchor="mm")
        return rotate(pp, -2)

    # --- кадр
    def frame(self, t):
        X, Y, SW, SH = SHEET
        ox = int(80 + 60 * math.sin(t * 0.07))
        oy_ = int(80 + 60 * math.cos(t * 0.05))
        fr = self.bg[oy_:oy_ + H, ox:ox + W].copy()
        cur = 0
        for k, (sc, tl) in enumerate(self.scenes):
            if t >= tl["s"] - 0.35:
                cur = k
        # алдыңғы парақ (соңғы күйінде), сосын жаңасы төменнен шығады
        sc, tl = self.scenes[cur]
        slide_s = tl["s"] - 0.35
        p = 1.0 if cur == 0 else ease((t - slide_s) / 0.45)
        if p < 1 and cur > 0:
            ptl = self.scenes[cur - 1][1]
            self.draw_scene(fr, cur - 1, ptl["e"], 0, subs=False)
        dy = (1 - p) * (H - Y + 40)
        self.draw_scene(fr, cur, t, dy, subs=p >= 0.95)
        return fr

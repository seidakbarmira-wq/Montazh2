#!/usr/bin/env python3
"""montazh.py — тік Reels монтаж движогы (@i10_aktoty_).

Командалар (жоба түбірінен):
  check | setup
  prep    VIDEO [--text FILE] [--lang kk] [--model small]
  plan    VIDEO [--style taza|plashka|neon] [--accent HEX] [--keep]
  raskadrovka VIDEO                     -> Раскадровка/<аты>_раскадровка.jpg
  render  VIDEO [--max-mb 90]       -> Дайын/<аты>_vN.mp4
  frames  RENDERED.mp4              -> Раскадровка/<аты>_кадрлар.jpg
  cover-candidates VIDEO            -> Раскадровка/<аты>_обложка_кадрлар.jpg
  cover   VIDEO --t SEC --title "Монтаж *5 минутта*" [--tag TEXT]
"""
import argparse, json, math, os, re, subprocess, sys, shutil, difflib

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
FONT = os.path.join(SKILL, "fonts", "Montserrat.ttf")
FACE_MODEL = os.path.join(SKILL, "models", "face_yunet.onnx")

W, H, FPS = 1080, 1920, 30
# Қауіпсіз рамка (Instagram батырмалары жаппайтын аймақ)
SAFE_L, SAFE_R, SAFE_T, SAFE_B = 70, 1000, 220, 1490
LOW_Y, LOW_R = 1070, 915          # y>LOW_Y болса, оң шекара LOW_R
SUB_CX, SUB_MAXW = 540, 730        # субтитр ортасы және ең үлкен ені (175..905)
GAP = 40                           # беттен ең аз қашықтық, px

STYLES = {
    "taza":    {"accent": "#FFD60A", "desc": "Таза: ақ мәтін, қара жиек, маңызды сөз түсті"},
    "plashka": {"accent": "#FF5C8A", "desc": "Плашка: маңызды сөз түсті жапсырма ішінде"},
    "neon":    {"accent": "#3EC5FF", "desc": "Неон: маңызды сөз жарқырайды (ИИ, технология)"},
}

DIRS = {"src": "Бастапқы", "sketch": "Раскадровка", "out": "Дайын"}


# ---------------------------------------------------------------- утилиталар
def die(msg):
    print("ҚАТЕ: " + msg)
    sys.exit(1)


def run(cmd, capture=True):
    r = subprocess.run(cmd, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "ignore")[-1500:]
        die("команда сәтсіз: %s\n%s" % (" ".join(cmd[:6]), err))
    return (r.stdout or b"").decode("utf-8", "ignore")


def workdir(video):
    name = os.path.splitext(os.path.basename(video))[0]
    d = os.path.join(".montazh", name)
    os.makedirs(d, exist_ok=True)
    return name, d


def jload(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def jsave(p, obj):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def clamp(v, a, b):
    return max(a, min(b, v))


def ease(x):
    x = clamp(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


# ---------------------------------------------------------------- check/setup
def cmd_check(a):
    ok = True
    for b in ("ffmpeg", "ffprobe"):
        if not shutil.which(b):
            print("жоқ: " + b + "  (apt-get install -y ffmpeg)")
            ok = False
    for m in ("cv2", "numpy", "PIL", "faster_whisper"):
        try:
            __import__(m)
        except Exception:
            print("жоқ: python модулі " + m)
            ok = False
    for p in (FONT, FACE_MODEL):
        if not os.path.exists(p):
            print("жоқ: " + p)
            ok = False
    print("БӘРІ ДАЙЫН" if ok else "setup керек")
    sys.exit(0 if ok else 1)


def cmd_setup(a):
    pkgs = ["numpy", "opencv-python-headless", "pillow", "faster-whisper", "piper-tts"]
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--break-system-packages"] + pkgs)
    if r.returncode != 0:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q"] + pkgs)
    if not shutil.which("ffmpeg"):
        subprocess.run("apt-get update -qq && apt-get install -y -qq ffmpeg", shell=True)
    try:  # Whisper моделін алдын ала жүктеу (бірінші монтаж тезірек болады)
        from faster_whisper import WhisperModel
        WhisperModel("small", device="cpu", compute_type="int8")
    except Exception as e:
        print("Whisper моделі жүктелмеді (желіге рұқсатты тексер): %s" % e)
    print("setup бітті. Тексеру: check")


# ---------------------------------------------------------------- probe
def probe(video):
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", video])
    j = json.loads(out)
    vs = [s for s in j["streams"] if s.get("codec_type") == "video"]
    if not vs:
        die("видео ағыны жоқ")
    v = vs[0]
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    try:
        rot = int(v.get("tags", {}).get("rotate", 0))
    except Exception:
        pass
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                rot = int(float(sd["rotation"]))
            except Exception:
                pass
    if abs(rot) % 180 == 90:
        w, h = h, w
    dur = float(j["format"].get("duration") or v.get("duration") or 0)
    hdr = v.get("color_transfer") in ("arib-std-b67", "smpte2084")
    has_audio = any(s.get("codec_type") == "audio" for s in j["streams"])
    return {"W": w, "H": h, "dur": dur, "hdr": hdr, "audio": has_audio}


def hdr_chain():
    return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")


# ---------------------------------------------------------------- бет табу
def analyze_faces(video, info):
    import numpy as np, cv2
    sw = 640 if info["W"] >= info["H"] else 360
    sh = int(round(info["H"] * sw / info["W"] / 2)) * 2
    vf = (hdr_chain() + "," if info["hdr"] else "") + "fps=4,scale=%d:%d,format=bgr24" % (sw, sh)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-vf", vf, "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE)
    det = cv2.FaceDetectorYN.create(FACE_MODEL, "", (sw, sh), 0.6, 0.3, 5000)
    k = info["W"] / sw
    faces, i, n = [], 0, sw * sh * 3
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        img = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
        _, res = det.detect(img)
        f = None
        if res is not None and len(res):
            best = max(res, key=lambda r: r[2] * r[3])
            x, y, bw, bh = [float(v) * k for v in best[:4]]
            f = [round(x, 1), round(y, 1), round(x + bw, 1), round(y + bh, 1)]
        faces.append(f)
        i += 1
    p.wait()
    return faces  # 4 Гц, дисплей координатасында [x0,y0,x1,y1] немесе null


# ---------------------------------------------------------------- сөздер
PUNCT = ".,;:!?«»\"'()—–-…"


def disp(w):
    """Экранға: соңындағы нүкте/үтірді алып тастаймыз, ?! қалады."""
    return w.rstrip(",.;:…").lstrip("«\"(") .rstrip("»\")") or w


def norm(w):
    return re.sub(r"[^\w]", "", w.lower())


def read_text(path):
    raw = open(path, encoding="utf-8").read()
    out = []
    for m in re.finditer(r"\*([^*]+)\*|(\S+)", raw):
        if m.group(1):
            for i, w in enumerate(m.group(1).split()):
                out.append({"w": w, "a": True})
        else:
            tok = m.group(2)
            if out and not re.search(r"\w", tok):
                out[-1]["w"] += tok  # жеке тұрған үтір/нүкте алдыңғы сөзге жабысады
            else:
                out.append({"w": tok, "a": False})
    return out


def whisper_words(video, lang, model):
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return None
    try:
        m = WhisperModel(model, device="cpu", compute_type="int8")
        # аудионы ffmpeg-пен оқимыз (PyAV нұсқасы faster-whisper-мен сәйкеспеуі мүмкін)
        import numpy as np
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
                              "-f", "f32le", "-"], stdout=subprocess.PIPE, check=True).stdout
        audio = np.frombuffer(raw, np.float32)
        segs, _ = m.transcribe(audio, language=lang, word_timestamps=True, vad_filter=True)
        ws = []
        for s in segs:
            for w in (s.words or []):
                t = w.word.strip()
                if t:
                    ws.append({"w": t, "s": round(w.start, 3), "e": round(w.end, 3)})
        return ws
    except Exception as e:
        print("Whisper іске қосылмады: %s" % e)
        return None


def voiced_segments(video, dur):
    p = subprocess.run(["ffmpeg", "-v", "info", "-i", video, "-af", "silencedetect=noise=-35dB:d=0.35",
                        "-f", "null", "-"], stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    txt = p.stderr.decode("utf-8", "ignore")
    st = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", txt)]
    en = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", txt)]
    segs, cur = [], 0.0
    for i, s in enumerate(st):
        if s - cur > 0.15:
            segs.append([cur, s])
        cur = en[i] if i < len(en) else dur
    if dur - cur > 0.15:
        segs.append([cur, dur])
    return segs or [[0.0, dur]]


def spread_words(words, segs):
    """Whisper жоқ кезде: сөздерді дауыс бар бөліктерге әріп санына қарай таратамыз."""
    total = sum(e - s for s, e in segs)
    chars = [len(w["w"]) + 1 for w in words]
    csum = float(sum(chars)) or 1.0
    out, acc = [], 0.0

    def at(x):  # x: 0..total -> уақыт
        for s, e in segs:
            if x <= e - s:
                return s + x
            x -= e - s
        return segs[-1][1]
    for w, c in zip(words, chars):
        a0 = acc / csum * total
        acc += c
        a1 = acc / csum * total
        out.append({"w": w["w"], "a": w["a"], "s": round(at(a0), 3), "e": round(at(a1) - 0.02, 3)})
    return out


def align(user, heard):
    """Пайдаланушы мәтінін (дұрыс жазылған) Whisper уақытымен байланыстыру."""
    A = [norm(w["w"]) for w in user]
    B = [norm(w["w"]) for w in heard]
    sm = difflib.SequenceMatcher(None, A, B, autojunk=False)
    t = [None] * len(user)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            t[blk.a + k] = (heard[blk.b + k]["s"], heard[blk.b + k]["e"])
    # жуық сәйкестік: тең емес блоктарда ұқсас сөздерді жұптастыру
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            for i in range(i1, i2):
                best, bj = 0.0, None
                for j in range(j1, j2):
                    r = difflib.SequenceMatcher(None, A[i], B[j]).ratio()
                    if r > best:
                        best, bj = r, j
                if bj is not None and best >= 0.6:
                    t[i] = (heard[bj]["s"], heard[bj]["e"])
    matched = sum(1 for x in t if x)
    # қалғандарын көршілер арасында интерполяция
    n = len(user)
    i = 0
    while i < n:
        if t[i] is None:
            j = i
            while j < n and t[j] is None:
                j += 1
            s0 = t[i - 1][1] if i > 0 else (heard[0]["s"] if heard else 0.0)
            s1 = t[j][0] if j < n else (heard[-1]["e"] if heard else s0 + 0.4 * (j - i))
            if s1 <= s0:
                s1 = s0 + 0.3 * (j - i)
            step = (s1 - s0) / (j - i)
            for k in range(i, j):
                t[k] = (s0 + step * (k - i), s0 + step * (k - i + 1) - 0.02)
            i = j
        else:
            i += 1
    out = [{"w": u["w"], "a": u["a"], "s": round(a, 3), "e": round(b, 3)} for u, (a, b) in zip(user, t)]
    return out, matched / max(1, n)


def cmd_prep(a):
    name, wd = workdir(a.video)
    info = probe(a.video)
    print("видео: %dx%d, %.1f сек%s%s" % (info["W"], info["H"], info["dur"],
          ", HDR (түсі түзетіледі)" if info["hdr"] else "", "" if info["audio"] else ", ДЫБЫС ЖОҚ"))
    print("бет іздеп жатырмын…")
    faces = analyze_faces(a.video, info)
    seen = sum(1 for f in faces if f)
    print("бет табылды: %d%% кадрда" % (100 * seen // max(1, len(faces))))
    user = read_text(a.text) if a.text else None
    heard = whisper_words(a.video, a.lang, a.model) if info["audio"] else None
    if user and heard:
        words, q = align(user, heard)
        print("мәтін дауыспен %d%% сәйкес келді" % round(q * 100))
        if q < 0.7:
            print("ЕСКЕРТУ: уақыты жуық — раскадровканы мұқият тексер")
    elif heard:
        words, q = [dict(w, a=False) for w in heard], 1.0
        print("Whisper %d сөз тапты (мәтін берілмеді — қазақшада қате болуы мүмкін, тексер)" % len(words))
    elif user:
        words, q = spread_words(user, voiced_segments(a.video, info["dur"])), 0.0
        print("ЕСКЕРТУ: Whisper жоқ — уақыт дауыс бөліктеріне шамамен таратылды")
    else:
        die("сөз жоқ: --text беріңіз немесе setup жасаңыз (Whisper)")
    info.update({"faces": faces, "words": words, "match": round(q, 2), "lang": a.lang})
    jsave(os.path.join(wd, "data.json"), info)
    print("дайын: %s/data.json (%d сөз)" % (wd, len(words)))


# ---------------------------------------------------------------- геометрия
class Geo:
    """Бастапқы видео -> 1080x1920 кадр. Бетке қарай кесу және зум."""

    def __init__(self, info, edit):
        import numpy as np
        self.np = np
        self.W, self.H = info["W"], info["H"]
        self.s0 = max(W / self.W, H / self.H)
        self.faces = info["faces"]
        self.base = float(edit.get("base_zoom", 1.0))
        self.pushes = edit.get("pushes", [])
        n = len(self.faces)
        cx = np.full(n, self.W / 2.0)
        cy = np.full(n, self.H / 2.0)
        have = np.array([f is not None for f in self.faces]) if n else np.array([])
        if n and have.any():
            idx = np.where(have)[0]
            fx = np.array([(self.faces[i][0] + self.faces[i][2]) / 2 for i in idx])
            fy = np.array([(self.faces[i][1] + self.faces[i][3]) / 2 for i in idx])
            cx = np.interp(np.arange(n), idx, fx)
            cy = np.interp(np.arange(n), idx, fy)
            k = 9  # ~2 сек жұмсарту — кадр дірілдемейді
            ker = np.ones(k) / k
            cx = np.convolve(np.pad(cx, k // 2, mode="edge"), ker, "valid")
            cy = np.convolve(np.pad(cy, k // 2, mode="edge"), ker, "valid")
        self.cx, self.cy, self.have = cx, cy, have

    def face_raw(self, t):
        if not self.faces:
            return None
        i = int(clamp(round(t * 4), 0, len(self.faces) - 1))
        for d in (0, 1, -1, 2, -2):
            j = i + d
            if 0 <= j < len(self.faces) and self.faces[j]:
                return self.faces[j]
        return None

    def zoom(self, t):
        z = self.base
        for p in self.pushes:
            s, e, dz = p["s"], p["e"], p.get("z", 0.07)
            if s - 0.01 <= t <= e + 0.6:
                up = ease((t - s) / 0.9)
                down = 1 - ease((t - e) / 0.6) if t > e else 1
                z += dz * up * down
        return z

    def view(self, t):
        z = self.zoom(t)
        S = self.s0 * z
        ww, hh = W / S, H / S
        i = int(clamp(round(t * 4), 0, max(0, len(self.cx) - 1))) if len(self.cx) else 0
        fcx = self.cx[i] if len(self.cx) else self.W / 2
        fcy = self.cy[i] if len(self.cy) else self.H / 2
        cy = self.H / 2 + (fcy - self.H / 2) * clamp((z - 1) / 0.12, 0, 1)
        x0 = clamp(fcx - ww / 2, 0, self.W - ww)
        y0 = clamp(cy - hh / 2, 0, self.H - hh)
        return x0, y0, S

    def head(self, t):
        """Шығыс кадрдағы бас: (сол, төбе, оң, иек) немесе None."""
        f = self.face_raw(t)
        if not f:
            return None
        x0, y0, S = self.view(t)
        fw, fh = f[2] - f[0], f[3] - f[1]
        L = (f[0] - 0.18 * fw - x0) * S
        R = (f[2] + 0.18 * fw - x0) * S
        top = (f[1] - 0.55 * fh - y0) * S
        chin = (f[3] + 0.10 * fh - y0) * S
        return L, top, R, chin

    def head_span(self, s, e):
        hs = [self.head(s + (e - s) * k / 6.0) for k in range(7)]
        hs = [h for h in hs if h]
        if not hs:
            return None
        return (min(h[0] for h in hs), min(h[1] for h in hs), max(h[2] for h in hs), max(h[3] for h in hs))


# ---------------------------------------------------------------- мәтін салу
class Painter:
    def __init__(self, edit):
        from PIL import ImageFont
        self.F = ImageFont
        self.style = edit.get("style", "taza")
        self.acc = hex2rgb(edit.get("accent") or STYLES[self.style]["accent"])
        self.size = int(edit.get("size") or 84)
        self._fonts = {}

    def font(self, px, weight=800):
        key = (px, weight)
        if key not in self._fonts:
            f = self.F.truetype(FONT, px)
            try:
                f.set_variation_by_axes([weight])
            except Exception:
                pass
            self._fonts[key] = f
        return self._fonts[key]

    def text_w(self, txt, px):
        return self.font(px).getlength(txt)

    def lines_for(self, items, scale=1.0):
        """Сөздерді 1–2 жолға бөлу. Маңызды сөз — бөлек жолда."""
        px = int(self.size * scale)
        if any(it["a"] for it in items) and len(items) > 1:
            lines, cur = [], []
            for i, it in enumerate(items):
                if it["a"]:
                    if cur:
                        lines.append(cur)
                    lines.append([i])
                    cur = []
                else:
                    cur.append(i)
            if cur:
                lines.append(cur)
            if len(lines) <= 2:
                return lines
        words = [disp(it["w"]) for it in items]
        full = " ".join(words)
        if self.text_w(full, px) <= SUB_MAXW * 0.92 and len(items) <= 3:
            return [list(range(len(items)))]
        best, bk = None, 1
        for k in range(1, len(items)):
            a = self.text_w(" ".join(words[:k]), px)
            b = self.text_w(" ".join(words[k:]), px)
            sc = max(a, b) + abs(a - b) * 0.3
            if best is None or sc < best:
                best, bk = sc, k
        return [list(range(bk)), list(range(bk, len(items)))]

    def render_chunk(self, items, lines, shown, scale=1.0):
        """RGBA сурет: shown — қанша сөз айтылды (қалғаны күңгірт)."""
        from PIL import Image, ImageDraw, ImageFilter
        base = int(self.size * scale)
        rows = []
        for ln in lines:
            acc_line = any(items[i]["a"] for i in ln)
            px = int(base * (1.22 if acc_line and len(ln) == 1 else 1.0))
            f = self.font(px)
            sp = f.getlength(" ")
            ws = [f.getlength(disp(items[i]["w"]).upper() if items[i]["a"] else disp(items[i]["w"])) for i in ln]
            rows.append((ln, px, f, sp, ws, sum(ws) + sp * (len(ln) - 1)))
        pad = 34
        cw = int(max(r[5] for r in rows)) + pad * 2
        lh = [int(r[1] * 1.22) for r in rows]
        ch = sum(lh) + pad * 2
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        glow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d, g = ImageDraw.Draw(img), ImageDraw.Draw(glow)
        y = pad
        for (ln, px, f, sp, ws, tw), h in zip(rows, lh):
            x = (cw - tw) / 2
            for i, wpx in zip(ln, ws):
                it = items[i]
                txt = disp(it["w"]).upper() if it["a"] else disp(it["w"])
                on = i < shown
                al = 255 if on else 105
                if it["a"] and self.style == "plashka":
                    r = 16
                    d.rounded_rectangle([x - 16, y - 4, x + wpx + 16, y + px * 1.14], r,
                                        fill=self.acc + (al,))
                    lum = 0.3 * self.acc[0] + 0.59 * self.acc[1] + 0.11 * self.acc[2]
                    col = (17, 17, 17) if lum > 150 else (255, 255, 255)
                    d.text((x, y), txt, font=f, fill=col + (al,))
                else:
                    col = self.acc if (it["a"] and on) else (255, 255, 255)
                    if self.style == "neon" and it["a"] and on:
                        g.text((x, y), txt, font=f, fill=self.acc + (255,))
                    d.text((x, y), txt, font=f, fill=col + (al,),
                           stroke_width=max(3, px // 16), stroke_fill=(0, 0, 0, int(al * 0.85)))
                x += wpx + sp
            y += h
        sh = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sh.paste((0, 0, 0, 150), (0, 0), img.split()[3])
        sh = sh.filter(ImageFilter.GaussianBlur(8))
        out = Image.alpha_composite(sh, Image.new("RGBA", (cw, ch)))
        if self.style == "neon":
            out = Image.alpha_composite(out, glow.filter(ImageFilter.GaussianBlur(14)))
            out = Image.alpha_composite(out, glow.filter(ImageFilter.GaussianBlur(5)))
        return Image.alpha_composite(out, img)

    def render_plate(self, text, kind="plate"):
        from PIL import Image, ImageDraw, ImageFilter
        px = 66 if kind == "plate" else 60
        f = self.font(px, 800)
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if f.getlength(t) > 800 and cur:
                lines.append(cur)
                cur = w
            else:
                cur = t
        lines.append(cur)
        tw = max(f.getlength(l) for l in lines)
        padx, pady = 38, 24
        cw, ch = int(tw + padx * 2), int(len(lines) * px * 1.25 + pady * 2)
        img = Image.new("RGBA", (cw + 40, ch + 40), (0, 0, 0, 0))
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([20, 26, 20 + cw, 26 + ch], 28, fill=(0, 0, 0, 120))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(10)))
        d = ImageDraw.Draw(img)
        if kind == "cta":
            d.rounded_rectangle([20, 20, 20 + cw, 20 + ch], 28, fill=self.acc + (245,))
            lum = 0.3 * self.acc[0] + 0.59 * self.acc[1] + 0.11 * self.acc[2]
            col = (17, 17, 17) if lum > 150 else (255, 255, 255)
        else:
            d.rounded_rectangle([20, 20, 20 + cw, 20 + ch], 28, fill=(20, 20, 24, 200),
                                outline=self.acc + (255,), width=4)
            col = (255, 255, 255)
        y = 20 + pady
        for l in lines:
            d.text((20 + (cw - f.getlength(l)) / 2, y), l, font=f, fill=col + (255,))
            y += px * 1.25
        return img


# ---------------------------------------------------------------- жоспар
def chunk_words(words, max_words=4, max_chars=26):
    chunks, cur = [], []
    for i, w in enumerate(words):
        if cur:
            prev = words[i - 1]
            gap = w["s"] - prev["e"]
            nchar = sum(len(x["w"]) + 1 for x in cur)
            brk = (len(cur) >= max_words or nchar + len(w["w"]) > max_chars or gap > 0.45
                   or prev["w"][-1:] in ".?!…" or (prev["w"][-1:] in ",;:" and len(cur) >= 2)
                   or (w["a"] and len(cur) >= 2))
            if brk:
                chunks.append(cur)
                cur = []
        cur.append(dict(w))
    if cur:
        chunks.append(cur)
    # «жетім» бір сөзді алдыңғыға қосу
    out = []
    for c in chunks:
        if out and len(c) == 1 and not c[0]["a"] and len(out[-1]) < max_words and c[0]["s"] - out[-1][-1]["e"] < 0.3:
            out[-1].extend(c)
        else:
            out.append(c)
    # тым қысқа фразаны (<0.6 с) келесісімен біріктіру
    merged = []
    for c in out:
        if merged and (merged[-1][-1]["e"] - merged[-1][0]["s"]) < 0.6 and len(merged[-1]) + len(c) <= max_words + 1:
            merged[-1].extend(c)
        else:
            merged.append(c)
    out = merged
    res = []
    for k, c in enumerate(out):
        s, e = c[0]["s"], c[-1]["e"]
        nxt = out[k + 1][0]["s"] if k + 1 < len(out) else e + 0.6
        e = max(e + 0.15, s + 0.9)
        if nxt > s + 0.05:
            e = min(e, nxt - 0.02)
        res.append({"s": round(s, 3), "e": round(e, 3), "items": c})
    return res


def place_sub(geo, pt, ch):
    """Субтитр орны: иектен төмен, рамка ішінде; болмаса — бастан жоғары."""
    hd = geo.head_span(ch["s"], ch["e"])
    for scale in (1.0, 0.88, 0.76, 0.66):
        lines = pt.lines_for(ch["items"], scale)
        img = pt.render_chunk(ch["items"], lines, len(ch["items"]), scale)
        w, h = img.size
        y_def = int(ch.get("y") or 1300) - h // 2
        lo = max(SAFE_T, int(hd[3] + GAP) - 34) if hd else SAFE_T
        top = max(y_def, lo)
        if w - 68 > SUB_MAXW:
            continue
        if top + h - 34 <= SAFE_B:
            return {"scale": scale, "lines": lines, "top": top, "where": "төменде"}
        if hd:
            hi = int(hd[1] - GAP) + 34
            if hi - h >= SAFE_T - 34 + 10:
                return {"scale": scale, "lines": lines, "top": max(SAFE_T + 10 - 34, hi - h), "where": "жоғарыда"}
    lines = pt.lines_for(ch["items"], 0.76)
    h = pt.render_chunk(ch["items"], lines, len(ch["items"]), 0.76).size[1]
    return {"scale": 0.76, "lines": lines, "top": SAFE_B - h + 34, "where": "МӘСЕЛЕ", "issue": True}


def place_plate(geo, pt, el):
    img = pt.render_plate(el["text"], el.get("kind", "plate"))
    w, h = img.size
    hd = geo.head_span(el["s"], el["e"])
    top_lim = SAFE_T + 10 - 20
    if el.get("y"):
        return {"top": int(el["y"]), "w": w, "h": h}
    if not hd or hd[1] - GAP - (h - 40) >= SAFE_T + 10:
        return {"top": top_lim + 30, "w": w, "h": h}
    # бастан жоғары орын жоқ — иектен төмен
    t2 = int(hd[3] + GAP) - 20
    if t2 + h - 20 <= SAFE_B:
        return {"top": t2, "w": w, "h": h, "below": True}
    return {"top": top_lim + 30, "w": w, "h": h, "issue": True}


def free_plate_slots(geo, dur):
    slots = []
    t = 0.5
    while t < dur - 1:
        hd = geo.head_span(t, t + 2.5)
        if not hd or hd[1] - GAP - SAFE_T >= 130:
            slots.append(round(t, 1))
            t += 3
        else:
            t += 0.5
    return slots


def build_layout(info, edit):
    geo = Geo(info, edit)
    pt = Painter(edit)
    issues = []
    for i, ch in enumerate(edit["chunks"]):
        lay = place_sub(geo, pt, ch)
        ch["_lay"] = lay
        if lay.get("issue"):
            issues.append("фраза %d (%.1f с): субтитрге бетсіз орын жоқ" % (i + 1, ch["s"]))
    for el in edit.get("elements", []):
        lay = place_plate(geo, pt, el)
        el["_lay"] = lay
        if lay.get("issue"):
            issues.append("плашка «%s» (%.1f с): бастан жоғары орын жоқ — секундын ауыстыр" % (el["text"], el["s"]))
        for ch in edit["chunks"]:
            if lay.get("below") and ch["s"] < el["e"] and ch["e"] > el["s"]:
                issues.append("плашка «%s» субтитрмен қабаттасады — секундын ауыстыр" % el["text"])
                break
    return geo, pt, issues


def cmd_plan(a):
    name, wd = workdir(a.video)
    dp, ep = os.path.join(wd, "data.json"), os.path.join(wd, "edit.json")
    if not os.path.exists(dp):
        die("алдымен prep")
    info = jload(dp)
    if a.keep and os.path.exists(ep):
        edit = jload(ep)
        if a.style:
            edit["style"] = a.style
            edit["accent"] = a.accent or STYLES[a.style]["accent"]
        elif a.accent:
            edit["accent"] = a.accent
    else:
        style = a.style or "taza"
        edit = {"style": style, "accent": a.accent or STYLES[style]["accent"], "size": None,
                "base_zoom": 1.0, "progress": True, "sfx": True, "chunks": chunk_words(info["words"]),
                "pushes": [], "elements": []}
        last = 0
        for ch in edit["chunks"]:
            for it in ch["items"]:
                if it["a"] and it["s"] - last > 2.5:
                    edit["pushes"].append({"s": it["s"], "e": min(ch["e"] + 0.8, it["s"] + 2.0), "z": 0.07})
                    last = it["s"]
    # зум бетті субтитрге «итерсе» — сол зумды алып тастаймыз
    for _ in range(3):
        geo, pt, issues = build_layout(info, edit)
        bad = [p for p in edit["pushes"] if any(
            c["_lay"].get("issue") and c["s"] < p["e"] + 0.6 and c["e"] > p["s"] for c in edit["chunks"])]
        if not bad:
            break
        edit["pushes"] = [p for p in edit["pushes"] if p not in bad]
    for c in edit["chunks"]:
        c.pop("_lay", None)
    for el in edit["elements"]:
        el.pop("_lay", None)
    jsave(ep, edit)
    print("стиль: %s, түс: %s, зум саны: %d" % (edit["style"], edit["accent"], len(edit["pushes"])))
    for i, c in enumerate(edit["chunks"]):
        txt = " ".join(("*%s*" % it["w"]) if it["a"] else it["w"] for it in c["items"])
        print("%2d  %5.1f–%5.1f  %s" % (i + 1, c["s"], c["e"], txt))
    print("плашкаға бос орын (сек): " + ", ".join(str(s) for s in free_plate_slots(geo, info["dur"])[:12]))
    print("ТЕКСЕРУ: бәрі дұрыс" if not issues else "МӘСЕЛЕЛЕР:\n- " + "\n- ".join(issues))


# ---------------------------------------------------------------- кадр құрастыру
class Compositor:
    def __init__(self, info, edit):
        import numpy as np
        self.np = np
        self.info, self.edit = info, edit
        self.geo, self.pt, self.issues = build_layout(info, edit)
        self.cache = {}
        ws = [w for c in edit["chunks"] for w in c["items"]]
        self.speech_end = ws[-1]["e"] if ws else info["dur"]

    def _rgba(self, key, maker):
        if key not in self.cache:
            im = maker()
            arr = self.np.asarray(im, dtype=self.np.float32)
            self.cache[key] = (arr[:, :, :3][:, :, ::-1].copy(), arr[:, :, 3:] / 255.0)
        return self.cache[key]

    def blend(self, frame, rgb, a, x, y, alpha=1.0):
        np = self.np
        h, w = a.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        sa = a[y0 - y:y1 - y, x0 - x:x1 - x] * alpha
        reg = frame[y0:y1, x0:x1].astype(np.float32)
        frame[y0:y1, x0:x1] = (reg * (1 - sa) + rgb[y0 - y:y1 - y, x0 - x:x1 - x] * sa).astype(np.uint8)

    def overlay(self, frame, t):
        e = self.edit
        acc = hex2rgb(e.get("accent") or STYLES[e.get("style", "taza")]["accent"])
        if e.get("progress", True) and t <= self.speech_end + 0.3:
            k = clamp(t / max(0.1, self.speech_end), 0, 1)
            y = SAFE_T + 4
            frame[y:y + 6, SAFE_L:SAFE_R] = (frame[y:y + 6, SAFE_L:SAFE_R] * 0.55 + 255 * 0.45 * 0.5).astype("uint8")
            x1 = int(SAFE_L + (SAFE_R - SAFE_L) * k)
            frame[y:y + 6, SAFE_L:x1] = acc[::-1]
        hide_subs = False
        for i, el in enumerate(e.get("elements", [])):
            if el["s"] <= t <= el["e"]:
                lay = el["_lay"]
                rgb, a = self._rgba(("el", i), lambda el=el: self.pt.render_plate(el["text"], el.get("kind", "plate")))
                k = ease((t - el["s"]) / 0.25) * (1 - ease((t - el["e"] + 0.2) / 0.2))
                x = int(540 - a.shape[1] / 2)
                self.blend(frame, rgb, a, x, int(lay["top"] + (1 - k) * 24), k)
                if lay.get("below"):
                    hide_subs = True
        if hide_subs:
            return frame
        for ci, ch in enumerate(e["chunks"]):
            if ch["s"] - 0.05 <= t <= ch["e"]:
                lay = ch["_lay"]
                shown = sum(1 for it in ch["items"] if it["s"] <= t + 0.04)
                shown = max(shown, 1)
                rgb, a = self._rgba(("ch", ci, shown), lambda ch=ch, lay=lay, shown=shown: self.pt.render_chunk(
                    ch["items"], lay["lines"], shown, lay["scale"]))
                k = ease((t - ch["s"] + 0.05) / 0.15)
                x = int(SUB_CX - a.shape[1] / 2)
                self.blend(frame, rgb, a, x, int(lay["top"] + (1 - k) * 14), k)
        return frame

    def transform(self, src, t, ws, off):
        return warp(self.geo, src, t, ws, off)


def warp(geo, src, t, ws, off):
    import numpy as np, cv2
    x0, y0, S = geo.view(t)
    k = S / ws
    M = np.float32([[k, 0, -(x0 * ws - off[0]) * k], [0, k, -(y0 * ws - off[1]) * k]])
    return cv2.warpAffine(src, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def load(video):
    name, wd = workdir(video)
    dp, ep = os.path.join(wd, "data.json"), os.path.join(wd, "edit.json")
    if not os.path.exists(ep):
        die("алдымен prep және plan")
    return name, wd, jload(dp), jload(ep)


def grab(video, t, info, scale_to=None):
    import numpy as np
    w, h = info["W"], info["H"]
    if scale_to:
        w, h = scale_to
    vf = (hdr_chain() + "," if info["hdr"] else "") + "scale=%d:%d,format=bgr24" % (w, h)
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", video, "-frames:v", "1",
                          "-vf", vf, "-f", "rawvideo", "-"], stdout=subprocess.PIPE).stdout
    if len(out) < w * h * 3:
        return None
    return np.frombuffer(out[:w * h * 3], np.uint8).reshape(h, w, 3).copy()


def guides(img):
    import cv2
    ov = img.copy()
    cv2.rectangle(ov, (LOW_R, LOW_Y), (W, H), (60, 60, 230), -1)
    cv2.rectangle(ov, (0, 0), (W, SAFE_T), (60, 60, 230), -1)
    cv2.rectangle(ov, (0, SAFE_B), (W, H), (60, 60, 230), -1)
    img[:] = (img * 0.72 + ov * 0.28).astype("uint8")
    pts = [(SAFE_L, SAFE_T), (SAFE_R, SAFE_T), (SAFE_R, LOW_Y), (LOW_R, LOW_Y), (LOW_R, SAFE_B), (SAFE_L, SAFE_B)]
    import numpy as np
    cv2.polylines(img, [np.array(pts, np.int32)], True, (0, 229, 255), 4)


def sheet(frames, labels, path):
    from PIL import Image, ImageDraw
    tw, th = 270, 480
    cols = 4
    rows = (len(frames) + cols - 1) // cols
    out = Image.new("RGB", (cols * tw + (cols + 1) * 10, rows * (th + 40) + 10), (24, 24, 24))
    d = ImageDraw.Draw(out)
    from PIL import ImageFont
    f = ImageFont.truetype(FONT, 22)
    for i, (fr, lb) in enumerate(zip(frames, labels)):
        im = Image.fromarray(fr[:, :, ::-1]).resize((tw, th))
        x = 10 + (i % cols) * (tw + 10)
        y = 10 + (i // cols) * (th + 40)
        out.paste(im, (x, y))
        d.text((x + 4, y + th + 6), lb, font=f, fill=(230, 230, 230))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path, quality=88)
    return path


def cmd_sketch(a):
    name, wd, info, edit = load(a.video)
    comp = Compositor(info, edit)
    chs = edit["chunks"]
    pick = []
    if chs:
        for k in range(8):
            c = chs[min(len(chs) - 1, int(k * len(chs) / 8))]
            pick.append((c["s"] + c["e"]) / 2 + 0.25)
    for el in edit.get("elements", []):
        pick.append((el["s"] + el["e"]) / 2)
    pick = sorted(set(round(p, 2) for p in pick))[:12]
    s0 = comp.geo.s0
    ws = max(s0, min(s0 * 1.1, 1.0))
    sw, sh = int(info["W"] * ws) // 2 * 2, int(info["H"] * ws) // 2 * 2
    frames, labels = [], []
    for t in pick:
        src = grab(a.video, t, info, (sw, sh))
        if src is None:
            continue
        fr = comp.transform(src, t, ws, (0, 0))
        hd = comp.geo.head(t)
        guides(fr)
        comp.overlay(fr, t)
        if hd:
            import cv2
            cv2.rectangle(fr, (int(hd[0]), int(hd[1])), (int(hd[2]), int(hd[3])), (80, 220, 80), 3)
        frames.append(fr)
        labels.append("%.1f с" % t)
    p = sheet(frames, labels, os.path.join(DIRS["sketch"], name + "_раскадровка.jpg"))
    print("раскадровка: " + p)
    print("сары сызық = қауіпсіз рамка, жасыл = бас (жазу оған тимеуі керек), қызыл = Instagram батырмалары")
    print("ТЕКСЕРУ: бәрі дұрыс" if not comp.issues else "МӘСЕЛЕЛЕР:\n- " + "\n- ".join(comp.issues))


# ---------------------------------------------------------------- дыбыс эффектілері
def make_sfx(edit, dur, path):
    import numpy as np
    sr = 48000
    n = int((dur + 1) * sr)
    out = np.zeros(n, np.float32)
    rng = np.random.default_rng(7)

    def pop(t, vol=0.22):
        L = int(0.09 * sr)
        x = np.arange(L) / sr
        f = 1300 * np.exp(-x * 18) + 500
        s = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-x * 45) * vol
        i = int(t * sr)
        out[i:i + L] += s[:max(0, min(L, n - i))]

    def whoosh(t, vol=0.16):
        L = int(0.45 * sr)
        x = np.linspace(0, 1, L)
        noise = rng.standard_normal(L).astype(np.float32)
        k = np.exp(-((x - 0.55) ** 2) / 0.04)
        sm = np.convolve(noise, np.ones(24) / 24, "same")
        s = sm * k * vol * 3
        i = int(max(0, t - 0.25) * sr)
        out[i:i + L] += s[:max(0, min(L, n - i))]
    for ch in edit["chunks"]:
        for it in ch["items"]:
            if it["a"]:
                pop(it["s"])
    for p in edit.get("pushes", []):
        whoosh(p["s"])
    for el in edit.get("elements", []):
        whoosh(el["s"], 0.12)
        pop(el["s"] + 0.12, 0.15)
    out = np.clip(out, -0.9, 0.9)
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((out * 32767).astype(np.int16).tobytes())


# ---------------------------------------------------------------- рендер
def next_version(name):
    os.makedirs(DIRS["out"], exist_ok=True)
    v = 1
    while os.path.exists(os.path.join(DIRS["out"], "%s_v%d.mp4" % (name, v))):
        v += 1
    return os.path.join(DIRS["out"], "%s_v%d.mp4" % (name, v))


def cmd_render(a):
    import numpy as np
    name, wd, info, edit = load(a.video)
    comp = Compositor(info, edit)
    if comp.issues:
        print("ЕСКЕРТУ (рендер жалғасады):\n- " + "\n- ".join(comp.issues))
    geo = comp.geo
    s0 = geo.s0
    ws = max(s0, min(s0 * 1.1, 1.0))
    sw, sh = int(info["W"] * ws) // 2 * 2, int(info["H"] * ws) // 2 * 2
    # көлденең видео: бет жүретін аймақты ғана аламыз (жылдамырақ)
    cx = geo.cx if len(geo.cx) else np.array([info["W"] / 2])
    halfw = W / (s0 * geo.base) / 2 + 40
    cx0 = int(clamp((cx.min() - halfw) * ws, 0, sw)) // 2 * 2
    cx1 = int(clamp((cx.max() + halfw) * ws, 0, sw)) // 2 * 2
    cw = max(2, cx1 - cx0)
    vf = (hdr_chain() + "," if info["hdr"] else "") + \
        "scale=%d:%d,crop=%d:%d:%d:0,fps=%d,format=bgr24" % (sw, sh, cw, sh, cx0, FPS)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", a.video, "-vf", vf, "-f", "rawvideo", "-"],
                           stdout=subprocess.PIPE)
    out = next_version(name)
    tmp = os.path.join(wd, "video_only.mp4")
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-", "-c:v", "libx264",
                            "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
                            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", tmp],
                           stdin=subprocess.PIPE)
    n = cw * sh * 3
    i = 0
    total = int(info["dur"] * FPS)
    while True:
        buf = dec.stdout.read(n)
        if len(buf) < n:
            break
        src = np.frombuffer(buf, np.uint8).reshape(sh, cw, 3)
        t = i / FPS
        fr = comp.transform(src, t, ws, (cx0, 0))
        comp.overlay(fr, t)
        enc.stdin.write(fr.tobytes())
        i += 1
        if i % (FPS * 10) == 0:
            print("  %d/%d сек" % (i // FPS, total // FPS), flush=True)
    enc.stdin.close()
    enc.wait()
    dec.wait()
    # дыбыс: дауыс өзгеріссіз + жеңіл эффектілер
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", tmp]
    if info["audio"] and edit.get("sfx", True):
        sfx = os.path.join(wd, "sfx.wav")
        make_sfx(edit, info["dur"], sfx)
        cmd += ["-i", a.video, "-i", sfx, "-filter_complex",
                "[1:a]aresample=48000[v];[2:a]volume=0.5[s];[v][s]amix=inputs=2:normalize=0:duration=first[a]",
                "-map", "0:v", "-map", "[a]"]
    elif info["audio"]:
        cmd += ["-i", a.video, "-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out]
    run(cmd)
    mb = os.path.getsize(out) / 1e6
    if a.max_mb and mb > a.max_mb:
        br = int(a.max_mb * 8e6 * 0.93 / max(1, info["dur"])) - 192000
        tmp2 = out + ".tmp.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", out, "-c:v", "libx264", "-preset", "veryfast", "-b:v", str(br),
             "-maxrate", str(br), "-bufsize", str(br * 2), "-c:a", "copy", "-movflags", "+faststart", tmp2])
        os.replace(tmp2, out)
        mb = os.path.getsize(out) / 1e6
    print("ДАЙЫН: %s  (%.1f сек, %.1f МБ)" % (out, i / FPS, mb))


def cmd_frames(a):
    import numpy as np
    info = probe(a.video)
    name = os.path.splitext(os.path.basename(a.video))[0]
    frames, labels = [], []
    for k in range(8):
        t = info["dur"] * (k + 0.5) / 8
        fr = grab(a.video, t, info)
        if fr is not None:
            guides(fr) if (fr.shape[1], fr.shape[0]) == (W, H) else None
            frames.append(fr)
            labels.append("%.1f с" % t)
    p = sheet(frames, labels, os.path.join(DIRS["sketch"], name + "_кадрлар.jpg"))
    print("кадрлар: " + p + "  (%dx%d, %.1f сек)" % (info["W"], info["H"], info["dur"]))


# ---------------------------------------------------------------- обложка
def cmd_cover_candidates(a):
    import cv2
    name, wd, info, edit = load(a.video)
    geo = Geo(info, edit)
    cands = []
    for i, f in enumerate(info["faces"]):
        if not f:
            continue
        t = i / 4.0
        size = (f[3] - f[1]) / info["H"]
        cen = 1 - abs((f[0] + f[2]) / 2 / info["W"] - 0.5)
        speaking = any(w["s"] - 0.1 <= t <= w["e"] + 0.1 for w in info["words"])
        cands.append((size * cen * (0.6 if speaking else 1.0) + (0.2 if t > info["dur"] - 2.5 else 0), t))
    cands.sort(reverse=True)
    pick, frames, labels = [], [], []
    for sc, t in cands:
        if all(abs(t - p) > 1.5 for p in pick):
            pick.append(t)
        if len(pick) == 8:
            break
    sw, sh = int(info["W"] * geo.s0) // 2 * 2, int(info["H"] * geo.s0) // 2 * 2
    for t in sorted(pick):
        src = grab(a.video, t, info, (sw, sh))
        if src is None:
            continue
        g = cv2.Laplacian(cv2.cvtColor(src, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        frames.append(warp(geo, src, t, geo.s0, (0, 0)))
        labels.append("%.1f с%s" % (t, "" if g > 60 else " (бұлдыр)"))
    p = sheet(frames, labels, os.path.join(DIRS["sketch"], name + "_обложка_кадрлар.jpg"))
    print("обложкаға кадрлар: " + p)


def cmd_cover(a):
    import numpy as np, cv2
    from PIL import Image, ImageDraw, ImageFont
    name, wd, info, edit = load(a.video)
    e2 = dict(edit, pushes=[], base_zoom=a.zoom)
    geo = Geo(info, e2)
    pt = Painter(edit)
    sw, sh = int(info["W"] * geo.s0 * a.zoom) // 2 * 2, int(info["H"] * geo.s0 * a.zoom) // 2 * 2
    src = grab(a.video, a.t, info, (sw, sh))
    if src is None:
        die("кадр алынбады")
    fr = warp(geo, src, a.t, geo.s0 * a.zoom, (0, 0))
    img = Image.fromarray(fr[:, :, ::-1]).convert("RGBA")
    grad = np.zeros((H, W, 4), np.uint8)
    yy = np.arange(H)
    al = (clamp(1, 0, 1) * np.clip((yy - 950) / 700.0, 0, 1) ** 1.3 * 225).astype(np.uint8)
    grad[:, :, 3] = al[:, None]
    img = Image.alpha_composite(img, Image.fromarray(grad, "RGBA"))
    d = ImageDraw.Draw(img)
    parts = re.split(r"(\*[^*]+\*)", a.title)
    lines = []
    for p in parts:
        if not p.strip():
            continue
        if p.startswith("*"):
            lines.append((p.strip("*").upper(), True))
        else:
            lines.append((p.strip(), False))
    rows = []
    for txt, accent in lines:
        px = 118 if accent else 74
        f = pt.font(px, 900 if accent else 800)
        while f.getlength(txt) > 880 and px > 50:
            px -= 4
            f = pt.font(px, 900 if accent else 800)
        rows.append((txt, accent, f, px))
    total = sum(int(r[3] * 1.15) for r in rows)
    y = 1640 - total
    hd = geo.head(a.t)
    if hd and y < hd[3] + GAP:
        print("ЕСКЕРТУ: заголовок бетке жақын — --zoom кішірейт немесе басқа кадр таңда")
    if a.tag:
        f = pt.font(40, 700)
        tw = f.getlength(a.tag)
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(lay).rounded_rectangle([90, y - 92, 90 + tw + 48, y - 26], 33, fill=(0, 0, 0, 120),
                                              outline=(255, 255, 255, 170), width=2)
        img = Image.alpha_composite(img, lay)
        d = ImageDraw.Draw(img)
        d.text((114, y - 84), a.tag, font=f, fill=(255, 255, 255, 255))
    for txt, accent, f, px in rows:
        d.text((90, y), txt, font=f, fill=(pt.acc if accent else (255, 255, 255)) + (255,),
               stroke_width=0 if accent else 2, stroke_fill=(0, 0, 0, 160))
        y += int(px * 1.15)
    os.makedirs(DIRS["out"], exist_ok=True)
    v = 1
    while os.path.exists(os.path.join(DIRS["out"], "%s_обложка_v%d.png" % (name, v))):
        v += 1
    out = os.path.join(DIRS["out"], "%s_обложка_v%d.png" % (name, v))
    img.convert("RGB").save(out)
    print("обложка: " + out + "  (профиль торында 3:4 кесіледі: маңыздысы y 240–1680 ішінде)")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Reels монтаж движогы")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("check")
    sp.add_parser("setup")
    p = sp.add_parser("prep")
    p.add_argument("video")
    p.add_argument("--text")
    p.add_argument("--lang", default="kk")
    p.add_argument("--model", default="small")
    p = sp.add_parser("plan")
    p.add_argument("video")
    p.add_argument("--style", choices=list(STYLES))
    p.add_argument("--accent")
    p.add_argument("--keep", action="store_true")
    p = sp.add_parser("raskadrovka", aliases=["sketch"])
    p.add_argument("video")
    p = sp.add_parser("render")
    p.add_argument("video")
    p.add_argument("--max-mb", type=float, default=90)
    p = sp.add_parser("frames")
    p.add_argument("video")
    p = sp.add_parser("cover-candidates")
    p.add_argument("video")
    p = sp.add_parser("cover")
    p.add_argument("video")
    p.add_argument("--t", type=float, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--tag")
    p.add_argument("--zoom", type=float, default=1.0)
    a = ap.parse_args()
    {"check": cmd_check, "setup": cmd_setup, "prep": cmd_prep, "plan": cmd_plan, "sketch": cmd_sketch,
     "raskadrovka": cmd_sketch, "render": cmd_render, "frames": cmd_frames, "cover-candidates": cmd_cover_candidates,
     "cover": cmd_cover}[a.cmd](a)


if __name__ == "__main__":
    main()

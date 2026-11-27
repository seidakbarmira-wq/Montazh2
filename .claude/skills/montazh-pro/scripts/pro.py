#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""montazh-pro — движок монтажа вертикальных Reels (говорящая голова).

Работает на Mac, Linux и в облачной сессии Claude Code. Нужны: Python 3.9–3.12,
ffmpeg, pip-пакеты numpy, opencv-python-headless, pillow, mediapipe (+ faster-whisper).
`pro.py setup` ставит всё сам.

Команды (запускать из папки проекта, где лежат Бастапқы/):
  check | setup
  probe    <видео>               — длительность, fps, склейки, паузы в речи
  analyze  <видео>               — лицо, верх головы, губы, движение (по кадрам)
  words    <видео> --text t.txt  — тайминги слов (Whisper + точный текст пользователя)
  plan     <видео> --style S     — раскладка: фразы, зумы, текст за головой, проверки
  plan     <видео> --keep        — перепроверить edit.json после ручных правок
  sketches <видео>               — 8 эскизов (настоящий рендер кадров)
  render   <видео> [--max-mb 90] — финал в Дайын/<видео>_vN.mp4
  frames   <готовое.mp4>         — контрольный лист кадров
  cover-candidates <видео>       — спокойные кадры для обложки
  cover    <видео> --t С --title "…*акцент*…" [--tag Т] [--word СЛОВО]
"""
import argparse
import difflib
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(SKILL, "fonts")
MODELS = os.path.join(SKILL, "models")
W, H = 1080, 1920
SAFE_L, SAFE_T, SAFE_R, SAFE_B = 78, 222, 999, 1543
LOW_Y, LOW_R = 1194, 887            # ниже y 1194 правый край ≤ 887 (кнопки Instagram)
SUB_L, SUB_R = 193, 887             # бокс субтитров (центр X = 540)
LINE_CHARS = 19
MIN_CHUNK = 1.0
HEAD_GAP = 30                       # зазор от волос/подбородка
# облако: /opt/mzpro (root); компьютер без прав на /opt: ~/.mzpro
VENV = "/opt/mzpro" if (os.path.isdir("/opt/mzpro") or os.access("/opt", os.W_OK)) \
    else os.path.join(os.path.expanduser("~"), ".mzpro")
MODEL_URLS = {
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "selfie_multiclass.tflite": "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite",
}


def fetch_models():
    """скачать модели mediapipe, если их нет в models/ (zip-версия скила идёт без них)"""
    import urllib.request
    os.makedirs(MODELS, exist_ok=True)
    for f, url in MODEL_URLS.items():
        p = os.path.join(MODELS, f)
        if os.path.isfile(p) and os.path.getsize(p) > 100000:
            continue
        print("скачиваю модель %s …" % f)
        urllib.request.urlretrieve(url, p + ".part")
        os.replace(p + ".part", p)

STYLES = {
    # тёплый минимализм: без плашек, тень + градиент, акцент тёмно-жёлтый
    "minimal": dict(font="Montserrat-Bold", afont="Montserrat-ExtraBold", size=60, asize=96,
                    accent="#E6B43C", dim=0.22, reveal="dim", acc_mode="color", outline=0),
    # кинетика: белый текст с обводкой, акцент белым на цветной плашке с жёсткой тенью
    "kinetic": dict(font="Montserrat-ExtraBold", afont="Montserrat-Black", size=62, asize=90,
                    accent="#FF2D2D", dim=0.30, reveal="dim", acc_mode="plate", outline=3),
    # стекло: слова появляются по одному, акцент на стеклянной плашке цветом
    "glass": dict(font="Montserrat-ExtraBold", afont="Montserrat-Black", size=60, asize=88,
                  accent="#7FD3FF", dim=0.0, reveal="rise", acc_mode="glass", outline=0),
    # лайм: брутально, акцент чёрным на ярком стикере + белая offset-плашка
    "lime": dict(font="Montserrat-ExtraBold", afont="Montserrat-Black", size=62, asize=90,
                 accent="#C8FF2E", dim=0.30, reveal="dim", acc_mode="sticker", outline=3),
}


# ================================================================ утилиты
def die(msg):
    print("ОШИБКА: " + msg)
    sys.exit(1)


def run(cmd, cwd=None, capture=True):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        tail = (p.stderr or "")[-2500:] if capture else ""
        die("команда упала: %s\n%s" % (" ".join(cmd[:6]), tail))
    return p


def ffmpeg_stderr(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       encoding="utf-8", errors="replace")
    return p.stderr or ""


def project(video):
    if not os.path.isfile(video):
        die("нет файла: %s" % video)
    root = os.getcwd()
    name = os.path.splitext(os.path.basename(video))[0]
    work = os.path.join(root, ".montazh", name)
    for d in (work, os.path.join(root, "Бастапқы"), os.path.join(root, "Раскадровка"),
              os.path.join(root, "Дайын")):
        os.makedirs(d, exist_ok=True)
    return dict(root=root, name=name, work=work, src=os.path.abspath(video))


def jload(path, what):
    if not os.path.isfile(path):
        die("нет %s — сначала запусти предыдущий шаг" % what)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def jsave(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def next_version(folder, stem, ext):
    n = 0
    for p in glob.glob(os.path.join(folder, glob.escape(stem) + "_v*" + ext)):
        m = re.search(r"_v(\d+)" + re.escape(ext) + "$", p)
        if m:
            n = max(n, int(m.group(1)))
    return os.path.join(folder, "%s_v%d%s" % (stem, n + 1, ext))


def display(word):
    return re.sub(r"[.,;:]+$", "", word.strip()) or word.strip()


def norm(word):
    return re.sub(r"[^\w]", "", word.lower())


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminance(hexc):
    r, g, b = (c / 255.0 for c in hex_rgb(hexc))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


# ================================================================ окружение
NEED = ("numpy", "cv2", "PIL", "mediapipe")


def missing_modules():
    miss = []
    for m in NEED:
        try:
            __import__(m)
        except Exception:
            miss.append(m)
    return miss


def maybe_reexec():
    """если пакетов нет в текущем python, но есть готовое окружение — перезапуститься в нём"""
    if not missing_modules():
        return
    for py in (os.path.join(VENV, "bin", "python"), os.path.join(os.getcwd(), ".venv", "bin", "python")):
        if os.path.isfile(py) and os.path.realpath(py) != os.path.realpath(sys.executable):
            os.execv(py, [py] + sys.argv)


def cmd_setup(a):
    pk = ["numpy", "opencv-python-headless", "pillow", "mediapipe", "faster-whisper"]
    if shutil.which("uv"):
        print("ставлю окружение через uv (Python 3.12) в %s …" % VENV)
        r = subprocess.run(["uv", "venv", "-q", "--python", "3.12", VENV])
        if r.returncode != 0:
            die("uv venv не удался")
        r = subprocess.run(["uv", "pip", "install", "-q", "--python", os.path.join(VENV, "bin", "python")] + pk)
    else:
        print("ставлю пакеты через pip …")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q"] + pk)
        if r.returncode != 0:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--break-system-packages"] + pk)
    if r.returncode != 0:
        die("не удалось поставить пакеты: %s" % " ".join(pk))
    fetch_models()
    if not shutil.which("ffmpeg"):
        print("ffmpeg не найден. Поставь: Linux `apt-get install -y ffmpeg`, Mac `brew install ffmpeg`.")
    print("ГОТОВО: окружение поставлено")


def cmd_check(a):
    ok = True
    print("Python", sys.version.split()[0], sys.executable)
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print("НЕТ %s — `apt-get install -y ffmpeg` (Linux) / `brew install ffmpeg` (Mac)" % tool)
            ok = False
    if shutil.which("ffmpeg"):
        enc = run(["ffmpeg", "-hide_banner", "-encoders"]).stdout
        if "libx264" not in enc:
            print("ffmpeg без libx264"); ok = False
    miss = missing_modules()
    if miss:
        print("НЕТ пакетов: %s → запусти `python3 %s setup`" % (", ".join(miss), os.path.abspath(__file__)))
        ok = False
    for f in ("face_landmarker.task", "selfie_multiclass.tflite"):
        if not os.path.isfile(os.path.join(MODELS, f)):
            print("НЕТ модели %s в %s" % (f, MODELS)); ok = False
    try:
        import faster_whisper  # noqa
        print("распознавание: faster-whisper")
    except Exception:
        print("(инфо) faster-whisper не стоит — тайминги будут по паузам речи, менее точно")
    print("ГОТОВО" if ok else "НЕ ГОТОВО")


# ================================================================ probe
def cmd_probe(a):
    P = project(a.video)
    info = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", P["src"]]).stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    au = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not v:
        die("в файле нет видео")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den or 0) else 30.0
    dur = float(info["format"].get("duration") or v.get("duration") or 0)
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) in (90, 270):
        w, h = h, w
    hdr = v.get("color_transfer") in ("arib-std-b67", "smpte2084")

    err = ffmpeg_stderr(["ffmpeg", "-hide_banner", "-nostats", "-i", P["src"], "-vn", "-af",
                         "silencedetect=noise=-32dB:d=0.25", "-f", "null", "-"])
    sil, cur = [], None
    for line in err.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            cur = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur is not None:
            sil.append([round(cur, 3), round(float(m.group(1)), 3)]); cur = None
    if cur is not None:
        sil.append([round(cur, 3), round(dur, 3)])
    speech, t = [], 0.0
    for s, e in sil:
        if s - t > 0.05:
            speech.append([round(t, 3), round(s, 3)])
        t = e
    if dur - t > 0.05:
        speech.append([round(t, 3), round(dur, 3)])

    err = ffmpeg_stderr(["ffmpeg", "-hide_banner", "-nostats", "-i", P["src"], "-an", "-vf",
                         "scale=240:-2,select='gt(scene,0.33)',showinfo", "-f", "null", "-"])
    cuts = []
    for m in re.finditer(r"pts_time:([\d.]+)", err):
        c = round(float(m.group(1)), 3)
        if 0.3 < c < dur - 0.2 and (not cuts or c - cuts[-1] > 0.4):
            cuts.append(c)
    vol = ffmpeg_stderr(["ffmpeg", "-hide_banner", "-nostats", "-i", P["src"], "-vn", "-af",
                         "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume: (-?[\d.]+) dB", vol)
    mean_db = float(m.group(1)) if m else -24.0

    data = dict(source=P["src"], width=w, height=h, fps=round(fps, 3), duration=round(dur, 3),
                hdr=hdr, audio=(au or {}).get("codec_name"), has_audio=bool(au), mean_db=mean_db,
                silences=sil, speech=speech, cuts=cuts)
    jsave(os.path.join(P["work"], "probe.json"), data)
    print("видео: %dx%d, %.2f fps, %.2f с, звук: %s%s" % (w, h, fps, dur, data["audio"], ", HDR" if hdr else ""))
    print("речь: %.2f–%.2f с; пауз: %d; склеек: %d %s" % (
        speech[0][0] if speech else 0, speech[-1][1] if speech else 0, len(sil), len(cuts), cuts[:12]))
    if w / float(h) > 0.6:
        print("внимание: исходник не вертикальный — кадр будет обрезан до 9:16 по лицу")
    if min(w, h) < 1080:
        print("внимание: разрешение ниже 1080 — зумы будут мягче, лучше снимать в 1080p/4K")


# ================================================================ кадры
def base_geom(probe):
    """размер «базы» = исходник, вписанный с запасом под зум (HR) в 1080x1920"""
    sw, sh = probe["width"], probe["height"]
    fit = max(W / float(sw), H / float(sh))
    hr = 1.25 if fit <= 0.8 else 1.0      # исходник ≥ 1350 px по ширине → запас резкости под зум
    bw = int(math.ceil(sw * fit * hr / 2.0)) * 2
    bh = int(math.ceil(sh * fit * hr / 2.0)) * 2
    return dict(fit=fit, hr=hr, bw=bw, bh=bh)


def src_filters(probe, scale_w, scale_h, fps=None):
    ch = []
    if probe.get("hdr"):
        ch.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                  "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    if fps:
        ch.append("fps=%s" % fps)
    ch.append("scale=%d:%d:flags=bicubic+accurate_rnd+full_chroma_int:in_color_matrix=bt709:in_range=tv:out_range=full"
              % (scale_w, scale_h))
    ch.append("format=rgb24")
    return ",".join(ch)


def grab_frame(probe, t, w, h):
    import numpy as np
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % max(0, t), "-i", probe["source"],
                        "-frames:v", "1", "-vf", src_filters(probe, w, h), "-f", "rawvideo", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buf = p.stdout
    if len(buf) < w * h * 3:
        p = subprocess.run(["ffmpeg", "-v", "error", "-sseof", "-0.2", "-i", probe["source"],
                            "-frames:v", "1", "-vf", src_filters(probe, w, h), "-f", "rawvideo", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        buf = p.stdout
    return np.frombuffer(buf[:w * h * 3], np.uint8).reshape(h, w, 3).copy()


class Vision:
    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks.python import vision, BaseOptions
        self.mp = mp
        self.face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=os.path.join(MODELS, "face_landmarker.task")),
            num_faces=1))
        self.seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=os.path.join(MODELS, "selfie_multiclass.tflite")),
            output_category_mask=True, output_confidence_masks=True))

    def image(self, rgb):
        return self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)

    def face_info(self, rgb):
        h, w = rgb.shape[:2]
        r = self.face.detect(self.image(rgb))
        if not r.face_landmarks:
            return None
        lm = r.face_landmarks[0]
        xs = [p.x * w for p in lm]
        ys = [p.y * h for p in lm]
        up, lo, l, rr = lm[13], lm[14], lm[78], lm[308]
        lips = abs(lo.y - up.y) * h / max(1e-3, abs(rr.x - l.x) * w)
        return dict(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys), lips=lips)

    def person_alpha(self, rgb):
        """мягкая альфа человека (0..1, float32) в размере кадра"""
        import numpy as np
        import cv2
        r = self.seg.segment(self.image(rgb))
        bg = r.confidence_masks[0].numpy_view()
        a = 1.0 - bg.astype(np.float32)
        if a.ndim == 3:
            a = a[:, :, 0]
        h, w = rgb.shape[:2]
        if a.shape[:2] != (h, w):
            a = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
        return a

    def head_top(self, rgb, face):
        """верх головы (волосы) над лицом"""
        import numpy as np
        r = self.seg.segment(self.image(rgb))
        cm = r.category_mask.numpy_view()
        if cm.ndim == 3:
            cm = cm[:, :, 0]
        h, w = rgb.shape[:2]
        sy, sx = cm.shape[0] / float(h), cm.shape[1] / float(w)
        fw = face["x1"] - face["x0"]
        x0 = int(max(0, (face["x0"] + 0.1 * fw) * sx))
        x1 = int(min(cm.shape[1], (face["x1"] - 0.1 * fw) * sx))
        y1 = int(min(cm.shape[0], face["y1"] * sy))
        col = cm[:y1, x0:x1]
        hair = np.where((col == 1) | (col == 3))
        if hair[0].size == 0:
            return face["y0"] - 0.35 * (face["y1"] - face["y0"])
        return float(np.percentile(hair[0], 0.5)) / sy


# ================================================================ analyze
def cmd_analyze(a):
    import numpy as np
    import cv2
    P = project(a.video)
    probe = jload(os.path.join(P["work"], "probe.json"), "probe.json (запусти probe)")
    g = base_geom(probe)
    fw, fh = int(round(probe["width"] * g["fit"] / 2)) * 2, int(round(probe["height"] * g["fit"] / 2)) * 2
    rate = a.rate
    print("анализ лица: %d кадров (%.0f в секунду) …" % (int(probe["duration"] * rate), rate))
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", probe["source"], "-an", "-vf",
                             src_filters(probe, fw, fh, fps=rate), "-f", "rawvideo", "-"],
                            stdout=subprocess.PIPE)
    vis = Vision()
    samples, prev, i = [], None, 0
    while True:
        buf = proc.stdout.read(fw * fh * 3)
        if len(buf) < fw * fh * 3:
            break
        rgb = np.frombuffer(buf, np.uint8).reshape(fh, fw, 3)
        t = i / float(rate)
        f = vis.face_info(rgb)
        small = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (90, 160)).astype(np.float32)
        motion = float(np.mean(np.abs(small - prev))) if prev is not None else 0.0
        prev = small
        s = dict(t=round(t, 3), motion=round(motion, 2))
        if f:
            s.update({k: round(v, 1) for k, v in f.items() if k != "lips"})
            s["lips"] = round(f["lips"], 3)
            s["top"] = round(vis.head_top(rgb, f), 1)
        samples.append(s)
        i += 1
    proc.wait()
    found = sum(1 for s in samples if "x0" in s)
    jsave(os.path.join(P["work"], "faces.json"), dict(fw=fw, fh=fh, rate=rate, samples=samples))
    print("кадров: %d, лицо найдено: %d" % (len(samples), found))
    if found < 0.6 * max(1, len(samples)):
        print("внимание: лицо видно меньше чем в 60%% кадров — зумы и текст за головой будут осторожнее")


class Faces:
    """данные лица во «fit»-координатах (исходник, вписанный в 1080x1920 по cover)"""

    def __init__(self, data, probe):
        self.s = [s for s in data["samples"] if "x0" in s]
        self.all = data["samples"]
        self.fw, self.fh = data["fw"], data["fh"]
        self.dur = probe["duration"]

    def window(self, t0, t1):
        sel = [s for s in self.s if t0 - 0.13 <= s["t"] <= t1 + 0.13]
        if not sel:
            near = sorted(self.s, key=lambda s: min(abs(s["t"] - t0), abs(s["t"] - t1)))[:2]
            sel = near
        if not sel:
            return dict(cx=self.fw / 2, cy=self.fh * 0.38, top=self.fh * 0.22, chin=self.fh * 0.5,
                        x0=self.fw * 0.3, x1=self.fw * 0.7, w=self.fw * 0.4, ok=False)
        cx = sorted((s["x0"] + s["x1"]) / 2 for s in sel)[len(sel) // 2]
        cy = sorted((s["y0"] + s["y1"]) / 2 for s in sel)[len(sel) // 2]
        return dict(cx=cx, cy=cy, top=min(s["top"] for s in sel), chin=max(s["y1"] for s in sel),
                    x0=min(s["x0"] for s in sel), x1=max(s["x1"] for s in sel),
                    w=sorted(s["x1"] - s["x0"] for s in sel)[len(sel) // 2], ok=True)


# ================================================================ words
def whisper_words(src, lang, model, work, prompt=None):
    try:
        from faster_whisper import WhisperModel
        print("распознаю речь (faster-whisper, модель %s)…" % model)
        import numpy as np
        m = WhisperModel(model, device="cpu", compute_type="int8")
        # звук декодирует ffmpeg (без PyAV — у него бывают несовместимые версии)
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
                              "-f", "f32le", "-"], stdout=subprocess.PIPE).stdout
        audio = np.frombuffer(pcm, np.float32)
        # окнами по 10 с: на казахском Whisper иначе «проглатывает» куски по 10–20 с
        sr, win, pos, out = 16000, 10.0, 0.0, []
        dur = len(audio) / float(sr)
        while pos < dur - 0.3:
            seg = audio[int(pos * sr):int(min(dur, pos + win) * sr)]
            segs, _ = m.transcribe(seg, language=lang or None, word_timestamps=True, vad_filter=False,
                                   condition_on_previous_text=False, beam_size=5)
            got = [(w.word.strip(), w.start + pos, w.end + pos) for sg in segs for w in (sg.words or [])]
            last_win = pos + win >= dur
            keep = [g for g in got if g[1] < pos + win - 1.0 or last_win]
            for g in keep:
                if not out or g[1] > out[-1]["s"] + 0.02:
                    out.append(dict(w=g[0], s=float(g[1]), e=float(g[2])))
            if last_win:
                break
            pos = keep[-1][2] if keep and keep[-1][2] > pos + 1.0 else pos + win - 1.0
        return out, "faster-whisper"
    except ImportError:
        pass
    if shutil.which("whisper"):
        print("распознаю речь (whisper, модель %s)…" % model)
        cmd = ["whisper", src, "--model", model, "--word_timestamps", "True",
               "--output_format", "json", "--output_dir", work, "--fp16", "False"]
        if lang:
            cmd += ["--language", lang]
        run(cmd)
        js = os.path.join(work, os.path.splitext(os.path.basename(src))[0] + ".json")
        data = jload(js, "результата whisper")
        out = []
        for s in data.get("segments", []):
            for w in s.get("words", []) or []:
                out.append(dict(w=w["word"].strip(), s=float(w["start"]), e=float(w["end"])))
        return out, "whisper"
    return None, None


def spread(tokens, t0, t1):
    wts = [len(norm(t)) + 2 for t in tokens]
    tot = float(sum(wts)) or 1.0
    out, t = [], t0
    for wt in wts:
        d = (t1 - t0) * wt / tot
        out.append((t, t + d))
        t += d
    return out


def estimate_by_speech(tokens, speech):
    if not speech:
        die("в видео не найдено речи")
    total = sum(e - s for s, e in speech)
    wts = [len(norm(t)) + 2 for t in tokens]
    tot = float(sum(wts))
    res, acc = [], 0.0

    def real(x):
        for s, e in speech:
            if x <= e - s + 1e-9:
                return s + x, (s, e)
            x -= (e - s)
        return speech[-1][1], speech[-1]

    for wt in wts:
        d = total * wt / tot
        a, seg_a = real(acc)
        b, seg_b = real(acc + d)
        if seg_a != seg_b:
            a = seg_b[0]
        res.append((a, max(b, a + 0.08)))
        acc += d
    return res


def cmd_words(a):
    P = project(a.video)
    probe = jload(os.path.join(P["work"], "probe.json"), "probe.json (запусти probe)")
    tokens, accents = [], []
    if a.text:
        with open(a.text, encoding="utf-8") as f:
            raw = f.read()
        for tok in raw.split():
            acc = "*" in tok
            tok = tok.replace("*", "")
            if not tok.strip():
                continue
            if not norm(tok) and tokens:
                tokens[-1] += " " + tok
                continue
            tokens.append(tok); accents.append(acc)
    cache = os.path.join(P["work"], "whisper_ref.json")
    ref, method = None, None
    if not a.no_whisper:
        if os.path.isfile(cache) and not a.redo:
            c = jload(cache, "кэш")
            ref, method = c["words"], c["method"]
            print("беру распознавание из кэша (%s); --redo чтобы распознать заново" % method)
        else:
            ref, method = whisper_words(P["src"], a.lang, a.model, P["work"],
                                        prompt=" ".join(tokens) if tokens else None)
            if ref:
                jsave(cache, dict(method=method, words=ref))
    if not tokens:
        if not ref:
            die("нет текста и нет Whisper. Попроси у пользователя точный текст и передай --text")
        tokens = [r["w"] for r in ref]; accents = [False] * len(tokens)
        print("ВНИМАНИЕ: текст взят из распознавания — попроси у пользователя точный текст")
    speech = probe["speech"]
    onset = speech[0][0] if speech else 0.0
    end_speech = speech[-1][1] if speech else probe["duration"]
    if ref:
        # побуквенное сопоставление: казахское распознавание неточно, но буквы совпадают
        rch, rt = [], []
        for r in ref:
            n_ = norm(r["w"])
            for k, ch in enumerate(n_):
                rch.append(ch)
                rt.append(r["s"] + (r["e"] - r["s"]) * k / max(1, len(n_)))
        uch, uw = [], []
        for wi, t in enumerate(tokens):
            for ch in norm(t):
                uch.append(ch); uw.append(wi)
        sm = difflib.SequenceMatcher(None, "".join(uch), "".join(rch), autojunk=False)
        hit = [[] for _ in tokens]
        for blk in sm.get_matching_blocks():
            if blk.size < 2:
                continue
            for k in range(blk.size):
                hit[uw[blk.a + k]].append(rt[blk.b + k])
        times = [None] * len(tokens)
        for wi, hs in enumerate(hit):
            if len(hs) >= max(2, len(norm(tokens[wi])) // 2):
                times[wi] = (min(hs), max(hs) + 0.06)
        # монотонность: выкинуть якоря, которые идут назад во времени
        last_t = -1
        for wi in range(len(tokens)):
            if times[wi] is not None:
                if times[wi][0] < last_t:
                    times[wi] = None
                else:
                    last_t = times[wi][0]
        i = 0
        while i < len(tokens):
            if times[i] is not None:
                i += 1; continue
            j = i
            while j < len(tokens) and times[j] is None:
                j += 1
            t0 = times[i - 1][1] if i > 0 else min(onset, ref[0]["s"])
            t1 = times[j][0] if j < len(tokens) else max(end_speech, ref[-1]["e"])
            if t1 - t0 < 0.08 * (j - i):
                t1 = t0 + 0.08 * (j - i)
            for k, st in enumerate(spread(tokens[i:j], t0, t1)):
                times[i + k] = st
            i = j
        matched = sum(1 for hs, t in zip(hit, tokens) if len(hs) >= max(2, len(norm(t)) // 2))
        print("совпало с распознаванием: %d из %d слов" % (matched, len(tokens)))
    else:
        method = "паузы в речи (оценка)"
        times = estimate_by_speech(tokens, speech)
    words, last = [], 0.0
    for k, (tok, acc) in enumerate(zip(tokens, accents)):
        s, e = times[k]
        s = max(s, last)
        if k == 0:
            near = [sp[0] for sp in speech if abs(sp[0] - s) < 0.6]
            if near:
                s = near[0]
        e = max(e, s + 0.08)
        words.append(dict(w=display(tok), s=round(s, 3), e=round(e, 3), a=acc,
                          end=bool(re.search(r"[.!?…]$", tok.strip()))))
        last = s + 0.02
    jsave(os.path.join(P["work"], "words.json"), dict(method=method, words=words))
    print("слов: %d, тайминги: %s, акцентов: %d" % (len(words), method, sum(accents)))
    print("первое слово: «%s» в %.2f с" % (words[0]["w"], words[0]["s"]))


# ================================================================ шрифты и раскладка текста
_FONT_CACHE = {}


def font(name, size):
    key = (name, int(size))
    if key not in _FONT_CACHE:
        from PIL import ImageFont
        _FONT_CACHE[key] = ImageFont.truetype(os.path.join(FONTS, name + ".ttf"), int(size))
    return _FONT_CACHE[key]


def tw(text, fname, size):
    return font(fname, size).getlength(text)


def line_len(items):
    return sum(len(it["w"]) + 1 for it in items) - 1


def split_balanced(words, limit=None):
    """1 строка или 2 примерно равные; без «сироты» на второй строке, если можно"""
    limit = limit or LINE_CHARS + 3
    if len(words) <= 1:
        return [words]
    total = sum(len(w) + 1 for w in words) - 1
    if total <= LINE_CHARS:
        return [words]
    opts = []
    for k in range(1, len(words)):
        l1 = sum(len(w) + 1 for w in words[:k]) - 1
        l2 = sum(len(w) + 1 for w in words[k:]) - 1
        orphan = len(words[k:]) == 1 or len(words[:k]) == 1
        over = max(0, max(l1, l2) - limit)
        opts.append((over * 10 + (4 if orphan else 0) + max(l1, l2) + 0.3 * abs(l1 - l2), k))
    bi = min(opts)[1]
    return [words[:bi], words[bi:]]


def split_lines(items):
    normal = [it for it in items if not it.get("inl_hidden") and not (it["a"] and not it.get("inline"))]
    if not normal:
        return []
    for k, it in enumerate(normal[:-1]):
        if it.get("end"):
            return [normal[:k + 1], normal[k + 1:]]
    idx = split_balanced([it["w"] for it in normal])
    return [normal[:len(idx[0])], normal[len(idx[0]):]] if len(idx) == 2 else [normal]


def fits(items):
    if sum(1 for it in items if it["a"]) > 1:
        return False
    return all(line_len(ln) <= LINE_CHARS + 3 for ln in split_lines(items))


def chunk_layout(chunk, st, sub_y, size=None):
    """позиции слов фразы: список строк [(items, font, size, y)], акцент, верх/низ блока"""
    base = size or st["size"]
    lines = split_lines(chunk["items"])
    acc = next((it for it in chunk["items"] if it["a"] and not it.get("inline")), None)
    maxw = SUB_R - SUB_L
    sz = base
    for ln in lines:
        txt = " ".join(it["w"] for it in ln)
        while tw(txt, st["font"], sz) > maxw and sz > 34:
            sz -= 2
    lh = int(sz * 1.22)
    asz = st["asize"]
    if acc:
        while tw(acc["w"].upper() if st["acc_mode"] != "color" else acc["w"], st["afont"], asz) > maxw - 60 and asz > 40:
            asz -= 2
    gap = 22
    acc_h = int(asz * 1.15) + (36 if st["acc_mode"] in ("plate", "glass", "sticker") else 0) if acc else 0
    block_h = lh * len(lines) + (gap + acc_h if acc else 0)
    top = int(sub_y - block_h / 2)
    out_lines, y = [], top
    for ln in lines:
        f = font(st["font"], sz)
        words, x = [], None
        txt = " ".join(it["w"] for it in ln)
        width = f.getlength(txt)
        x = 540 - width / 2
        space = f.getlength(" ")
        for it in ln:
            ww = f.getlength(it["w"])
            words.append(dict(it=it, x=x, w=ww))
            x += ww + space
        out_lines.append(dict(words=words, y=y, size=sz, h=lh))
        y += lh
    accd = None
    if acc:
        y += gap
        atxt = acc["w"].upper() if st["acc_mode"] != "color" else acc["w"]
        f = font(st["afont"], asz)
        aw = f.getlength(atxt)
        accd = dict(it=acc, text=atxt, size=asz, x=540 - aw / 2, y=y, w=aw, h=acc_h)
        y += acc_h
    return dict(lines=out_lines, acc=accd, top=top, bottom=y, size=sz)


# ================================================================ камера
class Camera:
    """отображение «база» → кадр 1080x1920 с зумом по лицу, без краёв исходника"""

    def __init__(self, edit, geom, faces):
        self.e, self.g, self.f = edit, geom, faces

    def shot_at(self, t):
        for sh in self.e["shots"]:
            if sh["s"] - 1e-6 <= t < sh["e"] - 1e-6:
                return sh
        return self.e["shots"][-1]

    def zoom_at(self, t):
        sh = self.shot_at(t)
        z = sh["z"]
        for p in sh.get("push", []):
            # плавный наезд 0.9 с, держим, плавный возврат 0.9 с
            if p["s"] <= t <= p["e"] + 0.9:
                k = ease((t - p["s"]) / 0.9) if t < p["s"] + 0.9 else 1.0
                if t > p["e"]:
                    k = 1 - ease((t - p["e"]) / 0.9)
                z += (p["z"] - sh["z"]) * k
        return z

    def center(self, sh, z):
        """центр окна в fit-координатах для плана sh при зуме z (лицо по центру, без краёв)"""
        fw, fh = self.f.fw, self.f.fh
        cx, cy = sh["fx"], sh["fy"]
        # лицо в верхней половине (~на 40% высоты кадра)
        want_cy = cy + (H * 0.5 - H * 0.40) / z
        hw, hh = W / 2.0 / z, H / 2.0 / z
        cx = min(max(cx, hw), fw - hw)
        cyy = min(max(want_cy, hh), fh - hh)
        return cx, cyy

    def matrix(self, t):
        """аффинная матрица base→out"""
        sh = self.shot_at(t)
        z = self.zoom_at(t)
        cx, cy = self.center(sh, z)
        hr = self.g["hr"]
        m = z / hr
        bx, by = cx * hr, cy * hr
        return [[m, 0, W / 2.0 - bx * m], [0, m, H / 2.0 - by * m]], z, (cx, cy)

    def to_out(self, t, x, y):
        sh = self.shot_at(t)
        z = self.zoom_at(t)
        cx, cy = self.center(sh, z)
        return W / 2.0 + (x - cx) * z, H / 2.0 + (y - cy) * z


# ================================================================ plan
def cut_between(cuts, t0, t1):
    return any(t0 + 0.04 < c <= t1 + 0.04 for c in cuts)


def finalize(chunks, dur, cuts):
    for c in chunks:
        c["start"] = round(c["items"][0]["s"], 3)
    for i, c in enumerate(chunks):
        last_e = max(it["e"] for it in c["items"])
        if i + 1 < len(chunks):
            nxt = chunks[i + 1]["start"]
            c["end"] = round(nxt if nxt - last_e <= 1.2 else max(last_e + 0.6, c["start"] + MIN_CHUNK), 3)
        else:
            c["end"] = round(min(dur, last_e + 0.8), 3)
    return [i for i, c in enumerate(chunks) if c["end"] - c["start"] < MIN_CHUNK - 1e-3]


def lead_short(chunks):
    """короткая фраза появляется чуть раньше (слова ждут тусклыми) за счёт длинной предыдущей"""
    for i in range(1, len(chunks)):
        c, p = chunks[i], chunks[i - 1]
        need = MIN_CHUNK - (c["end"] - c["start"])
        if need > 1e-3 and abs(p["end"] - c["start"]) < 0.01 and (p["end"] - p["start"]) - need >= MIN_CHUNK:
            c["start"] = round(c["start"] - need, 3)
            p["end"] = c["start"]
    return [i for i, c in enumerate(chunks) if c["end"] - c["start"] < MIN_CHUNK - 1e-3]


def rebalance(chunks, dur, cuts):
    """короткой фразе «одолжить» слово у соседа (в пределах предложения)"""
    for _ in range(3 * len(chunks)):
        short = finalize(chunks, dur, cuts)
        if not short:
            break
        moved = False
        for i in short:
            x = chunks[i]
            if i > 0:
                y = chunks[i - 1]
                if len(y["items"]) > 1 and not y["items"][-2]["end"] and not y["items"][-1]["end"] \
                        and not y["items"][-1]["a"]:
                    cand_y = y["items"][:-1]
                    if cand_y and y["items"][-1]["s"] - cand_y[0]["s"] >= MIN_CHUNK and fits([y["items"][-1]] + x["items"]):
                        x["items"].insert(0, y["items"].pop()); moved = True; break
            if i + 1 < len(chunks) and not x["items"][-1]["end"]:
                y = chunks[i + 1]
                if len(y["items"]) > 1 and not y["items"][0]["a"] and y["end"] - y["items"][1]["s"] >= MIN_CHUNK \
                        and fits(x["items"] + [y["items"][0]]):
                    x["items"].append(y["items"].pop(0)); moved = True; break
        if not moved:
            break


def make_chunks(words, cuts, dur):
    items = []
    for w in words:
        it = dict(w=w["w"], s=w["s"], e=w["e"], a=w["a"], end=w.get("end", False))
        for c in cuts:
            if it["s"] < c <= it["s"] + 0.25:
                it["s"] = round(c, 3)
        items.append(it)
    segs, cur = [], []
    for it in items:
        if cur and (it["s"] - cur[-1]["e"] > 0.6 or cut_between(cuts, cur[-1]["s"], it["s"])):
            segs.append(cur); cur = []
        cur.append(it)
        if it["a"] or it["end"]:
            segs.append(cur); cur = []
    if cur:
        segs.append(cur)
    chunks = []
    for sg in segs:
        chars = sum(len(it["w"]) + 1 for it in sg if not it["a"])
        k = max(1, int(math.ceil(chars / float(2 * LINE_CHARS))))
        span = sg[-1]["e"] - sg[0]["s"]
        k_min = max(1, int(math.ceil(chars / float(2 * (LINE_CHARS + 3)))))
        while k > k_min and span / k < 1.2:
            k -= 1
        if k == 1:
            chunks.append(dict(items=sg)); continue
        target = chars / float(k)
        bounds, run_c, parts, cur = [target * j for j in range(1, k)], 0, [], []
        normals = [j for j, it in enumerate(sg) if not it["a"]]
        last_normal = max(normals) if normals else 0
        for j, it in enumerate(sg):
            cur.append(it)
            run_c += 0 if it["a"] else len(it["w"]) + 1
            if bounds and run_c >= bounds[0] - 2 and j < last_normal:
                parts.append(cur); cur = []; bounds.pop(0)
        if cur:
            parts.append(cur)
        chunks += [dict(items=p) for p in parts]

    def can_merge(x, y):
        if any(it["a"] for it in x["items"]) and any(it["a"] for it in y["items"]):
            return False
        if any(it["a"] for it in x["items"]):
            return False
        if cut_between(cuts, x["items"][-1]["s"], y["items"][0]["s"]):
            return False
        return fits(x["items"] + y["items"])

    changed = True
    while changed:
        changed = False
        for i in finalize(chunks, dur, cuts):
            if i + 1 < len(chunks) and can_merge(chunks[i], chunks[i + 1]):
                chunks[i]["items"] += chunks.pop(i + 1)["items"]; changed = True; break
            if i > 0 and can_merge(chunks[i - 1], chunks[i]):
                chunks[i - 1]["items"] += chunks.pop(i)["items"]; changed = True; break
    # 2-й проход: фраза < 1 с важнее точной смены на склейке — сливаем и через склейку
    changed = True
    while changed:
        changed = False
        for i in finalize(chunks, dur, cuts):
            x = chunks[i]
            if i + 1 < len(chunks):
                y = chunks[i + 1]
                if not (any(it["a"] for it in x["items"]) and any(it["a"] for it in y["items"])) \
                        and fits(x["items"] + y["items"]):
                    x["items"] += chunks.pop(i + 1)["items"]; changed = True; break
            if i > 0:
                y = chunks[i - 1]
                if not (any(it["a"] for it in x["items"]) and any(it["a"] for it in y["items"])) \
                        and fits(y["items"] + x["items"]):
                    y["items"] += chunks.pop(i)["items"]; changed = True; break
    rebalance(chunks, dur, cuts)
    finalize(chunks, dur, cuts)
    lead_short(chunks)
    return chunks


def plate_size(text, fname="Montserrat-ExtraBold", size=52):
    lines = split_balanced(text.split())
    lw = [tw(" ".join(l), fname, size) for l in lines]
    return int(max(lw) + 80), int(len(lines) * size * 1.25 + 52), lines


def behind_size(text):
    sz = 230
    while tw(text, "Montserrat-Black", sz) > 840 and sz > 90:
        sz -= 4
    return sz, int(tw(text, "Montserrat-Black", sz) + 70), int(sz * 0.98 + 40)


def layout_report(edit, faces, geom, fix=True):
    """проверки + автоподгонка: сабы не задевают подбородок, плашки/текст за головой не на голове"""
    st = STYLES[edit["style"]]
    cam = Camera(edit, geom, faces)
    issues = []

    def head_out(t0, t1):
        info = faces.window(t0, t1)
        worst_top, worst_chin, xs = 1e9, -1e9, []
        for k in range(5):
            t = t0 + (t1 - t0) * k / 4.0
            top = cam.to_out(t, info["cx"], info["top"])[1]
            chin = cam.to_out(t, info["cx"], info["chin"])[1]
            x0 = cam.to_out(t, info["x0"], 0)[0]
            x1 = cam.to_out(t, info["x1"], 0)[0]
            worst_top, worst_chin = min(worst_top, top), max(worst_chin, chin)
            xs += [x0, x1]
        return worst_top, worst_chin, min(xs), max(xs)

    def lower_zoom(t0, t1):
        changed = False
        for sh in edit["shots"]:
            if sh["s"] < t1 and sh["e"] > t0:
                if sh.get("push"):
                    sh["push"] = [p for p in sh["push"] if not (p["s"] < t1 + 0.9 and p["e"] + 0.9 > t0)]
                    changed = True
                if sh["z"] > 1.0:
                    sh["z"] = round(max(1.0, sh["z"] - 0.05), 3); changed = True
        return changed

    # 1) субтитры ниже подбородка
    for i, c in enumerate(edit["chunks"]):
        for _ in range(8):
            lay = chunk_layout(c, st, c.get("sub_y", edit["sub_y"]), edit.get("size"))
            top, chin, _, _ = head_out(c["start"], c["end"])
            if lay["top"] - HEAD_GAP >= chin:
                break
            if fix and lower_zoom(c["start"], c["end"]):
                continue
            room = SAFE_B - lay["bottom"]
            need = chin + HEAD_GAP - lay["top"]
            if fix and room > 0:
                c["sub_y"] = int(c.get("sub_y", edit["sub_y"]) + min(room, need)); continue
            issues.append("фраза %d: субтитры ближе %d px к подбородку" % (i + 1, HEAD_GAP))
            break
        lay = chunk_layout(c, st, c.get("sub_y", edit["sub_y"]), edit.get("size"))
        if lay["bottom"] > SAFE_B:
            issues.append("фраза %d: субтитры ниже safe zone" % (i + 1))
    # 2) плашки и текст за головой
    for el in edit.get("elements", []):
        if not el.get("text"):
            continue
        for _ in range(8):
            top, chin, hx0, hx1 = head_out(el["s"], el["e"])
            if el["kind"] == "plate":
                pw, ph, _ = plate_size(el["text"])
                y = el.get("y") or SAFE_T + 34
                if y + ph + HEAD_GAP <= top:
                    el["_y"] = int(y); break
            else:
                sz, bw, bh = behind_size(el["text"])
                # голова перекрывает низ слова на ~130 px
                y = top + 130 - bh
                if y >= SAFE_T + 30:
                    el["_y"] = int(y); break
            if fix and lower_zoom(el["s"], el["e"]):
                continue
            issues.append("%s «%s» %.1f–%.1f: не хватает места над головой — перенеси на другое время"
                          " или убери" % ("плашка" if el["kind"] == "plate" else "текст за головой",
                                           el["text"], el["s"], el["e"]))
            el["_y"] = None
            break
        if el["e"] - el["s"] < (1.5 if el["kind"] == "behind" else 1.0):
            issues.append("«%s» на экране меньше %s с" % (el["text"], "1.5" if el["kind"] == "behind" else "1"))
    for i, c in enumerate(edit["chunks"]):
        if c["end"] - c["start"] < MIN_CHUNK - 1e-3:
            issues.append("фраза %d короче 1 с" % (i + 1))
    return issues


def cmd_plan(a):
    P = project(a.video)
    probe = jload(os.path.join(P["work"], "probe.json"), "probe.json (запусти probe)")
    faces = Faces(jload(os.path.join(P["work"], "faces.json"), "faces.json (запусти analyze)"), probe)
    geom = base_geom(probe)
    ep = os.path.join(P["work"], "edit.json")
    cuts, dur = probe["cuts"], probe["duration"]
    if a.keep:
        edit = jload(ep, "edit.json")
        finalize(edit["chunks"], dur, cuts)
        lead_short(edit["chunks"])
    else:
        words = jload(os.path.join(P["work"], "words.json"), "words.json (запусти words)")["words"]
        st = STYLES[a.style]
        chunks = make_chunks(words, cuts, dur)
        zmax = 1.18 if geom["hr"] > 1 else 1.12
        # планы между склейками; зум чередуется, на длинных планах — плавные наезды на акцентах
        bounds = [0.0] + cuts + [dur]
        shots = []
        for k in range(len(bounds) - 1):
            s, e = bounds[k], bounds[k + 1]
            info = faces.window(s, e)
            shots.append(dict(s=round(s, 3), e=round(e, 3), z=[1.0, zmax][k % 2] if len(bounds) > 2 else 1.0,
                              fx=round(info["cx"], 1), fy=round(info["cy"], 1), push=[]))
        for c in chunks:
            if any(it["a"] for it in c["items"]) and c["end"] - c["start"] >= 1.8:
                for sh in shots:
                    if sh["s"] + 0.3 <= c["start"] and c["end"] <= sh["e"] - 0.3 and sh["z"] < zmax - 0.01:
                        if not sh["push"] or c["start"] - sh["push"][-1]["e"] > 2.5:
                            sh["push"].append(dict(s=round(c["start"], 2), e=round(c["end"] - 0.9, 2),
                                                   z=round(min(zmax, sh["z"] + 0.08), 3)))
        # текст за головой: акцентные слова, ~1 раз на 20 с, фраза ≥ 1.6 с
        elements, last = [], -99
        n_behind = max(1, int(dur // 20))
        for c in chunks:
            acc = next((it for it in c["items"] if it["a"]), None)
            if not acc or c["end"] - c["start"] < 1.6 or c["start"] - last < 12 or len(acc["w"]) > 12:
                continue
            if sum(1 for e in elements if e["kind"] == "behind") >= n_behind:
                break
            info = faces.window(acc["s"], max(c["end"], acc["s"] + 1.6))
            _, _, bh = behind_size(acc["w"].upper())
            top_out = H / 2.0 + (info["top"] - Camera({"shots": [dict(s=0, e=dur, z=1.0, fx=info["cx"], fy=info["cy"])]},
                                                       geom, faces).center(dict(fx=info["cx"], fy=info["cy"]), 1.0)[1])
            if top_out + 130 - bh < SAFE_T + 30:
                continue
            elements.append(dict(kind="behind", s=round(acc["s"], 2), e=round(max(c["end"], acc["s"] + 1.6), 2),
                                 text=acc["w"].upper()))
            acc["inline"] = True        # слово уже крупно за головой → в сабах обычным словом
            last = c["start"]
        k = 0
        while k < len(chunks):
            c = chunks[k]
            if not fits(c["items"]) and len(c["items"]) > 2:
                ws = [it["w"] for it in c["items"]]
                halves = split_balanced(ws)
                n1 = len(halves[0]) if len(halves) == 2 else len(ws) // 2
                # делим только если обе половины ≥ 1 с, иначе шрифт чуть уменьшится сам
                if c["items"][n1]["s"] - c["start"] >= MIN_CHUNK and c["end"] - c["items"][n1]["s"] >= MIN_CHUNK:
                    chunks[k:k + 1] = [dict(items=c["items"][:n1]), dict(items=c["items"][n1:])]
                    finalize(chunks, dur, cuts)
                    continue
            k += 1
        rebalance(chunks, dur, cuts)
        finalize(chunks, dur, cuts)
        lead_short(chunks)
        edit = dict(source=P["src"], duration=dur, fps=probe["fps"], style=a.style,
                    accent=a.accent or st["accent"], sub_y=1312, size=None, progress=True,
                    shots=shots, chunks=chunks, elements=elements)
    if a.style and a.keep and a.style != edit["style"]:
        edit["style"] = a.style
        if not a.accent:
            edit["accent"] = STYLES[a.style]["accent"]
    if a.accent:
        edit["accent"] = a.accent
    issues = layout_report(edit, faces, geom)
    jsave(ep, edit)
    # отчёт
    for i, c in enumerate(edit["chunks"]):
        txt = " ".join(("[" + it["w"] + "]") if it["a"] and not it.get("inline") else it["w"] for it in c["items"])
        print("%2d  %6.2f–%6.2f  %s" % (i + 1, c["start"], c["end"], txt))
    print("планов: %d; наездов: %d; элементов: %s" % (
        len(edit["shots"]), sum(len(s.get("push", [])) for s in edit["shots"]),
        ", ".join("%s «%s» %.1f с" % ("за головой" if e["kind"] == "behind" else "плашка", e["text"], e["s"])
                  for e in edit.get("elements", [])) or "нет"))
    # где можно поставить плашки с ключевыми мыслями
    plates = [e for e in edit.get("elements", []) if e["kind"] == "plate"]
    if not plates:
        cam = Camera(edit, geom, faces)
        slots, t = [], 2.0
        sp = probe["speech"]
        end_sp = sp[-1][1] if sp else dur
        while t < end_sp - 2.0:
            info = faces.window(t, t + 2.0)
            top = cam.to_out(t, info["cx"], info["top"])[1]
            room = top - HEAD_GAP - SAFE_T - 34
            if room >= 120:
                slots.append("%.1f (%s)" % (t, "до 2 строк" if room >= 185 else "1 строка ≤ 19 знаков")); t += 12
            else:
                t += 1
        print("места для плашек с ключевой мыслью (сек): %s" % (", ".join(slots) or "нет места над головой"))
        print("→ добавь в edit.json elements: {\"kind\":\"plate\",\"s\":С,\"e\":С+2.5,\"text\":\"2–5 слов\"} и запусти plan --keep")
    print("ПРОВЕРКА: " + ("всё ок" if not issues else "\n  - " + "\n  - ".join(issues)))


# ================================================================ рисование
class Painter:
    def __init__(self, edit, probe, faces, geom):
        import numpy as np
        self.np = np
        self.e, self.p, self.f, self.g = edit, probe, faces, geom
        self.st = STYLES[edit["style"]]
        self.acc = hex_rgb(edit["accent"])
        self.cam = Camera(edit, geom, faces)
        self.layouts = [chunk_layout(c, self.st, c.get("sub_y", edit["sub_y"]), edit.get("size"))
                        for c in edit["chunks"]]
        y0 = int(H * 0.52)
        ramp = np.zeros(H, np.float32)
        x = np.clip((np.arange(H) - y0) / float(H - y0), 0, 1)
        ramp[:] = 0.62 * x * x * (3 - 2 * x)
        self.grad = (1.0 - ramp)[:, None, None]
        self.vision = None
        self.alpha_prev = None
        sp = probe["speech"]
        self.sp0 = sp[0][0] if sp else 0.0
        self.sp1 = sp[-1][1] if sp else probe["duration"]

    # ---------- слои
    def frame(self, base, t, raw_person=False):
        import cv2
        np = self.np
        M, z, _ = self.cam.matrix(t)
        out = cv2.warpAffine(base, np.array(M, np.float32), (W, H), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REFLECT)
        clean = out
        beh = [el for el in self.e.get("elements", []) if el["kind"] == "behind" and el.get("_y") is not None
               and el["s"] - 0.05 <= t <= el["e"] + 0.35]
        img = out.astype(np.float32)
        if beh:
            if self.vision is None:
                self.vision = Vision()
            a = self.vision.person_alpha(clean)
            a = np.clip((a - 0.35) / 0.3, 0, 1)
            a = cv2.GaussianBlur(a, (0, 0), 1.2)
            if self.alpha_prev is not None and self.alpha_prev.shape == a.shape:
                a = 0.6 * a + 0.4 * self.alpha_prev
            self.alpha_prev = a
            person = img.copy()
            for el in beh:
                self.draw_behind(img, el, t)
            img = img * (1 - a[:, :, None]) + person * a[:, :, None]
        else:
            self.alpha_prev = None
        img *= self.grad
        for el in self.e.get("elements", []):
            if el["kind"] == "plate" and el.get("_y") is not None and el["s"] - 0.05 <= t <= el["e"] + 0.3:
                self.draw_plate(img, el, t)
        self.draw_subs(img, t)
        if self.e.get("progress", True) and self.sp0 <= t <= self.sp1:
            k = (t - self.sp0) / max(0.1, self.sp1 - self.sp0)
            y = SAFE_T + 6
            img[y:y + 5, SAFE_L:SAFE_R] = img[y:y + 5, SAFE_L:SAFE_R] * 0.6 + 255 * 0.4 * 0.35
            x1 = int(SAFE_L + (SAFE_R - SAFE_L) * k)
            img[y:y + 5, SAFE_L:x1] = self.acc
        return np.clip(img, 0, 255).astype(np.uint8)

    def text_layer(self, w, h):
        from PIL import Image
        return Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))

    def blend(self, img, layer, x, y):
        np = self.np
        arr = np.asarray(layer).astype(np.float32)
        h, w = arr.shape[:2]
        x, y = int(round(x)), int(round(y))
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        sub = arr[y0 - y:y1 - y, x0 - x:x1 - x]
        al = sub[:, :, 3:4] / 255.0
        img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - al) + sub[:, :, :3] * al

    def glass(self, img, x, y, w, h, r=28, tint=(20, 22, 28), alpha=1.0):
        """стекло как в iPhone: размытие фона, лёгкий белый градиент, тонкая светлая кромка"""
        import cv2
        np = self.np
        x, y, w, h = int(x), int(y), int(w), int(h)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        roi = img[y0:y1, x0:x1]
        bl = cv2.GaussianBlur(roi, (0, 0), 16)
        grad = np.linspace(0.30, 0.08, y1 - y0, dtype=np.float32)[:, None, None]
        g = bl * 0.62 + np.array(tint, np.float32) * 0.38
        g = g * (1 - grad) + 255 * grad
        mask = rounded_mask(x1 - x0, y1 - y0, r)
        rim = np.clip(mask - cv2.erode(mask, np.ones((5, 5), np.uint8)), 0, 1)
        top_hl = np.zeros_like(mask)
        top_hl[:max(2, (y1 - y0) // 3)] = np.linspace(0.18, 0, max(2, (y1 - y0) // 3))[:, None]
        m = (mask * alpha)[:, :, None]
        img[y0:y1, x0:x1] = roi * (1 - m) + g * m
        hl = np.clip(rim * 0.55 + top_hl * mask, 0, 1)[:, :, None] * alpha
        img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - hl) + 255 * hl

    def draw_text(self, layer, xy, text, fname, size, fill, alpha=1.0, outline=0, shadow=True):
        from PIL import ImageDraw
        d = ImageDraw.Draw(layer)
        f = font(fname, size)
        a = int(255 * max(0.0, min(1.0, alpha)))
        if a <= 0:
            return
        if shadow:
            d.text((xy[0], xy[1] + max(2, size // 22)), text, font=f, fill=(0, 0, 0, int(a * 0.55)))
        if outline:
            d.text(xy, text, font=f, fill=fill + (a,), stroke_width=outline, stroke_fill=(0, 0, 0, int(a * 0.85)))
        else:
            d.text(xy, text, font=f, fill=fill + (a,))

    # ---------- субтитры
    def draw_subs(self, img, t):
        st = self.st
        for c, lay in zip(self.e["chunks"], self.layouts):
            if not (c["start"] <= t < c["end"]):
                continue
            top, bottom = lay["top"] - 20, lay["bottom"] + 20
            layer = self.text_layer(W, bottom - top)
            for ln in lay["lines"]:
                for wd in ln["words"]:
                    it = wd["it"]
                    k = (t - it["s"]) / 0.14
                    if st["reveal"] == "rise":
                        al = ease(k)
                        dy = (1 - ease_out(k)) * 14
                    else:
                        al = st["dim"] + (1 - st["dim"]) * ease(k)
                        dy = 0
                    if al <= 0.01:
                        continue
                    fill = (255, 255, 255)
                    self.draw_text(layer, (wd["x"], ln["y"] - top + dy), it["w"], st["font"], ln["size"],
                                   fill, al, outline=st["outline"] if st["outline"] else 0,
                                   shadow=not st["outline"])
            acc = lay["acc"]
            if acc and t >= acc["it"]["s"] - 0.02:
                self.blend(img, layer, 0, top)
                self.draw_accent(img, acc, t)
                continue
            self.blend(img, layer, 0, top)
            if acc and st["reveal"] == "dim" and st["acc_mode"] == "color":
                # акцент «ждёт» тусклым, как остальные слова
                l2 = self.text_layer(W, acc["h"] + 30)
                self.draw_text(l2, (acc["x"], 6), acc["text"], st["afont"], acc["size"], (255, 255, 255),
                               st["dim"], shadow=True)
                self.blend(img, l2, 0, acc["y"] - 6)

    def draw_accent(self, img, acc, t):
        st = self.st
        k = ease_out((t - acc["it"]["s"]) / 0.22)
        mode = st["acc_mode"]
        accent = self.acc
        txtc = (17, 17, 17) if luminance(self.e["accent"]) > 0.6 else (255, 255, 255)
        pad_x, pad_y = 26, 14
        size = acc["size"]
        pw = acc["w"] + 2 * pad_x
        ph = int(size * 1.08) + 2 * pad_y
        px = 540 - pw / 2
        py = acc["y"]
        if mode == "color":
            layer = self.text_layer(W, acc["h"] + 40)
            sc_dy = (1 - k) * 10
            self.draw_text(layer, (acc["x"], 8 + sc_dy), acc["text"], st["afont"], size, accent, 0.35 + 0.65 * k,
                           shadow=True)
            self.blend(img, layer, 0, acc["y"] - 8)
            return
        np = self.np
        wipe = int(pw * k)
        if wipe <= 2:
            return
        if mode == "glass":
            self.glass(img, px, py, wipe, ph, r=22, alpha=1.0)
            tc = accent
        else:
            x0, y0 = int(px), int(py)
            if mode == "kinetic" or mode == "plate":
                sx, sy = x0 + 10, y0 + 10
                img[sy:sy + ph, sx:sx + wipe] = img[sy:sy + ph, sx:sx + wipe] * 0.15
                img[y0:y0 + ph, x0:x0 + wipe] = accent
                tc = txtc
            else:  # sticker: белая offset-плашка + яркий стикер
                sx, sy = x0 + 12, y0 + 12
                img[sy:sy + ph, sx:sx + wipe] = (255, 255, 255)
                img[y0:y0 + ph, x0:x0 + wipe] = accent
                tc = (17, 17, 17) if luminance(self.e["accent"]) > 0.5 else (255, 255, 255)
        layer = self.text_layer(pw, ph)
        self.draw_text(layer, (pad_x, pad_y - size * 0.06), acc["text"], st["afont"], size, tc, 1.0,
                       shadow=(mode == "glass"))
        arr = np.asarray(layer).copy()
        arr[:, wipe:, 3] = 0
        from PIL import Image
        self.blend(img, Image.fromarray(arr), px, py)

    # ---------- плашка с ключевой мыслью
    def draw_plate(self, img, el, t):
        k_in = ease_out((t - el["s"]) / 0.35)
        k_out = 1 - ease((t - el["e"]) / 0.3) if t > el["e"] else 1.0
        al = min(k_in, k_out)
        if al <= 0.01:
            return
        pw, ph, lines = plate_size(el["text"])
        x = 540 - pw / 2
        y = el["_y"] + (1 - k_in) * 18
        self.glass(img, x, y, pw, ph, r=30, alpha=al)
        layer = self.text_layer(pw, ph)
        size = 52
        for i, ln in enumerate(lines):
            txt = " ".join(ln)
            lw = tw(txt, "Montserrat-ExtraBold", size)
            self.draw_text(layer, ((pw - lw) / 2, 22 + i * size * 1.25), txt, "Montserrat-ExtraBold", size,
                           (255, 255, 255), al, shadow=True)
        self.blend(img, layer, x, y)

    # ---------- текст за головой
    def draw_behind(self, img, el, t):
        k_in = ease_out((t - el["s"]) / 0.35)
        k_out = 1 - ease((t - el["e"]) / 0.3) if t > el["e"] else 1.0
        al = min(k_in, k_out)
        if al <= 0.01:
            return
        sz, bw, bh = behind_size(el["text"])
        x = 540 - bw / 2
        y = el["_y"] + (1 - k_in) * 30
        np = self.np
        mode = self.st["acc_mode"]
        x0, y0 = int(x), int(y)
        x1, y1 = min(W, x0 + bw), min(H, y0 + bh)
        if mode == "glass" or mode == "color":
            # тёмное дымчатое стекло — слово читается на любой комнате
            self.glass(img, x0, y0, bw, bh, r=34, tint=(8, 8, 10), alpha=al)
            img[y0:y1, x0:x1] *= (1 - 0.35 * al)
            tc = hex_rgb(self.e["accent"]) if mode == "color" else (255, 255, 255)
        else:
            img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - al) + np.array(self.acc, np.float32) * al
            tc = (17, 17, 17) if luminance(self.e["accent"]) > 0.5 else (255, 255, 255)
        layer = self.text_layer(bw, bh)
        lw = tw(el["text"], "Montserrat-Black", sz)
        self.draw_text(layer, ((bw - lw) / 2, (bh - sz * 1.0) / 2 - sz * 0.08), el["text"], "Montserrat-Black", sz,
                       tc, al, shadow=False)
        self.blend(img, layer, x0, y0)


def rounded_mask(w, h, r):
    import numpy as np
    import cv2
    big = 4
    m = np.zeros((h * big, w * big), np.uint8)
    rr = r * big
    cv2.rectangle(m, (rr, 0), (w * big - rr, h * big), 255, -1)
    cv2.rectangle(m, (0, rr), (w * big, h * big - rr), 255, -1)
    for cx, cy in ((rr, rr), (w * big - rr - 1, rr), (rr, h * big - rr - 1), (w * big - rr - 1, h * big - rr - 1)):
        cv2.circle(m, (cx, cy), rr, 255, -1)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0


# ================================================================ звук (синтез мягких SFX)
def sfx_track(edit, probe, path):
    import numpy as np
    sr = 48000
    n = int(probe["duration"] * sr) + sr
    out = np.zeros(n, np.float32)
    rng = np.random.default_rng(7)

    def env(m, a=0.004, r=0.08):
        t = np.arange(m) / sr
        return np.minimum(1, t / a) * np.exp(-t / r)

    def pop():
        m = int(0.12 * sr); t = np.arange(m) / sr
        f = 520 * np.exp(-t * 9) + 260
        return np.sin(2 * np.pi * np.cumsum(f) / sr) * env(m, 0.003, 0.035)

    def tick():
        m = int(0.05 * sr)
        x = rng.standard_normal(m) * env(m, 0.0008, 0.006)
        return np.convolve(x, [1, -0.9], "same")

    def whoosh(d=0.5):
        m = int(d * sr)
        x = rng.standard_normal(m)
        out_ = np.zeros(m); lp = 0.0
        cut = np.linspace(0.02, 0.18, m)
        for i in range(m):
            lp += cut[i] * (x[i] - lp); out_[i] = lp
        w = np.sin(np.pi * np.linspace(0, 1, m)) ** 2
        return out_ * w * 3.0

    def soft():
        w_ = whoosh(0.32)
        p_ = np.concatenate([np.zeros(int(0.1 * sr)), pop(), np.zeros(len(w_))])[:len(w_)]
        return w_ * 0.8 + p_ * 0.4

    snd = dict(pop=pop(), tick=tick(), whoosh=whoosh(), soft=soft())
    for k in snd:
        snd[k] = snd[k] / (np.max(np.abs(snd[k])) + 1e-9)
    # громкость относительно голоса: заметно, но мягко
    voice = 10 ** (probe.get("mean_db", -24) / 20.0)
    gain = dict(pop=voice * 1.9, tick=voice * 0.9, whoosh=voice * 1.5, soft=voice * 1.5)
    events = []
    for c in edit["chunks"]:
        events.append(("tick", c["start"]))
        acc = next((it for it in c["items"] if it["a"] and not it.get("inline")), None)
        if acc:
            events.append(("pop", acc["s"]))
    for el in edit.get("elements", []):
        if el.get("_y") is None:
            continue
        events.append(("whoosh" if el["kind"] == "behind" else "soft", el["s"] - (0.18 if el["kind"] == "behind" else 0.05)))
    for kind, t in events:
        i = int(max(0, t) * sr)
        s = snd[kind] * gain[kind]
        j = min(n, i + len(s))
        out[i:j] += s[:j - i]
    pcm = (np.clip(out, -1, 1) * 32767).astype("<i2")
    import wave
    with wave.open(path, "wb") as wv:
        wv.setnchannels(1); wv.setsampwidth(2); wv.setframerate(sr)
        wv.writeframes(pcm.tobytes())
    return len(events)


# ================================================================ рендер
def load_all(video):
    P = project(video)
    probe = jload(os.path.join(P["work"], "probe.json"), "probe.json")
    faces = Faces(jload(os.path.join(P["work"], "faces.json"), "faces.json"), probe)
    edit = jload(os.path.join(P["work"], "edit.json"), "edit.json (запусти plan)")
    geom = base_geom(probe)
    layout_report(edit, faces, geom, fix=True)
    return P, probe, faces, edit, geom


def guides(img):
    import cv2
    cv2.rectangle(img, (SAFE_L, SAFE_T), (SAFE_R, SAFE_B), (0, 229, 255), 3)
    ov = img.copy()
    cv2.rectangle(ov, (LOW_R, LOW_Y), (SAFE_R, SAFE_B), (255, 48, 48), -1)
    img[:] = (img * 0.78 + ov * 0.22).astype(img.dtype)


def sheet(frames, labels, out, cols=4, scale=0.4):
    import numpy as np
    import cv2
    from PIL import Image, ImageDraw
    tw_, th_ = int(W * scale), int(H * scale)
    rows = int(math.ceil(len(frames) / float(cols)))
    pad = 14
    canvas = Image.new("RGB", (cols * tw_ + (cols + 1) * pad, rows * th_ + (rows + 1) * pad), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    for i, (fr, lb) in enumerate(zip(frames, labels)):
        im = Image.fromarray(cv2.resize(fr, (tw_, th_), interpolation=cv2.INTER_AREA))
        x, y = pad + (i % cols) * (tw_ + pad), pad + (i // cols) * (th_ + pad)
        canvas.paste(im, (x, y))
        f = font("Montserrat-ExtraBold", 30)
        d.rectangle([x, y, x + f.getlength(lb) + 20, y + 44], fill=(17, 17, 17))
        d.text((x + 10, y + 4), lb, font=f, fill=(255, 255, 255))
    canvas.save(out, quality=90)


def cmd_sketches(a):
    import numpy as np
    P, probe, faces, edit, geom = load_all(a.video)
    jsave(os.path.join(P["work"], "edit.json"), edit)
    pt = Painter(edit, probe, faces, geom)
    cand = []
    for el in edit.get("elements", []):
        if el.get("_y") is not None:
            cand.append((el["s"] + 0.8, "за головой" if el["kind"] == "behind" else "плашка"))
    for i, c in enumerate(edit["chunks"]):
        acc = next((it for it in c["items"] if it["a"] and not it.get("inline")), None)
        cand.append((min(c["end"] - 0.06, (acc["s"] + 0.3) if acc else c["items"][-1]["s"] + 0.3),
                     "акцент" if acc else "фраза %d" % (i + 1)))
    n_el = len([e for e in edit.get("elements", []) if e.get("_y") is not None])
    els, accs, rest = cand[:n_el], [x for x in cand[n_el:] if x[1] == "акцент"], [x for x in cand[n_el:] if x[1] != "акцент"]
    pick, used = [], []

    def take(lst, n):
        for x in lst:
            if n <= 0:
                break
            if all(abs(x[0] - u) > 1.0 for u in used):
                pick.append(x); used.append(x[0]); n -= 1

    take(els[:4] if len(els) > 4 else els, 4)
    take(accs, 2)
    step = max(1, len(rest) // max(1, 8 - len(pick)))
    take(rest[::step], 8 - len(pick))
    take(els, 8 - len(pick))
    pick = sorted(pick)[:8]
    frames, labels = [], []
    for n, (t, lb) in enumerate(pick):
        base = grab_frame(probe, t, geom["bw"], geom["bh"])
        fr = pt.frame(base, t)
        if not a.clean:
            guides(fr)
        frames.append(fr); labels.append("%d · %.1f с · %s" % (n + 1, t, lb))
        print("  %d) %.2f с — %s" % (n + 1, t, lb))
    out = os.path.join(P["root"], "Раскадровка", P["name"] + "_эскизы.jpg")
    sheet(frames, labels, out)
    print("ЭСКИЗЫ: %s" % out)
    print("голубая рамка = safe zone, красная зона = кнопки Instagram (на видео их не будет)")


def cmd_render(a):
    import numpy as np
    P, probe, faces, edit, geom = load_all(a.video)
    issues = layout_report(edit, faces, geom, fix=True)
    if issues:
        print("ВНИМАНИЕ перед рендером:\n  - " + "\n  - ".join(issues))
    fps = probe["fps"]
    out_fps = fps if fps <= 31 else fps / 2.0
    if a.fps:
        out_fps = a.fps
    nfr = int(round(probe["duration"] * out_fps))
    pt = Painter(edit, probe, faces, geom)
    bw, bh = geom["bw"], geom["bh"]
    tmp = os.path.join(P["work"], "video_only.mp4")
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", probe["source"], "-an", "-vf",
                            src_filters(probe, bw, bh, fps="%.6f" % out_fps), "-f", "rawvideo", "-"],
                           stdout=subprocess.PIPE)
    rate = []
    if a.max_mb:
        kbps = int(a.max_mb * 8 * 1024 * 0.92 / max(probe["duration"], 1)) - 256
        kbps = max(1500, min(kbps, 16000))
        rate = ["-maxrate", "%dk" % kbps, "-bufsize", "%dk" % (kbps * 2)]
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", "%dx%d" % (W, H), "-r", "%.6f" % out_fps, "-i", "-",
                            "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int,format=yuv420p",
                            "-c:v", "libx264", "-preset", a.preset, "-crf", "17"] + rate +
                           ["-profile:v", "high", "-color_primaries", "bt709", "-color_trc", "bt709",
                            "-colorspace", "bt709", "-color_range", "tv", "-movflags", "+faststart", tmp],
                           stdin=subprocess.PIPE)
    import time
    t0 = time.time()
    last = None
    for i in range(nfr):
        buf = dec.stdout.read(bw * bh * 3)
        if len(buf) == bw * bh * 3:
            last = np.frombuffer(buf, np.uint8).reshape(bh, bw, 3)
        if last is None:
            die("не удалось прочитать кадры исходника")
        t = i / out_fps
        enc.stdin.write(pt.frame(last, t).tobytes())
        if i % int(out_fps * 5) == 0 and i:
            el = time.time() - t0
            print("  %3d%%  осталось ~%d с" % (100 * i // nfr, el / i * (nfr - i)), flush=True)
    enc.stdin.close(); enc.wait(); dec.kill()
    if enc.returncode != 0:
        die("кодирование видео упало")
    sfx = os.path.join(P["work"], "sfx.wav")
    n_sfx = sfx_track(edit, probe, sfx)
    out = next_version(os.path.join(P["root"], "Дайын"), P["name"], ".mp4")
    ab = "192k" if a.max_mb else "256k"
    if probe.get("has_audio"):
        run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-i", probe["source"], "-i", sfx, "-filter_complex",
             "[1:a:0]aresample=48000[v];[2:a]aresample=48000,aformat=channel_layouts=stereo[s];"
             "[v][s]amix=inputs=2:duration=first:normalize=0[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy",
             "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1",
             "-c:a", "aac", "-b:a", ab,
             "-t", "%.3f" % probe["duration"], "-movflags", "+faststart", out])
    else:
        run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-c", "copy", "-bsf:v",
             "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1", out])
    d = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                   "default=nw=1:nk=1", out]).stdout.strip() or 0)
    mb = os.path.getsize(out) / 1048576.0
    print("ГОТОВО: %s" % out)
    print("длительность: %.2f с (исходник %.2f с)%s" % (d, probe["duration"],
                                                        "" if abs(d - probe["duration"]) < 0.15 else "  ← НЕ СОВПАДАЕТ!"))
    print("звук: голос без изменений + %d мягких эффектов; размер %.1f МБ%s; время рендера %d с" % (
        n_sfx, mb, "  ← БОЛЬШЕ ЛИМИТА!" if a.max_mb and mb > a.max_mb else "", time.time() - t0))


def cmd_frames(a):
    import numpy as np
    d = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                   "default=nw=1:nk=1", a.video]).stdout.strip() or 0)
    frames, labels = [], []
    for k in range(8):
        t = d * (k + 0.5) / 8
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", a.video, "-frames:v", "1",
                            "-vf", "scale=%d:%d,format=rgb24" % (W, H), "-f", "rawvideo", "-"],
                           stdout=subprocess.PIPE)
        frames.append(np.frombuffer(p.stdout[:W * H * 3], np.uint8).reshape(H, W, 3))
        labels.append("%.1f с" % t)
    out = os.path.splitext(a.video)[0] + "_кадры.jpg"
    root = os.getcwd()
    out = os.path.join(root, ".montazh", os.path.basename(out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet(frames, labels, out)
    print("КАДРЫ: %s" % out)


# ================================================================ обложка
def cmd_cover_candidates(a):
    import numpy as np
    P = project(a.video)
    probe = jload(os.path.join(P["work"], "probe.json"), "probe.json")
    data = jload(os.path.join(P["work"], "faces.json"), "faces.json (запусти analyze)")
    geom = base_geom(probe)
    ss = [s for s in data["samples"] if "lips" in s]
    dur = probe["duration"]
    scored = []
    for s in ss:
        sc = s["lips"] * 4 + s["motion"] / 10.0 - (0.6 if s["t"] > dur - 2.5 else 0)
        if s["lips"] < 0.12:
            scored.append((sc, s))
    scored.sort(key=lambda x: x[0])
    pick, used = [], []
    for sc, s in scored:
        if all(abs(s["t"] - u) > 1.2 for u in used):
            pick.append(s); used.append(s["t"])
        if len(pick) == 8:
            break
    if not pick:
        die("не нашёл кадров с закрытым ртом — выбери секунду сам (лучше в самом конце видео)")
    frames, labels = [], []
    for k, s in enumerate(pick):
        fr = grab_frame(probe, s["t"], data["fw"], data["fh"])
        fw = s["x1"] - s["x0"]
        import cv2
        ch = min(data["fh"], fw * 3.2)
        cw = ch * W / float(H)
        cx = (s["x0"] + s["x1"]) / 2.0
        cy = (s["top"] + s["y1"]) / 2.0 + fw * 0.35
        x0 = int(min(max(0, cx - cw / 2), data["fw"] - cw)); y0 = int(min(max(0, cy - ch / 2), data["fh"] - ch))
        crop = fr[y0:y0 + int(ch), x0:x0 + int(cw)]
        crop = cv2.resize(crop, (W, H), interpolation=cv2.INTER_AREA)
        frames.append(crop)
        labels.append("%d · %.2f с" % (k + 1, s["t"]))
    out = os.path.join(P["work"], "cover_candidates.jpg")
    sheet(frames, labels, out)
    print("КАНДИДАТЫ: %s" % out)
    for k, s in enumerate(pick):
        print("  %d) %.2f с  рот %.3f  движение %.1f" % (k + 1, s["t"], s["lips"], s["motion"]))
    print("Выбери спокойное лицо (не середина слова, без прищура) и запусти cover --t <секунда>")


def cmd_cover(a):
    import numpy as np
    import cv2
    from PIL import Image
    P = project(a.video)
    probe = jload(os.path.join(P["work"], "probe.json"), "probe.json")
    ep = os.path.join(P["work"], "edit.json")
    edit = jload(ep, "edit.json") if os.path.isfile(ep) else dict(style="minimal", accent=STYLES["minimal"]["accent"])
    st = STYLES[a.style or edit.get("style", "minimal")]
    accent = a.accent or edit.get("accent") or st["accent"]
    acc = hex_rgb(accent)
    geom = base_geom(probe)
    fw, fh = int(round(probe["width"] * geom["fit"] * geom["hr"])), int(round(probe["height"] * geom["fit"] * geom["hr"]))
    big = grab_frame(probe, a.t, fw // 2 * 2, fh // 2 * 2)
    vis = Vision()
    f = vis.face_info(cv2.resize(big, (big.shape[1] // 2, big.shape[0] // 2)))
    if not f:
        die("на этом кадре не найдено лицо — выбери другую секунду")
    for k in ("x0", "y0", "x1", "y1"):
        f[k] *= 2
    # кадрирование: средний план, лицо по центру, на ~40% высоты
    z = a.zoom
    scale = z * W / float(fw) * (fw / (probe["width"] * geom["fit"] * geom["hr"])) * geom["hr"] / geom["hr"]
    scale = z / geom["hr"]
    cx, cy = (f["x0"] + f["x1"]) / 2.0, (f["y0"] + f["y1"]) / 2.0
    hw, hh = W / 2.0 / scale, H / 2.0 / scale
    cxx = min(max(cx, hw), big.shape[1] - hw)
    cyy = min(max(cy + (H * 0.5 - H * 0.42) / scale, hh), big.shape[0] - hh)
    M = np.array([[scale, 0, W / 2.0 - cxx * scale], [0, scale, H / 2.0 - cyy * scale]], np.float32)
    fr = cv2.warpAffine(big, M, (W, H), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REFLECT)
    fo = {k: (f[k] - (cxx if k[0] == "x" else cyy)) * scale + (W / 2.0 if k[0] == "x" else H / 2.0)
          for k in ("x0", "x1", "y0", "y1")}
    alpha = vis.person_alpha(fr)
    alpha = np.clip((alpha - 0.4) / 0.25, 0, 1)
    alpha = cv2.erode(alpha, np.ones((3, 3), np.uint8))
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.5)
    head_top = vis.head_top(fr, fo)
    img = fr.astype(np.float32)
    # фон: затемнить, размыть, тонировать в цвет стиля
    bg = cv2.GaussianBlur(img, (0, 0), 7) * 0.42
    bg = bg * 0.82 + np.array(acc, np.float32) * 0.18 * 0.42
    # огромное слово за головой: выше головы / вверх-вправо, голова перекрывает низ
    word = (a.word or "").upper()
    if word:
        head_w = fo["x1"] - fo["x0"]
        target_w = min(860, head_w * 1.75)
        sz = 300
        while tw(word, "Montserrat-Black", sz) > target_w and sz > 80:
            sz -= 4
        wl = tw(word, "Montserrat-Black", sz)
        by = max(SAFE_T + 30, head_top + sz * 0.42 - sz * 1.0)
        bx = min(max(SAFE_L, (fo["x0"] + fo["x1"]) / 2 - wl / 2 + head_w * 0.25), SAFE_R - wl)
        pad = 26
        x0, y0 = int(bx - pad), int(by - 10)
        x1, y1 = int(bx + wl + pad), int(by + sz * 1.0 + 16)
        bg[y0:y1, x0:x1] = np.array(acc, np.float32)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        from PIL import ImageDraw
        tc = (17, 17, 17) if luminance(accent) > 0.5 else (255, 255, 255)
        ImageDraw.Draw(layer).text((bx, by - sz * 0.1), word, font=font("Montserrat-Black", sz), fill=tc + (255,))
        la = np.asarray(layer).astype(np.float32)
        al = la[:, :, 3:4] / 255.0
        bg = bg * (1 - al) + la[:, :, :3] * al
    out_img = bg * (1 - alpha[:, :, None]) + img * alpha[:, :, None]
    # нижний градиент
    y0g = 1000
    ramp = np.clip((np.arange(H) - y0g) / float(H - y0g), 0, 1)
    ramp = (0.88 * ramp * ramp * (3 - 2 * ramp))[:, None, None]
    out_img = out_img * (1 - ramp)
    # заголовок снизу-слева, акцент отдельной строкой ЗАГЛАВНЫМИ
    from PIL import ImageDraw
    parts = re.split(r"(\*[^*]+\*)", a.title)
    normal = " ".join(p.strip() for p in parts if p and not p.startswith("*")).strip()
    accent_txt = " ".join(p.strip("*") for p in parts if p.startswith("*")).upper()
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x = 96
    maxw = LOW_R - x
    lines = split_balanced(normal.split()) if normal else []
    sz = 74
    while lines and max(tw(" ".join(l), "Montserrat-ExtraBold", sz) for l in lines) > maxw and sz > 44:
        sz -= 2
    asz = 120
    while accent_txt and tw(accent_txt, "Montserrat-Black", asz) > maxw and asz > 50:
        asz -= 2
    total = len(lines) * sz * 1.18 + (asz * 1.1 + 10 if accent_txt else 0)
    y = min(SAFE_B, 1680) - 40 - total
    chin = fo["y1"]
    if y < chin + HEAD_GAP:
        print("внимание: заголовок близко к лицу — возьми кадр с лицом выше или текст короче")
    for l in lines:
        d.text((x, y + 3), " ".join(l), font=font("Montserrat-ExtraBold", sz), fill=(0, 0, 0, 120))
        d.text((x, y), " ".join(l), font=font("Montserrat-ExtraBold", sz), fill=(255, 255, 255, 255))
        y += sz * 1.18
    if accent_txt:
        y += 10
        d.text((x, y), accent_txt, font=font("Montserrat-Black", asz), fill=acc + (255,))
    la = np.asarray(layer).astype(np.float32)
    al = la[:, :, 3:4] / 255.0
    out_img = out_img * (1 - al) + la[:, :, :3] * al
    if a.tag:
        tag = a.tag.upper()
        tsz = 34
        tw_ = tw(tag, "Montserrat-ExtraBold", tsz)
        pt = Painter.__new__(Painter)
        pt.np = np
        pt.glass(out_img, SAFE_L + 18, SAFE_T + 40, tw_ + 52, 66, r=33)
        l2 = Image.new("RGBA", (int(tw_ + 52), 66), (0, 0, 0, 0))
        ImageDraw.Draw(l2).text((26, 12), tag, font=font("Montserrat-ExtraBold", tsz), fill=(255, 255, 255, 255))
        arr = np.asarray(l2).astype(np.float32)
        a2 = arr[:, :, 3:4] / 255.0
        ys, xs = SAFE_T + 40, SAFE_L + 18
        out_img[ys:ys + 66, xs:xs + int(tw_ + 52)] = out_img[ys:ys + 66, xs:xs + int(tw_ + 52)] * (1 - a2) + arr[:, :, :3] * a2
    res = np.clip(out_img, 0, 255).astype(np.uint8)
    out = next_version(os.path.join(P["root"], "Дайын"), P["name"] + "_обложка", ".png")
    Image.fromarray(res).save(out)
    print("ОБЛОЖКА: %s" % out)
    print("проверь: лицо спокойное, текст не на голове, всё внутри safe zone и кропа сетки 3:4 (y 240–1680)")


# ================================================================ main
def main():
    ap = argparse.ArgumentParser(description="montazh-pro")
    sp = ap.add_subparsers(dest="cmd")
    sp.add_parser("check"); sp.add_parser("setup")
    for name in ("probe", "sketches", "render", "cover-candidates"):
        p = sp.add_parser(name); p.add_argument("video")
        if name == "sketches":
            p.add_argument("--clean", action="store_true", help="без рамок safe zone")
        if name == "render":
            p.add_argument("--preset", default="medium")
            p.add_argument("--max-mb", type=float, default=None, help="лимит размера файла (облако/GitHub: 90)")
            p.add_argument("--fps", type=float, default=None)
    p = sp.add_parser("analyze"); p.add_argument("video"); p.add_argument("--rate", type=float, default=4.0)
    p = sp.add_parser("frames"); p.add_argument("video")
    p = sp.add_parser("words"); p.add_argument("video")
    p.add_argument("--text"); p.add_argument("--lang", default=None)
    p.add_argument("--model", default="small"); p.add_argument("--no-whisper", action="store_true")
    p.add_argument("--redo", action="store_true")
    p = sp.add_parser("plan"); p.add_argument("video")
    p.add_argument("--style", default=None, choices=list(STYLES))
    p.add_argument("--accent"); p.add_argument("--keep", action="store_true")
    p = sp.add_parser("cover"); p.add_argument("video")
    p.add_argument("--t", type=float, required=True); p.add_argument("--title", required=True)
    p.add_argument("--tag"); p.add_argument("--word"); p.add_argument("--accent")
    p.add_argument("--style", default=None, choices=list(STYLES)); p.add_argument("--zoom", type=float, default=1.0)
    a = ap.parse_args()
    if a.cmd == "plan" and not a.keep and not a.style:
        a.style = "minimal"
    if a.cmd not in ("setup", "check", None):
        maybe_reexec()
        miss = missing_modules()
        if miss:
            die("нет пакетов %s — запусти `python3 %s setup`" % (", ".join(miss), os.path.abspath(__file__)))
    fn = {"check": cmd_check, "setup": cmd_setup, "probe": cmd_probe, "analyze": cmd_analyze,
          "words": cmd_words, "plan": cmd_plan, "sketches": cmd_sketches, "render": cmd_render,
          "frames": cmd_frames, "cover-candidates": cmd_cover_candidates, "cover": cmd_cover}.get(a.cmd)
    if not fn:
        ap.print_help(); sys.exit(1)
    fn(a)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        maybe_reexec()
    main()

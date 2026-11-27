#!/usr/bin/env python3
"""matin.py — мәтіннен Reels видео: ИИ дауыс + анимациялы графика + субтитр (@i10_aktoty_).

Командалар (жоба түбірінен):
  voices      [--text "..."]              -> Раскадровка/дауыстар.mp4 (барлық дауыстың үлгісі)
  voice       PLAN.json                   -> дауыс + уақыт кестесі (.matin/<аты>/)
  raskadrovka PLAN.json                   -> Раскадровка/<аты>_раскадровка.jpg
  render      PLAN.json [--max-mb 90]     -> Дайын/<аты>_vN.mp4
"""
import argparse, base64, json, os, re, subprocess, sys, wave, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MZ_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "montazh-kz", "scripts")
sys.path.insert(0, MZ_DIR)
import montazh as M  # noqa: E402  (шрифт, субтитр, тексеру утилиталары ортақ)

W, H, FPS = M.W, M.H, M.FPS
PIPER_DIR = os.path.expanduser("~/.cache/matin-piper")
PIPER_MODEL = "kk_KZ-issai-high"
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/kk/kk_KZ/issai/high/"
SAMPLE = "Сәлем! Бұл менің дауысым. Телефонмен монтаж жасауды бірге үйренейік."
GAP, LEAD, TAIL = 0.35, 0.25, 1.6


# ---------------------------------------------------------------- дауыс
def piper_paths():
    os.makedirs(PIPER_DIR, exist_ok=True)
    m = os.path.join(PIPER_DIR, PIPER_MODEL + ".onnx")
    c = m + ".json"
    for path, url in ((m, PIPER_URL + PIPER_MODEL + ".onnx"), (c, PIPER_URL + PIPER_MODEL + ".onnx.json")):
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            print("дауыс моделі жүктелуде: " + os.path.basename(path))
            urllib.request.urlretrieve(url, path)
    return m, c


_PV = {}


def piper_voice():
    if "v" not in _PV:
        try:
            from piper import PiperVoice
        except Exception:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--break-system-packages", "piper-tts"])
            from piper import PiperVoice
        m, c = piper_paths()
        _PV["v"] = PiperVoice.load(m, config_path=c)
        _PV["map"] = json.load(open(c, encoding="utf-8")).get("speaker_id_map", {})
    return _PV["v"], _PV["map"]


def piper_speakers():
    _, mp = piper_voice()
    if not mp:
        return [(0, "0")]
    return sorted(((v, k) for k, v in mp.items()))


def tts_piper(text, out_wav, speaker=0, speed=1.0):
    from piper import SynthesisConfig
    v, _ = piper_voice()
    cfg = SynthesisConfig(speaker_id=int(speaker), length_scale=1.0 / float(speed or 1.0))
    with wave.open(out_wav, "wb") as wf:
        v.synthesize_wav(text, wf, syn_config=cfg)
    return None  # сөз уақыты кейін есептеледі


def tts_eleven(text, out_wav, voice_id, model="eleven_v3", speed=1.0):
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        M.die("ELEVENLABS_API_KEY жоқ — орта баптауына қосыңыз немесе piper қолданыңыз")
    body = json.dumps({"text": text, "model_id": model}).encode()
    req = urllib.request.Request("https://api.elevenlabs.io/v1/text-to-speech/%s/with-timestamps" % voice_id,
                                 data=body, headers={"xi-api-key": key, "Content-Type": "application/json"})
    j = json.loads(urllib.request.urlopen(req, timeout=180).read())
    mp3 = out_wav + ".mp3"
    open(mp3, "wb").write(base64.b64decode(j["audio_base64"]))
    af = "atempo=%.3f" % speed if speed and abs(speed - 1) > 0.01 else "anull"
    M.run(["ffmpeg", "-v", "error", "-y", "-i", mp3, "-af", af, "-ac", "1", "-ar", "24000", out_wav])
    al = j.get("alignment") or {}
    ch, st, en = al.get("characters", []), al.get("character_start_times_seconds", []), al.get("character_end_times_seconds", [])
    words, cur, s0 = [], "", None
    for c, a, b in zip(ch + [" "], st + [0], en + [0]):
        if c.isspace():
            if cur:
                words.append({"w": cur, "s": s0 / speed, "e": prev / speed})
            cur, s0 = "", None
        else:
            if s0 is None:
                s0 = a
            cur += c
            prev = b
    return words


def wav_info(path):
    with wave.open(path) as w:
        return w.getnframes() / float(w.getframerate()), w.getframerate()


def word_times(scene_words, wav_path, dur, lang="kk", heard=None):
    """Сөздердің уақыты: ElevenLabs берсе — соны, Whisper болса — соны, әйтпесе әріп санымен."""
    if heard is None:
        heard = M.whisper_words(wav_path, lang, "small")
    if heard:
        out, q = M.align(scene_words, heard)
        if q >= 0.5:
            return out
    lead, tail = 0.08, 0.12
    return M.spread_words(scene_words, [[lead, max(lead + 0.2, dur - tail)]])


def strip_stars(t):
    return t.replace("*", "")


def voice_from_file(plan, wd, vc):
    """Пайдаланушы жіберген дайын дауыс: Whisper уақыты + мәтінмен сәйкестендіру, сахналарға бөлу."""
    src = vc["file"]
    sr = 48000
    out = os.path.join(wd, "voice.wav")
    M.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-af", "adelay=%d,apad=pad_dur=%.2f" % (int(LEAD * 1000), TAIL + 0.5),
           "-ac", "1", "-ar", str(sr), out])
    dur, _ = wav_info(out)
    heard = M.whisper_words(src, plan.get("lang", "kk"), vc.get("model", "small")) or []
    user, owner = [], []
    for i, sc in enumerate(plan["scenes"]):
        tmpf = os.path.join(wd, "t.txt")
        open(tmpf, "w", encoding="utf-8").write(sc["text"])
        ws = M.read_text(tmpf)
        user += ws
        owner += [i] * len(ws)
    al, q = M.align(user, heard)
    print("мәтін мен дауыс сәйкестігі: %d%%" % (q * 100))
    scenes = []
    n = len(plan["scenes"])
    for i in range(n):
        ws = [dict(w, s=round(w["s"] + LEAD, 3), e=round(w["e"] + LEAD, 3)) for w, o in zip(al, owner) if o == i]
        scenes.append({"i": i, "s": ws[0]["s"], "speech_e": ws[-1]["e"], "words": ws})
    for i, sc in enumerate(scenes):
        nxt = scenes[i + 1]["s"] if i + 1 < n else dur
        sc["e"] = round(max(sc["speech_e"], nxt - GAP) if i + 1 < n else dur - 0.3, 3)
    M.jsave(os.path.join(wd, "timeline.json"), {"dur": round(dur, 3), "scenes": scenes})
    print("дауыс дайын (файлдан): %.1f сек, %d сахна" % (dur, n))
    for sc, tl in zip(plan["scenes"], scenes):
        print("  %5.1f–%5.1f  [%s] %s" % (tl["s"], tl["e"], sc.get("kind", "say"), strip_stars(sc["text"])[:60]))


def voice_from_file_sent(plan, wd, vc):
    """Пайдаланушының дайын дауысы (mp3/wav): Whisper арқылы сөздерді сахналарға бөлеміз."""
    src = vc["file"]
    out = os.path.join(wd, "voice.wav")
    M.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ac", "1", "-ar", "48000", "-af", "apad=pad_dur=4", out])
    dur, _ = wav_info(out)
    heard = M.whisper_words(out, plan.get("lang", "kk"), vc.get("whisper", "small")) or []
    allw, owner = [], []
    tmpf = os.path.join(wd, "t.txt")
    for i, sc in enumerate(plan["scenes"]):
        open(tmpf, "w", encoding="utf-8").write(sc["text"])
        ws = M.read_text(tmpf)
        allw += ws
        owner += [i] * len(ws)
    # сахна шекаралары: ең ұзын үзілістер (+ нүкте/сұрақ белгісі) — n-1 таңдаймыз
    n = len(plan["scenes"])
    score = []
    for k in range(len(heard) - 1):
        gap = heard[k + 1]["s"] - heard[k]["e"]
        score.append((gap + (0.6 if heard[k]["w"][-1:] in ".?!…" else 0), k))
    cuts = sorted(k for _, k in sorted(score, reverse=True)[:n - 1])
    groups, prev = [], 0
    for k in cuts + [len(heard) - 1]:
        groups.append(heard[prev:k + 1])
        prev = k + 1
    if heard and len(groups) == len(plan["scenes"]):  # сөйлем саны = сахна саны: әр сахнаны өз сөйлемімен
        timed = []
        for i, g in enumerate(groups):
            ws = [w for w, o in zip(allw, owner) if o == i]
            tw, q = M.align(ws, g)
            timed += tw
        print("сөйлемдер сахналарға сәйкес келді (%d)" % len(groups))
    elif heard:
        timed, q = M.align(allw, heard)
        print("сәйкестік: %.0f%%" % (q * 100))
    else:
        timed = M.spread_words(allw, [[0.1, dur - 0.1]])
    scenes = []
    for i, sc in enumerate(plan["scenes"]):
        ws = [w for w, o in zip(timed, owner) if o == i]
        s = max(0.0, ws[0]["s"] - 0.15)
        speech_e = ws[-1]["e"]
        scenes.append({"i": i, "s": round(s, 3), "speech_e": round(speech_e, 3), "words": ws})
    for k, scn in enumerate(scenes):  # сахна келесісі басталғанша тұрады
        nxt = scenes[k + 1]["s"] if k + 1 < len(scenes) else None
        scn["e"] = round(nxt - 0.05 if nxt else scn["speech_e"] + TAIL + 0.6, 3)
    total = scenes[-1]["e"] + 0.4
    M.jsave(os.path.join(wd, "timeline.json"), {"dur": round(total, 3), "scenes": scenes})
    print("дауыс (файл) дайын: %.1f сек, %d сахна" % (total, len(scenes)))
    for sc, tl in zip(plan["scenes"], scenes):
        print("  %5.1f–%5.1f  [%s] %s" % (tl["s"], tl["e"], sc.get("kind", "say"), strip_stars(sc["text"])[:60]))


def cmd_voice(a):
    plan, name, wd = load_plan(a.plan)
    vc = plan.get("voice", {"engine": "piper", "speaker": 0})
    eng = vc.get("engine", "piper")
    if eng == "file":
        # "path" (tech стилі) және "file"/"src" — бәрі бір мағына: дайын дауыс файлы
        if "path" in vc and "file" not in vc:
            return voice_from_file_tech(plan, wd, vc)
        if "src" in vc and "file" not in vc:
            vc = dict(vc, file=vc["src"])
        if plan.get("look") == "altyn" or vc.get("split") == "sentences":
            return voice_from_file_sent(plan, wd, vc)
        return voice_from_file(plan, wd, vc)
    t, scenes, wavs = LEAD, [], []
    for i, sc in enumerate(plan["scenes"]):
        wp = os.path.join(wd, "scene%02d.wav" % i)
        txt = strip_stars(sc.get("speak") or sc.get("tts") or sc["text"])  # speak/tts: дауысқа арналған оқылуы (Claude → Клод)
        if eng == "elevenlabs":
            heard = tts_eleven(txt, wp, vc["voice_id"], vc.get("model", "eleven_v3"), vc.get("speed", 1.0))
        else:
            heard = tts_piper(txt, wp, vc.get("speaker", 0), vc.get("speed", 1.0))
        dur, sr = wav_info(wp)
        tmpf = os.path.join(wd, "t.txt")
        open(tmpf, "w", encoding="utf-8").write(sc["text"])
        ws = word_times(M.read_text(tmpf), wp, dur, plan.get("lang", "kk"), heard)
        hold = TAIL if sc.get("kind") == "cta" else 0.0
        scenes.append({"i": i, "s": round(t, 3), "e": round(t + dur + hold, 3), "speech_e": round(t + dur, 3),
                       "words": [dict(w, s=round(w["s"] + t, 3), e=round(w["e"] + t, 3)) for w in ws]})
        wavs.append((wp, t))
        t += dur + hold + GAP
    total = t - GAP + 0.4
    # бір дауыс жолына жинау
    import numpy as np
    sr = 48000
    track = np.zeros(int(total * sr) + sr, np.float32)
    for wp, st in wavs:
        tmp = wp + ".48k.raw"
        M.run(["ffmpeg", "-v", "error", "-y", "-i", wp, "-ac", "1", "-ar", str(sr), "-f", "f32le", tmp])
        x = np.fromfile(tmp, np.float32)
        i0 = int(st * sr)
        track[i0:i0 + len(x)] += x[:max(0, len(track) - i0)]
        os.remove(tmp)
    with wave.open(os.path.join(wd, "voice.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(track, -1, 1) * 32767).astype(np.int16).tobytes())
    M.jsave(os.path.join(wd, "timeline.json"), {"dur": round(total, 3), "scenes": scenes})
    print("дауыс дайын: %.1f сек, %d сахна" % (total, len(scenes)))
    for sc, tl in zip(plan["scenes"], scenes):
        print("  %5.1f–%5.1f  [%s] %s" % (tl["s"], tl["e"], sc.get("kind", "say"), strip_stars(sc["text"])[:60]))


def voice_from_file_tech(plan, wd, vc):
    """Дайын дауыс файлы (пайдаланушы жазған немесе ElevenLabs): Whisper-мен сөздерді мәтінге байлау."""
    src = vc["path"]
    if not os.path.exists(src):
        M.die("дауыс файлы жоқ: " + src)
    wav = os.path.join(wd, "voice.wav")
    M.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-af", "adelay=%d,loudnorm=I=-15:TP=-1.5,apad=pad_dur=%.2f" % (int(LEAD * 1000), TAIL + 0.6),
           "-ac", "1", "-ar", "48000", wav])
    dur, _ = wav_info(wav)
    heard = M.whisper_words(wav, plan.get("lang", "kk"), "small") or []
    allw, owner = [], []
    for i, sc in enumerate(plan["scenes"]):
        tmpf = os.path.join(wd, "t.txt")
        open(tmpf, "w", encoding="utf-8").write(sc["text"])
        ws = M.read_text(tmpf)
        allw += ws
        owner += [i] * len(ws)
    if heard:
        al, q = M.align(allw, heard)
        print("дауыс мәтінмен сәйкестігі: %d%%" % int(q * 100))
    else:
        al = M.spread_words(allw, [[LEAD, dur - 0.2]])
    scenes = []
    for i, sc in enumerate(plan["scenes"]):
        ws = [w for w, o in zip(al, owner) if o == i]
        scenes.append({"i": i, "words": ws, "s": ws[0]["s"] - 0.12, "speech_e": ws[-1]["e"]})
    for i, x in enumerate(scenes):
        nxt = scenes[i + 1]["s"] if i + 1 < len(scenes) else None
        x["e"] = round(nxt - 0.02 if nxt else max(dur, x["speech_e"] + TAIL), 3)
        x["s"] = round(max(0.0, x["s"]), 3)
    total = scenes[-1]["e"] + 0.4
    M.jsave(os.path.join(wd, "timeline.json"), {"dur": round(total, 3), "scenes": scenes})
    print("дауыс дайын (файлдан): %.1f сек, %d сахна" % (total, len(scenes)))
    for sc, tl in zip(plan["scenes"], scenes):
        print("  %5.1f–%5.1f  [%s] %s" % (tl["s"], tl["e"], sc.get("kind", "say"), strip_stars(sc["text"])[:60]))


def pitch_hz(wav_path):
    """Дауыстың орташа биіктігі (Гц): ~165-тен жоғары — көбіне әйел дауысы."""
    import numpy as np
    with wave.open(wav_path) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    fl, hop, out = int(0.04 * sr), int(0.02 * sr), []
    lo, hi = int(sr / 400), int(sr / 70)
    for i in range(0, len(x) - fl, hop):
        f = x[i:i + fl] - x[i:i + fl].mean()
        if np.sqrt((f ** 2).mean()) < 0.02:
            continue
        ac = np.correlate(f, f, "full")[fl - 1:]
        if ac[0] <= 0:
            continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] / ac[0] > 0.45:
            out.append(sr / k)
    return float(np.median(out)) if out else 0.0


def cmd_voices(a):
    """Әр дауыстың үлгісін бір видеоға жинау: экранда «Дауыс N» жазуы."""
    os.makedirs(M.DIRS["sketch"], exist_ok=True)
    wd = os.path.join(".matin", "_voices")
    os.makedirs(wd, exist_ok=True)
    from PIL import Image, ImageDraw
    parts = []
    for sid, sname in piper_speakers():
        wp = os.path.join(wd, "v%d.wav" % sid)
        tts_piper(a.text or SAMPLE, wp, sid)
        dur, _ = wav_info(wp)
        img = Image.new("RGB", (W, H), (16, 18, 32))
        d = ImageDraw.Draw(img)
        f1, f2 = M.Painter({"style": "taza"}).font(150), M.Painter({"style": "taza"}).font(56, 600)
        lab = "Дауыс %d" % sid
        d.text(((W - f1.getlength(lab)) / 2, 760), lab, font=f1, fill=(255, 214, 10))
        d.text(((W - f2.getlength("piper · " + sname)) / 2, 960), "piper · " + sname, font=f2, fill=(200, 200, 210))
        ip = os.path.join(wd, "v%d.png" % sid)
        img.save(ip)
        vp = os.path.join(wd, "v%d.mp4" % sid)
        M.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", ip, "-i", wp, "-t", "%.2f" % (dur + 0.6),
               "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-ar", "48000", "-ac", "1", vp])
        parts.append(vp)
    lst = os.path.join(wd, "list.txt")
    open(lst, "w").write("".join("file '%s'\n" % os.path.abspath(p) for p in parts))
    out = os.path.join(M.DIRS["sketch"], "дауыстар.mp4")
    M.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    print("дауыс үлгілері: %s (%d дауыс)" % (out, len(parts)))
    info = []
    for sid, sname in piper_speakers():
        hz = pitch_hz(os.path.join(wd, "v%d.wav" % sid))
        kind = "әйел" if hz >= 165 else "ер"
        info.append({"engine": "piper", "speaker": sid, "name": sname, "pitch_hz": round(hz), "type": kind})
        print("  Дауыс %d (speaker %d, %s): ~%d Гц → %s дауысы" % (sid, sid, sname, hz, kind))
    M.jsave(os.path.join(".matin", "voices.json"), info)


# ---------------------------------------------------------------- жоспар
def load_plan(path):
    plan = M.jload(path)
    name = plan.get("name") or os.path.splitext(os.path.basename(path))[0]
    wd = os.path.join(".matin", name)
    os.makedirs(wd, exist_ok=True)
    return plan, name, wd


def load_all(path):
    plan, name, wd = load_plan(path)
    tp = os.path.join(wd, "timeline.json")
    if not os.path.exists(tp):
        M.die("алдымен: voice " + path)
    return plan, name, wd, M.jload(tp)


# ---------------------------------------------------------------- сурет салу
class Scene:
    """Бір сахнаның суреттері (кэшпен)."""

    def __init__(self, plan, sc, tl, pt):
        self.sc, self.tl, self.pt, self.plan = sc, tl, pt, plan
        self.kind = sc.get("kind", "say")
        self.items = tl["words"]
        self.chunks = M.chunk_words([dict(w) for w in self.items], 3, 20) if self.kind in ("say", "step") else []
        self.cache = {}

    def get(self, key, maker):
        if isinstance(key, tuple) and key[0] == "fit":  # бұл сурет емес, өлшем
            if key not in self.cache:
                self.cache[key] = maker()
            return self.cache[key]
        if key not in self.cache:
            import numpy as np
            im = maker()
            arr = np.asarray(im, dtype=np.float32)
            self.cache[key] = (arr[:, :, :3][:, :, ::-1].copy(), arr[:, :, 3:] / 255.0)
        return self.cache[key]


def fit_chunk(pt, items):
    """Субтитр рамкаға (ені SUB_MAXW) сыятын өлшем мен жолдарды табу."""
    for sc in (1.0, 0.9, 0.8, 0.7, 0.62):
        lines = pt.lines_for(items, sc)
        if pt.render_chunk(items, lines, len(items), sc).size[0] - 68 <= M.SUB_MAXW:
            return sc, lines
    return 0.62, pt.lines_for(items, 0.62)


def wrap_words(pt, items, px, maxw):
    f = pt.font(px)
    sp = f.getlength(" ")
    lines, cur, cw = [], [], 0
    for i, it in enumerate(items):
        w = f.getlength(M.disp(it["w"]).upper() if it["a"] else M.disp(it["w"]))
        if cur and cw + sp + w > maxw:
            lines.append(cur)
            cur, cw = [], 0
        cur.append((i, w))
        cw += (sp if len(cur) > 1 else 0) + w
    if cur:
        lines.append(cur)
    return lines, sp


def render_hook(pt, items, shown, acc):
    """Үлкен сұрақ: сөздер айтылған сайын шығады, маңызды сөз — түсті маркермен."""
    from PIL import Image, ImageDraw, ImageFilter
    px = 96
    while True:
        lines, sp = wrap_words(pt, items, px, 860)
        if len(lines) <= 5 or px <= 70:
            break
        px -= 6
    f = pt.font(px, 900)
    lh = int(px * 1.28)
    cw, ch = 940, lh * len(lines) + 60
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = 30
    for ln in lines:
        tw = sum(w for _, w in ln) + sp * (len(ln) - 1)
        x = (cw - tw) / 2
        for i, w in ln:
            it = items[i]
            txt = M.disp(it["w"]).upper() if it["a"] else M.disp(it["w"])
            on = i < shown
            if it["a"]:
                d.rounded_rectangle([x - 12, y + px * 0.08, x + w + 12, y + px * 1.16], 14,
                                    fill=acc + (255 if on else 60,))
                d.text((x, y), txt, font=f, fill=(15, 15, 20, 255 if on else 90))
            else:
                d.text((x, y), txt, font=f, fill=(255, 255, 255, 255 if on else 70))
            x += w + sp
        y += lh
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 140), (0, 0), img.split()[3])
    return Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)), img)


def render_big(pt, word, acc):
    from PIL import Image, ImageDraw
    px = 230
    f = pt.font(px, 900)
    while f.getlength(word) > 860 and px > 90:
        px -= 10
        f = pt.font(px, 900)
    w = int(f.getlength(word)) + 40
    img = Image.new("RGBA", (w, int(px * 1.3)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((20, 0), word, font=f, fill=(0, 0, 0, 0), stroke_width=4, stroke_fill=acc + (150,))
    return img


def render_step(pt, num, title, acc):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (900, 420), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([340, 20, 560, 240], fill=acc + (160,))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(28)))
    d = ImageDraw.Draw(img)
    d.ellipse([340, 20, 560, 240], fill=acc + (255,))
    f = pt.font(150, 900)
    s = str(num)
    d.text((450 - f.getlength(s) / 2, 30), s, font=f, fill=(15, 15, 20, 255))
    f2 = pt.font(76, 800)
    t = title.upper()
    while f2.getlength(t) > 880:
        f2 = pt.font(f2.size - 4, 800)
    d.text((450 - f2.getlength(t) / 2, 285), t, font=f2, fill=(255, 255, 255, 255),
           stroke_width=3, stroke_fill=(0, 0, 0, 160))
    return img


def render_card(pt, text, acc):
    from PIL import Image, ImageDraw, ImageFilter
    px = 70
    while True:
        f = pt.font(px, 800)
        words, lines, cur = strip_stars(text).split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if f.getlength(t) > 760 and cur:
                lines.append(cur)
                cur = w
            else:
                cur = t
        lines.append(cur)
        if len(lines) <= 5 or px <= 46:
            break
        px -= 4
    lh = int(px * 1.28)
    cw, ch = 880, lh * len(lines) + 90
    img = Image.new("RGBA", (cw + 60, ch + 60), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([30, 40, 30 + cw, 40 + ch], 44, fill=(0, 0, 0, 150))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(16)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([30, 30, 30 + cw, 30 + ch], 44, fill=(255, 255, 255, 34), outline=(255, 255, 255, 90), width=3)
    y = 30 + 45
    for l in lines:
        d.text((30 + (cw - f.getlength(l)) / 2, y), l, font=f, fill=(255, 255, 255, 255))
        y += lh
    return img


def render_button(pt, label, acc):
    from PIL import Image, ImageDraw, ImageFilter
    f = pt.font(66, 900)
    tw = f.getlength(label)
    bw, bh = int(tw + 120), 130
    img = Image.new("RGBA", (bw + 80, bh + 80), (0, 0, 0, 0))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(gl).rounded_rectangle([40, 40, 40 + bw, 40 + bh], 65, fill=acc + (190,))
    img = Image.alpha_composite(img, gl.filter(ImageFilter.GaussianBlur(22)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([40, 40, 40 + bw, 40 + bh], 65, fill=acc + (255,))
    lum = 0.3 * acc[0] + 0.59 * acc[1] + 0.11 * acc[2]
    col = (15, 15, 20) if lum > 150 else (255, 255, 255)
    d.text((40 + (bw - tw) / 2, 40 + (bh - 66 * 1.2) / 2), label, font=f, fill=col + (255,))
    return img


def render_arrow(acc):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (120, 110), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([48, 0, 72, 55], fill=(255, 255, 255, 255))
    d.polygon([(10, 50), (110, 50), (60, 108)], fill=(255, 255, 255, 255))
    return img


class Renderer:
    def __init__(self, plan, tl):
        import numpy as np
        self.np = np
        self.plan, self.tl = plan, tl
        th = plan.get("theme", {})
        self.acc = M.hex2rgb(th.get("accent", "#FFD60A"))
        self.bg1 = np.array(M.hex2rgb(th.get("bg1", "#0B1020"))[::-1], np.float32)
        self.bg2 = np.array(M.hex2rgb(th.get("bg2", "#2A1258"))[::-1], np.float32)
        self.pt = M.Painter({"style": th.get("style", "taza"), "accent": th.get("accent", "#FFD60A"),
                             "size": th.get("size", 88)})
        self.scenes = [Scene(plan, sc, t, self.pt) for sc, t in zip(plan["scenes"], tl["scenes"])]
        self.speech_end = tl["scenes"][-1]["speech_e"] if tl["scenes"] else tl["dur"]
        # фон: кіші өлшемде есептеп, үлкейтеміз
        self.sw, self.sh = 270, 480
        yy = np.linspace(0, 1, self.sh)[:, None, None]
        self.grad = (self.bg1 * (1 - yy) + self.bg2 * yy) * np.ones((1, self.sw, 1), np.float32)
        gy, gx = np.mgrid[0:self.sh, 0:self.sw].astype(np.float32)
        self.gx, self.gy = gx, gy
        rng = np.random.default_rng(3)
        self.grain = (rng.standard_normal((H // 2, W // 2)) * 5).astype(np.float32)
        self.grain = np.repeat(np.repeat(self.grain, 2, 0), 2, 1)[:, :, None]

    def background(self, t):
        import cv2
        np = self.np
        img = self.grad.copy()
        acc = np.array(self.acc[::-1], np.float32)
        for k, (ax, ay, sp, r, a) in enumerate(((0.32, 0.30, 0.11, 120, 0.30), (0.70, 0.72, 0.08, 150, 0.22),
                                                (0.55, 0.12, 0.05, 90, 0.14))):
            cx = self.sw * (ax + 0.18 * np.sin(t * sp * 6.28 + k))
            cy = self.sh * (ay + 0.10 * np.cos(t * sp * 5.1 + k * 2))
            g = np.exp(-((self.gx - cx) ** 2 + (self.gy - cy) ** 2) / (2 * r * r))[:, :, None] * a
            col = acc if k != 1 else (acc * 0.4 + np.array([255, 120, 80], np.float32) * 0.6)
            img = img * (1 - g) + col * g
        big = cv2.resize(img, (W, H), interpolation=cv2.INTER_CUBIC)
        return np.clip(big + self.grain, 0, 255).astype(np.uint8)

    def blend(self, frame, rgba, x, y, alpha=1.0):
        rgb, a = rgba
        M.Compositor.blend(self, frame, rgb, a, int(x), int(y), alpha)

    def scene_at(self, t):
        for k, s in enumerate(self.scenes):
            if s.tl["s"] - 0.15 <= t <= s.tl["e"] + GAP * 0.6:
                return k, s
        return None, None

    def frame(self, t):
        np = self.np
        fr = self.background(t)
        # прогресс
        k = M.clamp(t / max(0.1, self.speech_end), 0, 1)
        y = M.SAFE_T + 4
        fr[y:y + 7, M.SAFE_L:M.SAFE_R] = (fr[y:y + 7, M.SAFE_L:M.SAFE_R] * 0.6 + 255 * 0.4 * 0.45).astype(np.uint8)
        fr[y:y + 7, M.SAFE_L:int(M.SAFE_L + (M.SAFE_R - M.SAFE_L) * k)] = self.acc[::-1]
        si, s = self.scene_at(t)
        if s is None:
            return fr
        st, en = s.tl["s"], s.tl["e"]
        fin = M.ease((t - st + 0.15) / 0.35)
        fout = 1 - M.ease((t - en) / (GAP * 0.6))
        al = fin * fout
        dy = (1 - fin) * 50
        shown = max(1, sum(1 for w in s.items if w["s"] <= t + 0.04))
        acc = self.acc
        if s.kind == "hook":
            im = s.get(("hook", shown), lambda: render_hook(self.pt, s.items, shown, acc))
            hh = im[1].shape[0]
            self.blend(fr, im, (W - im[1].shape[1]) / 2, 840 - hh / 2 + dy, al)
        elif s.kind in ("say", "step"):
            if s.kind == "step":
                im = s.get("step", lambda: render_step(self.pt, s.sc.get("num", 1), s.sc.get("title", ""), acc))
                pop = M.ease((t - st) / 0.4)
                self.blend(fr, im, (W - 900) / 2, 330 + (1 - pop) * 60, al * pop)
                cy = 1130
            else:
                big = s.sc.get("big") or next((M.disp(w["w"]).upper() for w in s.items if w["a"]), None)
                if big:
                    im = s.get("big", lambda: render_big(self.pt, big, acc))
                    drift = M.clamp((t - st) / max(0.5, en - st), 0, 1) * 40
                    self.blend(fr, im, (W - im[1].shape[1]) / 2 + 20 - drift, 470, al * 0.9)
                cy = 960
            for ci, ch in enumerate(s.chunks):
                nxt = s.chunks[ci + 1]["s"] if ci + 1 < len(s.chunks) else 1e9
                if ch["s"] - 0.05 <= t < min(ch["e"] + 0.05, nxt - 0.05):
                    sc_, lines = s.get(("fit", ci), lambda ch=ch: fit_chunk(self.pt, ch["items"]))
                    sh2 = max(1, sum(1 for w in ch["items"] if w["s"] <= t + 0.04))
                    im = s.get(("ch", ci, sh2), lambda ch=ch, lines=lines, sh2=sh2, sc_=sc_: self.pt.render_chunk(
                        ch["items"], lines, sh2, sc_))
                    k2 = M.ease((t - ch["s"] + 0.05) / 0.15)
                    self.blend(fr, im, M.SUB_CX - im[1].shape[1] / 2, cy - im[1].shape[0] / 2 + (1 - k2) * 16,
                               al * k2)
        elif s.kind == "cta":
            im = s.get("card", lambda: render_card(self.pt, s.sc.get("card") or s.sc["text"], acc))
            self.blend(fr, im, (W - im[1].shape[1]) / 2, 330 + dy, al)
            top_btn = 330 + im[1].shape[0] + 30
            bt = s.get("btn", lambda: render_button(self.pt, s.sc.get("button", "Подробнее"), acc))
            pk = M.ease((t - st - 0.5) / 0.3)
            pulse = 1 + 0.035 * np.sin((t - st) * 6.0)
            rgb, a = bt
            import cv2
            if abs(pulse - 1) > 0.002:
                sz = (int(a.shape[1] * pulse), int(a.shape[0] * pulse))
                rgb = cv2.resize(rgb, sz)
                a = cv2.resize(a, sz)[:, :, None]
            top_btn = min(top_btn, 1300)
            self.blend(fr, (rgb, a), (W - a.shape[1]) / 2, top_btn + (bt[1].shape[0] - a.shape[0]) / 2, al * pk)
            ar = s.get("arrow", lambda: render_arrow(acc))
            bounce = abs(np.sin((t - st) * 4.0)) * 22
            self.blend(fr, ar, (W - 120) / 2, min(top_btn + 185, M.SAFE_B - 120) + bounce, al * pk)
        return fr


# ---------------------------------------------------------------- дыбыс эффектілері
def make_sfx(plan, tl, path):
    import numpy as np
    sr = 48000
    n = int((tl["dur"] + 1) * sr)
    out = np.zeros(n, np.float32)
    rng = np.random.default_rng(5)

    def add(i, s):
        i = max(0, int(i))
        L = min(len(s), n - i)
        if L > 0:
            out[i:i + L] += s[:L]

    def pop(t, vol=0.2):
        L = int(0.09 * sr)
        x = np.arange(L) / sr
        f = 1300 * np.exp(-x * 18) + 500
        add(t * sr, np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-x * 45) * vol)

    def whoosh(t, vol=0.14):
        L = int(0.5 * sr)
        x = np.linspace(0, 1, L)
        s = np.convolve(rng.standard_normal(L), np.ones(30) / 30, "same") * np.exp(-((x - 0.55) ** 2) / 0.04)
        add((t - 0.3) * sr, s * vol * 3)
    for sc, t in zip(plan["scenes"], tl["scenes"]):
        whoosh(t["s"])
        if sc.get("kind") == "step":
            pop(t["s"] + 0.1, 0.25)
        if sc.get("kind") == "cta":
            pop(t["s"] + 0.55, 0.25)
        for w in t["words"]:
            if w.get("a"):
                pop(w["s"], 0.16)
        for it in sc.get("items", []):
            if "_t" in it:
                pop(it["_t"], 0.24)
        if sc.get("kind") == "hook":
            kw = [w["s"] for w in t["words"] if w.get("a")]
            if kw:  # «гүрс» — төмен соққы
                L = int(0.6 * sr)
                x = np.arange(L) / sr
                add(kw[0] * sr, np.sin(2 * np.pi * (45 + 90 * np.exp(-x * 25)) * x) * np.exp(-x * 6) * 0.55)
                whoosh(kw[0], 0.2)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(out, -0.9, 0.9) * 32767).astype(np.int16).tobytes())


# ---------------------------------------------------------------- раскадровка / рендер
def make_renderer(plan, tl):
    if plan.get("look") == "altyn":
        import altyn
        return altyn.Renderer(plan, tl)
    if plan.get("theme", {}).get("style") == "kollazh":
        from kollazh import KollazhRenderer
        return KollazhRenderer(plan, tl)
    if plan.get("theme", {}).get("look") == "tech":
        import tech
        return tech.TechRenderer(plan, tl, Scene)
    return Renderer(plan, tl)


def cmd_raskadrovka(a):
    plan, name, wd, tl = load_all(a.plan)
    r = make_renderer(plan, tl)
    frames, labels = [], []
    for sc, t in zip(plan["scenes"], tl["scenes"]):
        for tt in (t["s"] + (t["speech_e"] - t["s"]) * 0.8,):
            fr = r.frame(tt)
            M.guides(fr)
            frames.append(fr)
            labels.append("%.1f с · %s" % (tt, sc.get("kind", "say")))
    p = M.sheet(frames[:12], labels[:12], os.path.join(M.DIRS["sketch"], name + "_раскадровка.jpg"))
    print("раскадровка: " + p)
    print("сары сызық = қауіпсіз рамка, қызыл = Instagram батырмалары (видеода көрінбейді)")


def cmd_render(a):
    plan, name, wd, tl = load_all(a.plan)
    r = make_renderer(plan, tl)
    tmp = os.path.join(wd, "video_only.mp4")
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "%dx%d" % (W, H),
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", tmp], stdin=subprocess.PIPE)
    total = int(tl["dur"] * FPS)
    for i in range(total):
        enc.stdin.write(r.frame(i / FPS).tobytes())
        if i and i % (FPS * 10) == 0:
            print("  %d/%d сек" % (i // FPS, total // FPS), flush=True)
    enc.stdin.close()
    enc.wait()
    sfx = os.path.join(wd, "sfx.wav")
    if plan.get("theme", {}).get("look") == "tech":
        import tech
        tech.sfx_tech(plan, tl, sfx, bed=not plan.get("music") and plan.get("bed", True))
    else:
        make_sfx(plan, tl, sfx)
    if plan.get("music") in ("none", False):
        plan.pop("music")
    elif plan.get("look") == "altyn" and not plan.get("music"):
        import altyn
        pad = os.path.join(wd, "pad.wav")
        altyn.make_pad(tl["dur"] + 1, pad)
        plan.setdefault("music_volume", 0.5)
        plan["music"] = pad
    out = M.next_version(name)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", tmp, "-i", os.path.join(wd, "voice.wav"), "-i", sfx]
    mix = "[1:a]volume=1.0[v];[2:a]volume=0.55[s]"
    ins = "[v][s]"
    nin = 2
    if plan.get("music"):
        cmd += ["-stream_loop", "-1", "-i", plan["music"]]
        mix += ";[3:a]volume=%.2f[m]" % float(plan.get("music_volume", 0.12))
        ins += "[m]"
        nin = 3
    cmd += ["-filter_complex", mix + ";%samix=inputs=%d:normalize=0:duration=first[a]" % (ins, nin),
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            "-movflags", "+faststart", out]
    M.run(cmd)
    mb = os.path.getsize(out) / 1e6
    if a.max_mb and mb > a.max_mb:
        br = int(a.max_mb * 8e6 * 0.93 / max(1, tl["dur"])) - 192000
        t2 = out + ".tmp.mp4"
        M.run(["ffmpeg", "-v", "error", "-y", "-i", out, "-c:v", "libx264", "-preset", "veryfast", "-b:v", str(br),
               "-c:a", "copy", "-movflags", "+faststart", t2])
        os.replace(t2, out)
        mb = os.path.getsize(out) / 1e6
    print("ДАЙЫН: %s  (%.1f сек, %.1f МБ)" % (out, tl["dur"], mb))


def cmd_oblozhka(a):
    plan, name, wd, tl = load_all(a.plan)
    import tech
    os.makedirs(M.DIRS["out"] if "out" in M.DIRS else "Дайын", exist_ok=True)
    out = os.path.join("Дайын", name + "_обложка.jpg")
    print("обложка: " + tech.render_cover(plan, tl, out))


def main():
    ap = argparse.ArgumentParser(description="Мәтіннен Reels видео")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("voices")
    p.add_argument("--text")
    for c in ("voice", "raskadrovka", "oblozhka"):
        p = sp.add_parser(c)
        p.add_argument("plan")
    p = sp.add_parser("render")
    p.add_argument("plan")
    p.add_argument("--max-mb", type=float, default=90)
    a = ap.parse_args()
    {"voices": cmd_voices, "voice": cmd_voice, "raskadrovka": cmd_raskadrovka, "render": cmd_render, "oblozhka": cmd_oblozhka}[a.cmd](a)


if __name__ == "__main__":
    main()

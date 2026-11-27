#!/usr/bin/env python3
"""mult.py — «мульт» стилі: беж фон, мульт кейіпкер (Мысық Мырза), жоғарыда сөзбе-сөз шығатын мәтін,
ортада анимациялы карточкалар. Дауыс — пайдаланушының дайын аудиосы.

  prep        PLAN.json            -> .matin/<аты>/data.json (сөз уақыттары + ауыз анимациясы)
  raskadrovka PLAN.json            -> Раскадровка/<аты>_раскадровка.jpg
  render      PLAN.json [--max-mb 90] -> Дайын/<аты>_vN.mp4
"""
import argparse, difflib, json, os, re, subprocess, sys, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MULT = os.path.join(os.path.dirname(HERE), "mult")
FPS = 30


def load(plan):
    P = json.load(open(plan, encoding="utf-8"))
    wd = os.path.join(".matin", P["name"]); os.makedirs(wd, exist_ok=True)
    return P, wd


def norm(s):
    return re.sub(r"[^0-9a-zа-яёәғқңөұүһі]", "", s.lower())


def transcribe(audio, wd):
    f = os.path.join(wd, "whisper_words.json")
    if os.path.exists(f):
        return json.load(open(f, encoding="utf-8"))
    from faster_whisper import WhisperModel
    w16 = os.path.join(wd, "a16.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", audio, "-ar", "16000", "-ac", "1", w16], check=True)
    wv = wave.open(w16); a = np.frombuffer(wv.readframes(wv.getnframes()), np.int16).astype(np.float32) / 32768
    segs, _ = WhisperModel("small", device="cpu", compute_type="int8").transcribe(a, language="kk", word_timestamps=True)
    W = [[x.word.strip(), x.start, x.end] for s in segs for x in s.words]
    json.dump(W, open(f, "w"), ensure_ascii=False)
    return W


def prep(plan):
    P, wd = load(plan)
    W = transcribe(P.get("audio_orig", P["audio"]), wd)
    # whisper таңбалары + уақыты
    hc, ht = [], []
    for w, s, e in W:
        n = norm(w)
        for k, ch in enumerate(n):
            hc.append(ch); ht.append(s + (e - s) * k / max(1, len(n)))
    # пайдаланушы мәтіні
    words = []
    for si, sc in enumerate(P["scenes"]):
        for m in re.finditer(r"\*([^*]+)\*([^\s*]*)|(\S+)", sc["text"]):
            if m.group(3):
                words.append({"w": m.group(3), "e": False, "s": si}); continue
            ws = m.group(1).split()
            for k, w in enumerate(ws):
                words.append({"w": w, "e": True, "s": si, "p": m.group(2) if k == len(ws) - 1 else ""})
    uc, uw = [], []
    for i, w in enumerate(words):
        for ch in norm(w["w"]):
            uc.append(ch); uw.append(i)
    sm = difflib.SequenceMatcher(None, uc, hc, autojunk=False)
    ct = [None] * len(uc)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            ct[a + k] = ht[b + k]
    # әр сөздің басталу уақыты
    for i, w in enumerate(words):
        ts = [ct[k] for k in range(len(uc)) if uw[k] == i and ct[k] is not None]
        w["t"] = min(ts) if ts else None
    # бос орындарды интерполяциялау
    known = [(i, w["t"]) for i, w in enumerate(words) if w["t"] is not None]
    for i, w in enumerate(words):
        if w["t"] is None:
            prv = max([k for k in known if k[0] < i], default=(-1, 0.0))
            nxt = min([k for k in known if k[0] > i], default=(len(words), W[-1][2]))
            w["t"] = prv[1] + (nxt[1] - prv[1]) * (i - prv[0]) / (nxt[0] - prv[0])
    for i in range(1, len(words)):
        words[i]["t"] = max(words[i]["t"], words[i - 1]["t"] + 0.05)
    # ауыз анимациясы
    wav = os.path.join(wd, "env.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", P["audio"], "-ar", "16000", "-ac", "1", wav], check=True)
    wv = wave.open(wav); a = np.frombuffer(wv.readframes(wv.getnframes()), np.int16).astype(np.float32) / 32768
    hop = 16000 // FPS
    rms = np.array([np.sqrt(np.mean(a[i:i + hop] ** 2)) for i in range(0, len(a), hop)])
    rms = rms / (np.percentile(rms, 95) + 1e-6)
    env = np.clip((rms - 0.12) / 0.8, 0, 1)
    env = np.convolve(env, [0.25, 0.5, 0.25], "same")
    dur = len(a) / 16000 + 1.0
    scenes = []
    for si, sc in enumerate(P["scenes"]):
        ws = [w for w in words if w["s"] == si]
        scenes.append({"card": sc["card"], "label": sc["label"], "pose": sc.get("pose", "talk"),
                       "t0": max(0.0, ws[0]["t"] - 0.25) if si else 0.0,
                       "words": [{"w": w["w"], "e": w["e"], "p": w.get("p", ""), "t": round(w["t"], 3)} for w in ws]})
    for si, sc in enumerate(scenes):
        sc["t1"] = scenes[si + 1]["t0"] if si + 1 < len(scenes) else dur + 1
    data = {"fps": FPS, "dur": round(dur, 3), "env": [round(float(x), 3) for x in env], "scenes": scenes}
    json.dump(data, open(os.path.join(wd, "data.json"), "w"), ensure_ascii=False)
    for sc in scenes:
        print(f"{sc['t0']:6.2f}  " + " ".join(f"{w['w']}@{w['t']:.1f}" for w in sc["words"][:6]) + " …")
    return data, wd


def node(*args, stdout=None):
    env = dict(os.environ)
    return subprocess.Popen(["node", os.path.join(MULT, "render.js"), *args], stdout=stdout, env=env)


def raskadrovka(plan):
    P, wd = load(plan)
    data, _ = prep(plan)
    ts = []
    for sc in data["scenes"]:
        t = min(sc["t1"] - 0.25, sc["words"][-1]["t"] + 0.5)
        ts.append(round(t, 2))
    fd = os.path.join(wd, "rf"); os.makedirs(fd, exist_ok=True)
    for f in os.listdir(fd): os.remove(os.path.join(fd, f))
    node(os.path.join(wd, "data.json"), "frames", ",".join(map(str, ts)), fd).wait()
    os.makedirs("Раскадровка", exist_ok=True)
    out = f"Раскадровка/{P['name']}_раскадровка.jpg"
    n = len(ts); cols = 3 if n <= 9 else 4
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(fd, "f%02d.jpg"),
                    "-vf", f"scale=360:640,pad=376:656:8:8:color=0x2b2b2b,tile={cols}x{(n + cols - 1) // cols}:color=0x2b2b2b",
                    "-frames:v", "1", "-q:v", "3", out], check=True)
    print(out)


def render(plan, max_mb):
    P, wd = load(plan)
    data, _ = prep(plan)
    os.makedirs("Дайын", exist_ok=True)
    v = 1
    while os.path.exists(f"Дайын/{P['name']}_v{v}.mp4"): v += 1
    out = f"Дайын/{P['name']}_v{v}.mp4"
    br = int(min(12000, max_mb * 8 * 1024 * 0.92 / data["dur"] - 192))
    pr = node(os.path.join(wd, "data.json"), "video", "", "", stdout=subprocess.PIPE)
    ff = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                         "-i", P["audio"], "-filter_complex", "[1:a]apad[a]", "-map", "0:v", "-map", "[a]",
                         "-c:v", "libx264", "-preset", "medium", "-b:v", f"{br}k", "-maxrate", f"{br}k", "-bufsize", f"{2 * br}k",
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out],
                        stdin=pr.stdout)
    pr.wait()
    print(out, os.path.getsize(out) // 1024 // 1024, "MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("plan"); ap.add_argument("--max-mb", type=float, default=90)
    a = ap.parse_args()
    {"prep": lambda: prep(a.plan), "raskadrovka": lambda: raskadrovka(a.plan), "render": lambda: render(a.plan, a.max_mb)}[a.cmd]()

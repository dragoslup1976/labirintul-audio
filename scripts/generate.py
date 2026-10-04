#!/usr/bin/env python3
"""Genereaza audio (voce Piper, ro_RO-mihai-medium) pentru articolele de pe
labirintulmagazin.org si le salveaza in public/audio/<id>.mp3 + <id>.json.

Fiecare .json contine propozitiile si momentele lor (secunde), ca aplicatia
sa poata evidentia textul in timp ce se aude audio-ul.
"""
import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import requests

SITE = os.environ.get("SITE", "https://labirintulmagazin.org")
OUT_DIR = os.environ.get("OUT_DIR", "public/audio")
MODEL = os.environ.get("PIPER_MODEL", "models/ro_RO-mihai-medium.onnx")
KEEP_LATEST = int(os.environ.get("KEEP_LATEST", "200"))  # cate articole pastram
FETCH_COUNT = int(os.environ.get("FETCH_COUNT", "30"))   # cate articole verificam
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "10"))   # limita pe rulare
VOICE_ID = "ro_RO-mihai-medium"
FORMAT_VERSION = 1

PAUSE_SENTENCE = 0.35
PAUSE_PARAGRAPH = 0.65
PAUSE_AFTER_TITLE = 0.8

# La fel ca in aplicatie: propozitie = pana la . ! ? … urmat de spatiu/sfarsit.
SENTENCE_RE = re.compile(r'[^\n]+?(?:[.!?…]+["”»)\]]*(?=\s|$)|$|(?=\n))')
HAS_LETTER_RE = re.compile(r'[^\W_]', re.UNICODE)


def strip_html(raw: str) -> str:
    raw = re.sub(r'<(script|style)\b.*?</\1>', '', raw, flags=re.I | re.S)
    raw = re.sub(r'<[^>]*>', '', raw)
    return html.unescape(raw).replace('\xa0', ' ').strip()


def split_sentences(text: str):
    """Intoarce lista de (propozitie, paragraf_nou)."""
    out = []
    prev_end = 0
    for m in SENTENCE_RE.finditer(text):
        raw = m.group(0)
        s = raw.strip()
        if not s or not HAS_LETTER_RE.search(s):
            continue
        new_par = '\n' in text[prev_end:m.start()] or not out
        out.append((s, new_par))
        prev_end = m.end()
    return out


def fetch_posts():
    url = f"{SITE}/wp-json/wp/v2/posts"
    params = {
        "per_page": FETCH_COUNT,
        "_fields": "id,modified,title,content",
        "orderby": "date",
        "order": "desc",
    }
    r = requests.get(url, params=params, timeout=60,
                     headers={"User-Agent": "labirintul-audio/1.0"})
    r.raise_for_status()
    return r.json()


def synth_sentence(voice, text: str) -> bytes:
    return b"".join(c.audio_int16_bytes for c in voice.synthesize(text))


def silence(seconds: float, rate: int) -> bytes:
    return b"\x00\x00" * int(seconds * rate)


def get_ffmpeg() -> str:
    """ffmpeg din sistem, sau (daca lipseste) cel adus de pachetul imageio-ffmpeg."""
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "imageio-ffmpeg"],
                   check=True)
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


_FFMPEG = None


def encode_mp3(pcm: bytes, rate: int, path: str):
    global _FFMPEG
    if _FFMPEG is None:
        _FFMPEG = get_ffmpeg()
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        f.write(pcm)
        raw = f.name
    try:
        subprocess.run(
            [_FFMPEG, "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(rate),
             "-ac", "1", "-i", raw, "-codec:a", "libmp3lame", "-b:a", "56k", path],
            check=True)
    finally:
        os.unlink(raw)


def build_audio(voice, post):
    rate = voice.config.sample_rate
    title = strip_html(post["title"]["rendered"])
    body = strip_html(post["content"]["rendered"])
    units = []
    if title:
        units.append((title, True, "title"))
    for s, new_par in split_sentences(body):
        units.append((s, new_par, "body"))
    if not any(k == "body" for _, _, k in units):
        return None

    pcm = bytearray()
    sentences = []
    for i, (text, new_par, kind) in enumerate(units):
        if i > 0:
            prev_kind = units[i - 1][2]
            if prev_kind == "title":
                pause = PAUSE_AFTER_TITLE
            else:
                pause = PAUSE_PARAGRAPH if new_par else PAUSE_SENTENCE
            pcm += silence(pause, rate)
        start = len(pcm) / 2 / rate
        pcm += synth_sentence(voice, text)
        end = len(pcm) / 2 / rate
        sentences.append({"k": kind, "t": text, "s": round(start, 3), "e": round(end, 3)})
    return bytes(pcm), rate, sentences


def content_hash(post) -> str:
    h = hashlib.sha1()
    h.update(str(FORMAT_VERSION).encode())
    h.update(VOICE_ID.encode())
    h.update(strip_html(post["title"]["rendered"]).encode())
    h.update(strip_html(post["content"]["rendered"]).encode())
    return h.hexdigest()


def prune(posts_ids_in_order):
    """Pastreaza doar cele mai recente KEEP_LATEST articole (dupa id)."""
    ids = sorted(
        {int(f.split(".")[0]) for f in os.listdir(OUT_DIR) if f.endswith(".json")},
        reverse=True)
    for old in ids[KEEP_LATEST:]:
        for ext in ("json", "mp3"):
            p = os.path.join(OUT_DIR, f"{old}.{ext}")
            if os.path.exists(p):
                os.remove(p)
                print(f"sters (vechi): {p}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="fisier JSON cu articole (pentru teste)")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    posts = json.load(open(args.fixture, encoding="utf-8")) if args.fixture else fetch_posts()

    from piper import PiperVoice
    voice = None
    done = 0
    for post in posts:
        pid = post["id"]
        meta_path = os.path.join(OUT_DIR, f"{pid}.json")
        digest = content_hash(post)
        if os.path.exists(meta_path):
            try:
                if json.load(open(meta_path, encoding="utf-8")).get("hash") == digest:
                    continue
            except Exception:
                pass
        if done >= MAX_PER_RUN:
            print("limita pe rulare atinsa; restul la urmatoarea rulare")
            break
        if voice is None:
            voice = PiperVoice.load(MODEL, config_path=MODEL + ".json")
        t0 = time.time()
        try:
            res = build_audio(voice, post)
        except Exception as e:  # un articol stricat nu opreste restul
            print(f"EROARE articol {pid}: {e}", file=sys.stderr)
            continue
        if res is None:
            print(f"articol {pid}: fara text, sar peste")
            continue
        pcm, rate, sentences = res
        encode_mp3(pcm, rate, os.path.join(OUT_DIR, f"{pid}.mp3"))
        meta = {
            "id": pid,
            "voice": VOICE_ID,
            "version": FORMAT_VERSION,
            "modified": post.get("modified"),
            "hash": digest,
            "duration": round(len(pcm) / 2 / rate, 3),
            "sentences": sentences,
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))
        done += 1
        print(f"articol {pid}: {len(sentences)} propozitii, "
              f"{meta['duration']:.0f}s audio, generat in {time.time() - t0:.0f}s")

    prune([p["id"] for p in posts])
    print(f"gata: {done} articole noi/actualizate")


if __name__ == "__main__":
    main()

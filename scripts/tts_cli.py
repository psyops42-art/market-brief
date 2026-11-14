"""Text → MP3 with Google Cloud TTS, for on-demand use (GitHub Actions 'tts' workflow).

Engines
  chirp  : Chirp 3 HD voices (same as the weekly video), e.g. ko-KR-Chirp3-HD-Kore
  gemini : Gemini-TTS through the same Cloud TTS endpoint; style is steered by --prompt

Long text is split into chunks (Cloud TTS caps a request at ~5,000 bytes) and joined with ffmpeg.
Usage: python scripts/tts_cli.py --text-file in.txt --out out.mp3 [--engine chirp|gemini] ...
"""
import argparse
import base64
import os
import re
import subprocess
import sys
import tempfile

import requests

URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
MAX_BYTES = 4500


def chunks(text):
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?。…\n])\s+", text.strip()):
        if not sent:
            continue
        cand = (cur + " " + sent).strip()
        if len(cand.encode("utf-8")) > MAX_BYTES and cur:
            parts.append(cur)
            cur = sent
        else:
            cur = cand
    if cur:
        parts.append(cur)
    return parts


def synth(text, a, key):
    if a.engine == "gemini":
        body = {
            "input": {"text": text, **({"prompt": a.prompt} if a.prompt else {})},
            "voice": {"languageCode": a.lang, "name": a.voice, "model_name": a.model},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": a.rate},
        }
    else:
        name = a.voice if "-" in a.voice else f"{a.lang}-Chirp3-HD-{a.voice}"
        body = {
            "input": {"text": text},
            "voice": {"languageCode": a.lang, "name": name},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": a.rate, "sampleRateHertz": 44100},
        }
    r = requests.post(URL, params={"key": key}, json=body, timeout=120)
    if r.status_code != 200:
        # Never print the key; Google's error text does not contain it.
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
    return base64.b64decode(r.json()["audioContent"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--text-file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--engine", choices=["chirp", "gemini"], default="chirp")
    p.add_argument("--voice", default="Kore")
    p.add_argument("--lang", default="ko-KR")
    p.add_argument("--rate", type=float, default=1.0)
    p.add_argument("--prompt", default="")
    p.add_argument("--model", default="gemini-2.5-flash-tts")
    a = p.parse_args()

    key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not key:
        sys.exit("GOOGLE_TTS_API_KEY is not set")
    text = open(a.text_file, encoding="utf-8").read()
    parts = chunks(text)
    print(f"engine={a.engine} voice={a.voice} chunks={len(parts)} chars={len(text)}")

    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i, part in enumerate(parts):
            f = os.path.join(tmp, f"{i:03d}.mp3")
            with open(f, "wb") as fh:
                fh.write(synth(part, a, key))
            files.append(f)
        if len(files) == 1:
            os.replace(files[0], a.out)
        else:
            lst = os.path.join(tmp, "list.txt")
            with open(lst, "w") as fh:
                fh.writelines(f"file '{f}'\n" for f in files)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                            "-i", lst, "-c", "copy", a.out], check=True)
    print(f"wrote {a.out} ({os.path.getsize(a.out)} bytes)")


if __name__ == "__main__":
    main()

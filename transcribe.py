#!/usr/bin/env python3
"""Full, word-for-word transcription of a video or an audio file, for Harness Maxxing.

    python3 transcribe.py <url|file> [-o out.txt] [--engine auto|harness|gemini] [--model M]

Engines:
- harness: the voice engine set in Harness (Settings -> Voice: local Whisper, Groq or OpenAI), through
  `maxxing voice transcribe`. That command takes 15 minutes of audio at most, so longer audio is cut into parts of
  about 10 minutes, at a silence when there is one, and the texts are joined. Nothing new to configure.
- gemini: Google Gemini, key GEMINI_API_KEY. The only engine for YouTube: Gemini reads the video itself and nothing
  is downloaded (YouTube refuses downloads from most server addresses).
- auto (default): YouTube -> gemini; anything else -> harness when its voice engine is ready, else gemini.

Other links are downloaded with yt-dlp when it is installed. The text goes to stdout, or to -o (written at once at
the end; on failure, <out>.err holds the reason). Progress goes to stderr. Standard library only.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://generativelanguage.googleapis.com"
MODEL = "gemini-3.1-flash-lite"
PART = 600          # seconds per part for the Harness engine (its limit is 900)
WINDOW = 60         # look back up to this many seconds for a silence to cut at
YOUTUBE_RE = re.compile(r"^https?://(www\.|m\.)?(youtube\.com/(watch|shorts|live)|youtu\.be/)", re.I)
URL_RE = re.compile(r"^https?://", re.I)
SILENCE_RE = re.compile(r"silence_(start|end): (-?[\d.]+)")
PROMPT = (
    "Transcribe everything that is said, word for word, in the language spoken, from start to finish. "
    "Do not summarise, do not translate, add no title, no timestamps and no comments. "
    "Punctuate normally and start a new line when the topic or the speaker changes. "
    "If nobody speaks, answer only: [no speech]"
)


class Failure(Exception):
    """A reason shown to the user as is."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def maxxing_bin() -> str:
    return shutil.which("maxxing") or "/usr/local/bin/maxxing"


# ---------------------------------------------------------------- engine choice

def harness_ready() -> bool:
    try:
        p = subprocess.run([maxxing_bin(), "voice", "status", "--json"], capture_output=True, text=True, timeout=60)
        return p.returncode == 0 and bool(json.loads(p.stdout).get("ready"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def pick_engine(source: str, engine: str, ready) -> str:
    """-> "gemini" or "harness". `ready` is called only when needed (it starts a process)."""
    if YOUTUBE_RE.match(source):
        if engine == "harness":
            raise Failure("YouTube links are read by Gemini only (YouTube refuses downloads from servers)")
        return "gemini"
    if engine in ("harness", "gemini"):
        return engine
    return "harness" if ready() else "gemini"


# ---------------------------------------------------------------- Gemini

def gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise Failure("GEMINI_API_KEY is missing: add it in Settings -> Keys "
                      "(or use the Harness voice engine for files)")
    return key


def call(key: str, method: str, url: str, body=None, headers=None, timeout=600):
    """Request to the Gemini API (key in a header, never in the URL). -> (json, headers)."""
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("x-goog-api-key", key)
    if data is not None and not isinstance(body, bytes):
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return (json.loads(raw) if raw else {}), r.headers
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise Failure(f"Gemini HTTP {e.code}: {detail}") from None


def upload(key: str, path: Path, mime: str) -> dict:
    """Gemini Files API (resumable upload in one go), then wait for the ACTIVE state."""
    _, h = call(key, "POST", f"{API}/upload/v1beta/files", {"file": {"display_name": path.name}}, {
        "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(path.stat().st_size), "X-Goog-Upload-Header-Content-Type": mime})
    up_url = h.get("x-goog-upload-url")
    if not up_url:
        raise Failure("Gemini returned no upload address")
    info, _ = call(key, "POST", up_url, path.read_bytes(), {
        "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"})
    f = info["file"]
    for _ in range(120):
        if f.get("state") == "ACTIVE":
            return f
        if f.get("state") == "FAILED":
            raise Failure("Gemini could not read the file")
        time.sleep(3)
        f, _ = call(key, "GET", f"{API}/v1beta/{f['name']}")
    raise Failure("the file was still being processed by Gemini after 6 minutes")


def generate(key: str, model: str, media: dict, low_res: bool) -> str:
    cfg = {"temperature": 0, "maxOutputTokens": 65536}
    if low_res:
        cfg["mediaResolution"] = "MEDIA_RESOLUTION_LOW"  # the picture hardly matters, the sound does
    body = {"contents": [{"parts": [media, {"text": PROMPT}]}], "generationConfig": cfg}
    d, _ = call(key, "POST", f"{API}/v1beta/models/{model}:generateContent", body, timeout=1800)
    cand = (d.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
    reason = cand.get("finishReason")
    if not text:
        raise Failure(f"no text returned (finishReason={reason}, {d.get('promptFeedback', '')})")
    if reason == "MAX_TOKENS":
        text += "\n\n[TRANSCRIPT CUT: Gemini's output length limit was reached]"
        log("cut short (MAX_TOKENS)")
    return text


def gemini_file(audio: Path, model: str) -> str:
    key = gemini_key()
    log(f"Sending to Gemini ({audio.stat().st_size // 1024} KB)…")
    f = upload(key, audio, "audio/mpeg")
    try:
        log(f"Transcribing with Gemini ({model})…")
        return generate(key, model, {"file_data": {"mime_type": f["mimeType"], "file_uri": f["uri"]}}, False)
    finally:
        try:
            call(key, "DELETE", f"{API}/v1beta/{f['name']}", timeout=30)
        except Failure:
            pass  # Gemini deletes files by itself after 48 h


# ---------------------------------------------------------------- Harness voice engine

def ffmpeg(*args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    if not shutil.which("ffmpeg"):
        raise Failure("ffmpeg is missing: apt-get install -y ffmpeg")
    return subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", *args], capture_output=True, text=True,
                          timeout=timeout)


def to_audio(src: Path, tmp: Path) -> Path:
    """Audio track only, mono 16 kHz 32 kb/s: speech stays clear and the file stays small."""
    out = tmp / "audio.mp3"
    p = ffmpeg("-loglevel", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out))
    if p.returncode != 0 or not out.exists():
        raise Failure(f"ffmpeg could not extract the audio: {p.stderr.strip()[-300:]}")
    return out


def probe(audio: Path) -> tuple[float, list]:
    """-> (duration in seconds, [(silence start, silence end)…])."""
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
                       capture_output=True, text=True, timeout=120)
    try:
        duration = float(d.stdout.strip())
    except ValueError:
        raise Failure(f"ffprobe could not read the audio length: {d.stderr.strip()[-300:]}") from None
    p = ffmpeg("-i", str(audio), "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-")
    marks, start, silences = SILENCE_RE.findall(p.stderr), None, []
    for kind, t in marks:
        if kind == "start":
            start = float(t)
        elif start is not None:
            silences.append((start, float(t)))
            start = None
    return duration, silences


def cut_points(duration: float, silences: list, part: int = PART, window: int = WINDOW) -> list:
    """Where to cut: about every `part` seconds, in the middle of the last silence of the `window` seconds before
    the mark (no cut inside a word), else at the mark. Every part stays <= part seconds."""
    cuts, last = [], 0.0
    while duration - last > part:
        mark = last + part
        mids = [(a + b) / 2 for a, b in silences if mark - window <= (a + b) / 2 <= mark]
        cut = max(mids) if mids else mark
        cuts.append(round(cut, 2))
        last = cut
    return cuts


def harness_part(path: Path) -> str:
    p = subprocess.run([maxxing_bin(), "voice", "transcribe", str(path)], capture_output=True, text=True,
                       timeout=3600)
    out = p.stdout.strip()
    if p.returncode != 0 or not out.startswith("{"):
        raise Failure("Harness voice engine: " + (out or p.stderr.strip())[-300:].removeprefix("error: "))
    return (json.loads(out).get("text") or "").strip()


def harness_file(audio: Path, tmp: Path) -> str:
    duration, silences = probe(audio)
    bounds = [0.0, *cut_points(duration, silences), duration]
    texts = []
    for i in range(len(bounds) - 1):
        log(f"Transcribing with the Harness voice engine: part {i + 1}/{len(bounds) - 1}…")
        if len(bounds) == 2:
            part = audio
        else:
            part = tmp / f"part{i:03d}.mp3"
            p = ffmpeg("-loglevel", "error", "-y", "-i", str(audio), "-ss", str(bounds[i]), "-to", str(bounds[i + 1]),
                       "-c", "copy", str(part))
            if p.returncode != 0:
                raise Failure(f"ffmpeg could not cut the audio: {p.stderr.strip()[-300:]}")
        texts.append(harness_part(part))
    text = "\n".join(t for t in texts if t)
    if not text:
        raise Failure("no speech heard")
    return text


# ---------------------------------------------------------------- main flow

def download(url: str, tmp: Path) -> Path:
    ytdlp = shutil.which("yt-dlp") or os.path.expanduser("~/.local/bin/yt-dlp")
    if not os.path.exists(ytdlp):
        raise Failure("yt-dlp is not installed, so this link cannot be downloaded: "
                      "download the file yourself and send it")
    p = subprocess.run([ytdlp, "--no-playlist", "-f", "bestaudio/best", "-o", str(tmp / "dl.%(ext)s"), url],
                       capture_output=True, text=True, timeout=1800)
    files = [f for f in tmp.glob("dl.*") if not f.name.endswith(".part")]
    if p.returncode != 0 or not files:
        err = [ln for ln in p.stderr.splitlines() if "ERROR" in ln]
        raise Failure("download refused: " + (err[-1][:300] if err else p.stderr.strip()[-300:])
                      + " (download the file yourself and send it)")
    return files[0]


def transcribe(source: str, engine: str = "auto", model: str = MODEL) -> str:
    chosen = pick_engine(source, engine, harness_ready)
    if YOUTUBE_RE.match(source):
        log(f"YouTube: read directly by Gemini ({model})")
        return generate(gemini_key(), model, {"file_data": {"file_uri": source}}, low_res=True)
    if chosen == "gemini":
        gemini_key()  # fail before a long download
    with tempfile.TemporaryDirectory(prefix="transcribe-") as t:
        tmp = Path(t)
        if URL_RE.match(source):
            log("Downloading (yt-dlp)…")
            src = download(source, tmp)
        else:
            src = Path(source).expanduser()
            if not src.is_file():
                raise Failure(f"file not found: {src}")
        log("Extracting the audio…")
        audio = to_audio(src, tmp)
        return harness_file(audio, tmp) if chosen == "harness" else gemini_file(audio, model)


def main() -> int:
    ap = argparse.ArgumentParser(description="Full, word-for-word transcription of a video or an audio file.")
    ap.add_argument("source", help="a link (YouTube or other) or the path of an audio / video file")
    ap.add_argument("-o", "--output", help="write the text to this file (default: stdout)")
    ap.add_argument("--engine", choices=["auto", "harness", "gemini"], default="auto",
                    help="auto (default): YouTube -> gemini, the rest -> the Harness voice engine when ready")
    ap.add_argument("--model", default=MODEL, help=f"Gemini model (default {MODEL})")
    a = ap.parse_args()
    out = Path(a.output) if a.output else None
    try:
        text = transcribe(a.source.strip(), a.engine, a.model)
    except Exception as e:  # noqa: BLE001 - the reason must come out, not a stack trace
        log(f"FAILED: {e}")
        if out:
            out.with_name(out.name + ".err").write_text(str(e), encoding="utf-8")
        return 1
    if out:
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(out)
        log(f"OK: {len(text)} characters -> {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

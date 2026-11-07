"""Background jobs of the Transcription page: `transcribe.py` started detached, file uploads in chunks (an extension
request body is limited to 5 MB), a short history. One job = one folder <state>/jobs/<id>/: meta.json, out.txt (the
text, written at once at the end) or out.txt.err (the reason), log, pid."""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

SCRIPT = str(Path(__file__).with_name("transcribe.py"))
KEEP = 30                       # jobs kept in the history
MAX_UPLOAD = 2 * 1024 ** 3      # 2 GB, like the Files page of Harness
ENGINES = ("auto", "harness", "gemini")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
URL_RE = re.compile(r"^https?://[^\s]{4,2000}$", re.I)


class Refused(Exception):
    """A refused request: the message is shown as is."""


def _jobs(state: Path) -> Path:
    d = Path(state) / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_dir(state: Path, job_id: str) -> Path:
    if not ID_RE.match(job_id or ""):
        raise Refused("invalid id")
    return _jobs(state) / job_id


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[-1].split()[0] != "Z"


def _engine(engine) -> str:
    engine = engine or "auto"
    if engine not in ENGINES:
        raise Refused("unknown engine")
    return engine


def status(d: Path) -> str:
    if (d / "out.txt").exists():
        return "done"
    if (d / "out.txt.err").exists():
        return "error"
    try:
        pid = int((d / "pid").read_text())
    except (OSError, ValueError):
        return "error"
    return "running" if _alive(pid) else "error"


def summary(d: Path) -> dict:
    meta = json.loads((d / "meta.json").read_text())
    st = status(d)
    out = {"id": d.name, "label": meta["label"], "started": meta["started"], "status": st}
    if st == "done":
        out["chars"] = len((d / "out.txt").read_text(errors="replace"))
        out["finished"] = (d / "out.txt").stat().st_mtime
    elif st == "error":
        err = d / "out.txt.err"
        out["error"] = err.read_text()[:500] if err.exists() else "interrupted (server restarted?)"
    else:
        lines = (d / "log").read_text(errors="replace").strip().splitlines() if (d / "log").exists() else []
        out["step"] = lines[-1][:200] if lines else "starting…"
    return out


def list_jobs(state: Path) -> list:
    dirs = [d for d in _jobs(state).iterdir() if (d / "meta.json").exists()]
    dirs.sort(key=lambda d: (d / "meta.json").stat().st_mtime, reverse=True)
    for old in dirs[KEEP:]:
        if status(old) != "running":
            shutil.rmtree(old, ignore_errors=True)
    return [summary(d) for d in dirs[:KEEP]]


def get_text(state: Path, job_id: str) -> dict:
    d = _job_dir(state, job_id)
    if not (d / "out.txt").exists():
        raise Refused("no text for this job")
    return {**summary(d), "text": (d / "out.txt").read_text(errors="replace")}


def delete(state: Path, job_id: str) -> None:
    d = _job_dir(state, job_id)
    if d.exists() and status(d) == "running":
        raise Refused("job still running, wait for the end")
    shutil.rmtree(d, ignore_errors=True)


def start(state: Path, source: str, label: str, key: str, engine: str = "auto", cleanup: Path = None,
          runner=None) -> dict:
    """Starts `transcribe.py source -o out.txt` detached; a thread waits for the end (no zombie). The Gemini key is
    given to that process only."""
    job_id = uuid.uuid4().hex
    d = _jobs(state) / job_id
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"label": label[:300], "started": time.time()}))
    env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
    if key:
        env["GEMINI_API_KEY"] = key
    cmd = [sys.executable, SCRIPT, source, "-o", str(d / "out.txt"), "--engine", engine]
    with open(d / "log", "wb") as log:
        p = (runner or subprocess.Popen)(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
                                         start_new_session=True)
    (d / "pid").write_text(str(p.pid))

    def reap():
        p.wait()
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)
    threading.Thread(target=reap, daemon=True).start()
    return summary(d)


def start_url(state: Path, url: str, key: str, engine: str = "auto", runner=None) -> dict:
    url = (url or "").strip()
    if not URL_RE.match(url):
        raise Refused("an http(s) address is expected")
    return start(state, url, url, key, _engine(engine), runner=runner)


def upload_chunk(state: Path, body: dict, key: str, runner=None) -> dict:
    """Chunk number `index` (base64) of the upload `upload`; the last one (`last`) starts the transcription."""
    engine = _engine(body.get("engine"))
    up = body.get("upload") or uuid.uuid4().hex
    if not ID_RE.match(up):
        raise Refused("invalid upload")
    name = re.sub(r"[^\w.\- ]", "_", str(body.get("name") or "file"))[:120] or "file"
    d = Path(state) / "uploads" / up
    d.mkdir(parents=True, exist_ok=True)
    f = d / ("src" + Path(name).suffix[:10])
    index = int(body.get("index", 0))
    if index == 0 and f.exists():
        f.unlink()
    have = f.stat().st_size if f.exists() else 0
    if index > 0 and have != int(body.get("offset", -1)):
        shutil.rmtree(d, ignore_errors=True)
        raise Refused("chunk out of order, upload cancelled")
    data = base64.b64decode(body.get("data") or "", validate=True)
    if have + len(data) > MAX_UPLOAD:
        shutil.rmtree(d, ignore_errors=True)
        raise Refused("file too big (2 GB at most)")
    with open(f, "ab") as fh:
        fh.write(data)
    if not body.get("last"):
        return {"upload": up, "received": have + len(data)}
    return start(state, str(f), name, key, engine, cleanup=d, runner=runner)


def purge_uploads(state: Path, max_age: float = 6 * 3600) -> None:
    """Uploads abandoned on the way (tab closed): deleted after 6 h."""
    root = Path(state) / "uploads"
    if not root.exists():
        return
    now = time.time()
    for d in root.iterdir():
        try:
            if now - d.stat().st_mtime > max_age and not any(now - x.stat().st_mtime < max_age for x in d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass

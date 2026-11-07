"""Jobs and uploads of the Transcription page, with no network and no engine (a fake transcribe.py).
Run: python3 -m pytest -q tests/"""
import base64
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jobs  # noqa: E402


def fake(tmp_path, monkeypatch, script):
    """Replaces transcribe.py with a small shell script: $1 = source, $3 = output file, $5 = engine. Like the real
    one, it writes to $3.tmp then renames: the text never shows half-written."""
    exe = tmp_path / "fake-transcribe"
    exe.write_text('OUT="$3"; set -- "$1" "$2" "$3.tmp" "$4" "$5"\n' + script + '[ -f "$3" ] && mv "$3" "$OUT"\n')
    monkeypatch.setattr(jobs.sys, "executable", "/bin/sh")
    monkeypatch.setattr(jobs, "SCRIPT", str(exe))


def wait(state, job_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        j = next(x for x in jobs.list_jobs(state) if x["id"] == job_id)
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job never finished")


def test_url_text_key_and_engine(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, 'echo "step 1" >&2; printf "%s|%s|%s" "$1" "$GEMINI_API_KEY" "$5" > "$3"\n')
    j = jobs.start_url(tmp_path, " https://youtu.be/abc ", "K1", "gemini")
    assert j["status"] == "running"
    done = wait(tmp_path, j["id"])
    assert done["status"] == "done" and done["chars"] == len("https://youtu.be/abc|K1|gemini")
    assert jobs.get_text(tmp_path, j["id"])["text"] == "https://youtu.be/abc|K1|gemini"


def test_no_key_means_no_key_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "LEAKED")
    fake(tmp_path, monkeypatch, 'printf "[%s]" "${GEMINI_API_KEY-unset}" > "$3"\n')
    j = wait(tmp_path, jobs.start_url(tmp_path, "https://x.example/v", None)["id"])
    assert jobs.get_text(tmp_path, j["id"])["text"] == "[unset]"


def test_chars_counts_characters_not_bytes(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, 'printf "été" > "$3"\n')
    j = wait(tmp_path, jobs.start_url(tmp_path, "https://x.example/v", "K")["id"])
    assert j["chars"] == 3


def test_failure_reason_returned(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, 'printf "blocked by the site" > "$OUT.err"; exit 1\n')
    j = wait(tmp_path, jobs.start_url(tmp_path, "https://x.example/v", "K")["id"])
    assert j["status"] == "error" and j["error"] == "blocked by the site"


def test_death_without_trace_is_a_failure(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "kill -9 $$\n")
    j = wait(tmp_path, jobs.start_url(tmp_path, "https://x.example/v", "K")["id"])
    assert j["status"] == "error" and "interrupted" in j["error"]


@pytest.mark.parametrize("bad", ["", "ftp://x/y", "echo pwned", "-o /etc/passwd", None])
def test_url_refused(tmp_path, bad):
    with pytest.raises(jobs.Refused):
        jobs.start_url(tmp_path, bad, "K")


@pytest.mark.parametrize("bad", ["whisper", "--model", "../x"])
def test_engine_refused(tmp_path, bad):
    with pytest.raises(jobs.Refused):
        jobs.start_url(tmp_path, "https://x.example/v", "K", bad)
    with pytest.raises(jobs.Refused):
        jobs.upload_chunk(tmp_path, {"name": "a.mp3", "index": 0, "data": "", "engine": bad}, "K")


def test_invalid_id(tmp_path):
    for bad in ["../x", "a" * 31, "", "A" * 32]:
        with pytest.raises(jobs.Refused):
            jobs.get_text(tmp_path, bad)
        with pytest.raises(jobs.Refused):
            jobs.delete(tmp_path, bad)


def test_upload_in_chunks_then_cleanup(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, 'cat "$1" > "$3"\n')
    parts = [b"abc", b"def", b"gh"]
    up, off, got = None, 0, None
    for i, p in enumerate(parts):
        got = jobs.upload_chunk(tmp_path, {"upload": up, "name": "../../x y.mp3", "index": i, "offset": off,
                                           "data": base64.b64encode(p).decode(), "last": i == 2}, "K")
        up = got.get("upload", up)
        off += len(p)
    j = wait(tmp_path, got["id"])
    assert j["label"] == ".._.._x y.mp3"
    assert jobs.get_text(tmp_path, j["id"])["text"] == "abcdefgh"
    time.sleep(0.2)
    assert not (tmp_path / "uploads" / up).exists()


def test_chunk_out_of_order_cancels(tmp_path):
    got = jobs.upload_chunk(tmp_path, {"name": "a.mp3", "index": 0, "data": base64.b64encode(b"abc").decode()}, "K")
    with pytest.raises(jobs.Refused):
        jobs.upload_chunk(tmp_path, {"upload": got["upload"], "name": "a.mp3", "index": 1, "offset": 99,
                                     "data": base64.b64encode(b"x").decode()}, "K")
    assert not (tmp_path / "uploads" / got["upload"]).exists()


def test_history_bounded_and_delete(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, 'echo x > "$3"\n')
    monkeypatch.setattr(jobs, "KEEP", 2)
    ids = []
    for i in range(3):
        ids.append(wait(tmp_path, jobs.start_url(tmp_path, f"https://x.example/{i}", "K")["id"])["id"])
        time.sleep(0.02)
    assert [j["id"] for j in jobs.list_jobs(tmp_path)] == ids[:0:-1]
    jobs.delete(tmp_path, ids[2])
    assert [j["id"] for j in jobs.list_jobs(tmp_path)] == [ids[1]]


def test_api_routes(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import api
    fake(tmp_path, monkeypatch, 'echo x > "$3"\n')
    monkeypatch.setattr(api.jobs, "SCRIPT", jobs.SCRIPT)
    monkeypatch.setattr(api.jobs.sys, "executable", "/bin/sh")
    ctx = {"state_dir": str(tmp_path), "secret": lambda name: None}
    assert api.handle(ctx, "GET", "jobs", {}, None) == {"jobs": [], "gemini": False}
    assert api.handle(ctx, "POST", "jobs", {}, {"url": "nope"}) == {"error": "an http(s) address is expected"}
    assert api.handle(ctx, "GET", "other", {}, None)[0] == 404

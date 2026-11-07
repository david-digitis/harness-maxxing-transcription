"""Engine choice, cut points and Harness engine output parsing of transcribe.py, with no network and no audio."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transcribe as t  # noqa: E402


def never():
    raise AssertionError("readiness must not be checked")


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=x", "https://youtu.be/x", "https://m.youtube.com/shorts/x"])
def test_youtube_always_gemini(url):
    assert t.pick_engine(url, "auto", never) == "gemini"
    assert t.pick_engine(url, "gemini", never) == "gemini"
    with pytest.raises(t.Failure):
        t.pick_engine(url, "harness", never)


def test_auto_follows_harness_readiness():
    assert t.pick_engine("/a.mp3", "auto", lambda: True) == "harness"
    assert t.pick_engine("/a.mp3", "auto", lambda: False) == "gemini"
    assert t.pick_engine("https://pod.example/a.mp3", "gemini", never) == "gemini"
    assert t.pick_engine("/a.mp3", "harness", never) == "harness"


def test_short_audio_is_not_cut():
    assert t.cut_points(600, []) == []
    assert t.cut_points(42, [(10, 11)]) == []


def test_cut_without_silence_at_the_mark():
    assert t.cut_points(1500, []) == [600, 1200]


def test_cut_in_the_last_silence_before_the_mark():
    silences = [(500, 502), (580, 584), (601, 603), (1150, 1152)]
    assert t.cut_points(1300, silences) == [582, 1151]


def test_every_part_stays_under_the_harness_limit():
    silences = [(s, s + 1) for s in range(0, 7200, 37)]
    bounds = [0, *t.cut_points(7200, silences), 7200]
    parts = [b - a for a, b in zip(bounds, bounds[1:])]
    assert max(parts) <= t.PART and min(parts) > 0


def test_missing_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(t.Failure, match="Settings -> Keys"):
        t.gemini_key()


def fake_run(stdout, code=0):
    return lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=stdout, stderr="")


def test_harness_part_reads_the_text(monkeypatch):
    monkeypatch.setattr(t.subprocess, "run", fake_run('{"text": " Bonjour. ", "language": "fr"}'))
    assert t.harness_part(Path("x.mp3")) == "Bonjour."


def test_harness_part_error_is_shown(monkeypatch):
    monkeypatch.setattr(t.subprocess, "run", fake_run("error: voice is off", 1))
    with pytest.raises(t.Failure, match="voice is off"):
        t.harness_part(Path("x.mp3"))


def test_harness_ready(monkeypatch):
    monkeypatch.setattr(t.subprocess, "run", fake_run('{"ready": true}'))
    assert t.harness_ready() is True
    monkeypatch.setattr(t.subprocess, "run", fake_run("not json"))
    assert t.harness_ready() is False

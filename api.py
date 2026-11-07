"""Transcription page: a link (YouTube or other) or an audio / video file -> the full text, to copy. The work is done
by transcribe.py, started in the background; job handling: jobs.py. Optional Gemini key: Settings -> Keys,
GEMINI_API_KEY (it can stay hidden from sessions)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("community_transcription_jobs", Path(__file__).with_name("jobs.py"))
jobs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jobs)


def handle(ctx, method, path, query, body):
    state = Path(ctx["state_dir"])
    parts = path.split("/")
    body = body if isinstance(body, dict) else {}
    try:
        if method == "GET" and path == "jobs":
            jobs.purge_uploads(state)
            return {"jobs": jobs.list_jobs(state), "gemini": bool(ctx["secret"]("GEMINI_API_KEY"))}
        if method == "GET" and len(parts) == 2 and parts[0] == "jobs":
            return jobs.get_text(state, parts[1])
        if method == "DELETE" and len(parts) == 2 and parts[0] == "jobs":
            jobs.delete(state, parts[1])
            return {"ok": True}
        if method == "POST" and path == "jobs":
            return jobs.start_url(state, body.get("url"), ctx["secret"]("GEMINI_API_KEY"), body.get("engine"))
        if method == "POST" and path == "upload":
            return jobs.upload_chunk(state, body, ctx["secret"]("GEMINI_API_KEY"))
    except jobs.Refused as e:
        return {"error": str(e)}  # 200: the page shows the message (ctx.api does not return the body of an error)
    return 404, {"error": "not_found", "message": f"{method} {path}"}

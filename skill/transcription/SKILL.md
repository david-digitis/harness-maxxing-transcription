---
name: transcription
description: Get the full, word-for-word text of a video or an audio - a YouTube link, a podcast or another link, or an audio / video file on the server (meeting, call, voice memo, interview) - with the Transcription extension of Harness Maxxing. Use when the user asks to transcribe, wants "the text of this video", "what is said in this recording", or when you need the spoken content of a media file to work on it (quote it, summarise it, answer questions about it).
---

# Transcription

The Transcription extension ships a command any session can run:

```bash
python3 /root/maxxing/extensions/transcription/transcribe.py <url|file> -o /path/to/out.txt
```

- `-o` writes the text at once at the end (on failure `<out>.err` holds the reason); without it the text goes to
  stdout. Progress goes to stderr. Prefer `-o` and read the file in parts: a one-hour video is ~60 000 characters.
- Engines (`--engine auto|harness|gemini`, default `auto`):
  - **harness**: the voice engine of Settings -> Voice (local Whisper, Groq or OpenAI). Free and private with local
    Whisper. Audio over 15 minutes is cut into ~10-minute parts at silences and joined.
  - **gemini**: Google Gemini, needs `GEMINI_API_KEY` in the environment. The only engine for YouTube links (Gemini
    reads the video itself; YouTube refuses downloads from servers).
  - `auto`: YouTube -> gemini, everything else -> harness when its voice engine is ready, else gemini.
- Other links are downloaded with `yt-dlp` when it is installed; many sites refuse server downloads. Then ask the user
  to download the file and send it (it lands in the topic's `attachments/`) and transcribe that file.

## Rules

- The text is the transcript, not the truth: names, numbers and technical words can be misheard. Say so when it
  matters, and never present a guess from the transcript as a fact.
- A Gemini key is usually stored *not exposed* to sessions. If `GEMINI_API_KEY` is missing here, do not look for it
  elsewhere: tell the user to use the **Transcription** page (it reads the key itself), or to expose the key in
  Settings -> Keys if they want sessions to transcribe YouTube links.
- Audio sent to Gemini, Groq or OpenAI leaves the server. For a private recording (a client call, an internal
  meeting), use `--engine harness` with the local Whisper engine, and say which engine you used.
- Long audio with local Whisper takes about 1/10 of its length on 4 CPUs and uses ~1 GB of RAM: run it in the
  background for anything over ~20 minutes.

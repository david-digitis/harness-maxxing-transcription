# Transcription for Harness Maxxing

A community [page extension](https://github.com/makushingu/harness-maxxing/blob/main/docs/user/13-page-extensions.md)
for [Harness Maxxing](https://github.com/makushingu/harness-maxxing): paste a link or send an audio / video file, get
the **full, word-for-word text** with a **Copy** button. No summary, no rewriting: the transcript only.

- **A Transcription page**: a YouTube link, another link (podcast, MP3…) or a file from your computer (up to 2 GB);
  the work runs in the background, you can leave the page; the last 30 transcripts are kept.
- **Your Harness voice engine for files**: it reuses what you already set in **Settings -> Voice** (local Whisper,
  Groq or OpenAI). With local Whisper it is free and nothing leaves your server. Harness takes 15 minutes of audio
  at most per message, so longer audio is cut into parts of about 10 minutes, at a silence, and the texts are joined.
- **Gemini for YouTube** (optional): YouTube refuses downloads from most servers, so a YouTube link is read by
  Google Gemini directly, from its address. Gemini can also do files if you prefer it.
- **A skill for Claude sessions**: Claude can transcribe a video or a recording by itself when it needs the content.

Measured on a 4-vCPU / 8 GB server with local Whisper `small`: 5 minutes of French speech in 33 s, 20 minutes in
2 min 05 (about a tenth of the audio length), ~750 MB of RAM while it runs.

> **Community extension, not reviewed by the Harness Maxxing maintainers.** Like every extension, `api.py` runs
> inside the Harness server with its rights (root on a standard install). Read the code before installing it: it is
> short and uses the Python standard library only.

## Requirements

- Harness Maxxing with page extensions (extension contract `"api": 1`) and `ffmpeg` (`apt-get install -y ffmpeg`).
- For files: a voice engine ready in **Settings -> Voice** (`maxxing voice status` says `ready: yes`).
- For YouTube links: a [Gemini API key](https://aistudio.google.com/apikey).
- For other links: [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (optional; many sites refuse server downloads, then
  download the file yourself and send it).

## Install

1. On the Harness server, as root:

   ```bash
   git clone https://github.com/david-digitis/harness-maxxing-transcription.git /root/maxxing/extensions/transcription
   ```

   (`/root/maxxing` is the default workspace: use yours if you changed it. The folder must be named `transcription`.)
2. **Settings -> Extensions -> Reload**, then open **Transcription** under Extensions (pin it to keep it in the
   sidebar).
3. Optional, for YouTube: **Settings -> Keys -> Add a key**, name `GEMINI_API_KEY`. Leave **Expose to Harness
   sessions** off unless you want Claude sessions to transcribe YouTube links too: the page reads the key without it.
4. For Claude sessions, link the skill once (a link, not a copy: a `git pull` keeps it current):

   ```bash
   ln -s /root/maxxing/extensions/transcription/skill/transcription /root/maxxing/.claude/skills/transcription
   ```

## Use

- **Page**: paste a link and **Transcribe**, or **Send a file**. **Engine**: *Automatic* (YouTube -> Gemini, the rest
  -> your Harness voice engine when ready, else Gemini), *Harness voice engine* or *Gemini*.
- **Command** (what the page runs, and what the skill tells Claude to run):

  ```bash
  python3 /root/maxxing/extensions/transcription/transcribe.py <url|file> [-o out.txt] [--engine auto|harness|gemini]
  ```

## What is sent where

| Source | Engine | What leaves the server |
|---|---|---|
| YouTube link | Gemini | the link (Google reads the video) |
| File or other link | Harness, local Whisper | nothing |
| File or other link | Harness, Groq or OpenAI | the audio, to Groq or OpenAI (as for your voice messages) |
| File or other link | Gemini | the audio (mono, 32 kb/s), uploaded to the Gemini Files API and deleted after the transcript |
| Other link | any | the download request to that site (yt-dlp) |

The Gemini key stays in the Keys vault and is given only to the transcription process. Jobs, texts and uploads stay
in the extension's state folder on your server; an upload is deleted as soon as its job ends.

## Update and remove

- Update: `git -C /root/maxxing/extensions/transcription pull`, then **Settings -> Extensions -> Reload**.
- Remove: delete `/root/maxxing/extensions/transcription` and the link `/root/maxxing/.claude/skills/transcription`,
  then **Reload**. Optionally remove `GEMINI_API_KEY` from Settings -> Keys.

## Development

```bash
python3 -m pytest -q tests/      # no network, no engine
node --check page.js
```

## License

[MIT](LICENSE). Made by [Digitis](https://www.digitis.be), a Belgian cloud telephony operator that runs Harness
Maxxing every day.

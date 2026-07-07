# Demo video (1 minute)

A scripted, AI-narrated screencast of the dashboard: **Playwright** drives the app through
a fixed tour and records the screen; your **TTS** clips become the narration; **ffmpeg**
muxes them — paced so video and voice line up.

```
beats.py             storyboard: narration text + on-screen action + fallback timing (source of truth)
prepare_audio.py     your beat_*.wav -> output/narration.wav + output/timings.json + output/narration.srt
export_storyboard.py beats.py -> output/storyboard.json (for the Node recorder)
record_tour.js       drives the running web-app, records output/tour.webm (paced to timings.json)
Makefile             install / texts / audio / record / video / all
```

> **Why Node for the recorder?** Playwright's *Python* sync driver segfaults on some Linux
> boxes (a driver-transport crash, unrelated to Chromium). The bundled **Node** driver +
> Chromium are rock-solid, so `make record` runs `record_tour.js` on the very browser you
> installed with `playwright install chromium` — no extra downloads, nothing else to set up.

## Prerequisites

- The **web-app running** at `DEMO_URL` (default `http://127.0.0.1:8080`): `make -C .. dev`.
- **agent-service running** too, if you want the SHAP explanation and weather-chat beats to
  show real AI answers (otherwise those bubbles show the connection notice).
- **ffmpeg + ffprobe** on PATH (`apt install ffmpeg`).
- Playwright + Chromium (installed by `make install`).

## Flow

```bash
cd web-app/demo
make install                       # venv + Playwright
.venv/bin/playwright install chromium

# 1) Get the narration text for your TTS tool (one file per beat):
make texts                         # -> output/beat_1.txt .. output/beat_5.txt

# 2) Generate a WAV per beat with your TTS, save as:
#      demo/audio/beat_1.wav .. beat_5.wav
#    (aim for roughly the seconds in beats.py; exact length is fine — the video adapts)

# 3) Assemble the narration + timing:
make audio                         # -> output/narration.wav, output/timings.json, output/narration.srt

# 4) Record the screen tour (paced to the real audio):
make record                        # -> output/tour.webm   (HEADED=1 make record to watch)

# 5) Mux into the final video:
make video                         # -> output/demo.mp4
# or with burned-in Japanese subtitles:
make video-subs                    # -> output/demo_subbed.mp4
```

`make all` runs steps 3–5 in one go (after you've placed the beat WAVs).

## Tuning

- **Edit narration / pacing** in `beats.py` (text + fallback `seconds`). Re-run from step 1.
- **Feature a specific store**: `DEMO_STORE="千葉県…店" make record` (default: highest-demand
  store). Pick one covered by the weather forecast so the weather beat shows data.
- **No TTS yet?** `make record` alone works — it falls back to the `seconds` in `beats.py`
  and produces a silent tour you can voice over later.
- **Different URL**: `DEMO_URL=http://192.168.x.y:8080 make record`.

## Notes

- Recording is deterministic and re-runnable — tweak and re-record until it's clean.
- The live chat/SHAP beats take real GPU time; the beat durations include headroom, but if
  your model is slow, bump beats 4–5 `seconds` (or the TTS length) so the answer finishes
  on screen.
- `output/` and `audio/*.wav` are git-ignored.

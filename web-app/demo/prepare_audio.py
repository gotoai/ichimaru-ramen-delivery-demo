"""Assemble the narration from your per-beat TTS clips, and derive the timing.

You generate one WAV per beat with your TTS tool (aim for roughly the `seconds` in
beats.py, but exact length doesn't matter — the video is paced to match). Drop them in
demo/audio/ as beat_1.wav … beat_5.wav. This script then:

  * normalizes each clip (or inserts silence of the fallback length if a clip is missing),
  * concatenates them into output/narration.wav,
  * writes output/timings.json (per-beat start + measured duration) — record_tour.js reads
    this to pace the screen capture so video ↔ voice line up,
  * writes output/narration.srt (subtitles from the beat text at the measured timings).

Needs ffmpeg + ffprobe on PATH. Run BEFORE record_tour.js.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile

import beats

HERE = pathlib.Path(__file__).parent
AUDIO_IN = HERE / "audio"
OUT = HERE / "output"
SR, CH = 44100, 2


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def _probe_dur(path: pathlib.Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def _norm_clip(src: pathlib.Path, dst: pathlib.Path) -> None:
    _run(["ffmpeg", "-y", "-i", str(src), "-ar", str(SR), "-ac", str(CH), str(dst)])


def _silence(seconds: float, dst: pathlib.Path) -> None:
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i",
          f"anullsrc=r={SR}:cl=stereo", "-t", f"{seconds}", str(dst)])


def _srt_ts(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    ms = int((s - int(s)) * 1000)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp())
    segs, meta, start = [], [], 0.0

    for beat in beats.BEATS:
        clip = AUDIO_IN / f"beat_{beat['id']}.wav"
        seg = tmp / f"seg_{beat['id']}.wav"
        if clip.exists():
            _norm_clip(clip, seg)
        else:
            print(f"  beat {beat['id']}: no audio → {beat['seconds']}s silence placeholder")
            _silence(beat["seconds"], seg)
        dur = _probe_dur(seg)
        segs.append(seg)
        meta.append({"id": beat["id"], "start": round(start, 3),
                     "dur": round(dur, 3), "text": beat["text"]})
        start += dur

    # Concatenate segments -> narration.wav
    listfile = tmp / "list.txt"
    listfile.write_text("".join(f"file '{s}'\n" for s in segs))
    narration = OUT / "narration.wav"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
          "-c", "copy", str(narration)])

    (OUT / "timings.json").write_text(
        json.dumps({"beats": meta, "total": round(start, 3)}, ensure_ascii=False, indent=2))

    srt = []
    for i, m in enumerate(meta, 1):
        srt.append(f"{i}\n{_srt_ts(m['start'])} --> {_srt_ts(m['start'] + m['dur'])}\n{m['text']}\n")
    (OUT / "narration.srt").write_text("\n".join(srt), encoding="utf-8")

    print(f"\nWrote {narration} ({start:.1f}s), output/timings.json, output/narration.srt")


if __name__ == "__main__":
    main()

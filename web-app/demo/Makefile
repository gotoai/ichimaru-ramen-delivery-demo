# 1-minute demo video: Playwright screen capture + your TTS narration, muxed with ffmpeg.
# See README.md for the full flow. Needs ffmpeg/ffprobe on PATH.

VENV := .venv
PY := $(VENV)/bin/python
SUB_STYLE := FontName=Noto Sans CJK JP,FontSize=18,OutlineColour=&H80000000,BorderStyle=3

# The tour runs on the bundled Playwright JS driver (its Node + Chromium), because the
# Playwright *Python* sync driver segfaults on some Linux boxes. Resolve the driver dir
# from the installed package so we reuse the browser you already installed.
DRIVER := $(shell $(PY) -c "import playwright,os;print(os.path.join(os.path.dirname(playwright.__file__),'driver'))" 2>/dev/null)

.PHONY: help install texts audio record video video-subs all clean

help:
	@echo "make install    - venv + Playwright (then: $(VENV)/bin/playwright install chromium)"
	@echo "make texts      - write output/beat_N.txt (narration per beat) for your TTS tool"
	@echo "make audio      - assemble output/narration.wav + timings.json + narration.srt from demo/audio/beat_*.wav"
	@echo "make record     - drive the running web-app and record output/tour.webm (paced to timings.json)"
	@echo "make video      - mux tour.webm + narration.wav -> output/demo.mp4"
	@echo "make video-subs - same, with burned-in Japanese subtitles -> output/demo_subbed.mp4"
	@echo "make all        - audio -> record -> video"

install:
	python3.12 -m venv $(VENV)
	$(PY) -m pip install -U pip && $(PY) -m pip install -r requirements.txt
	@echo "Now run: $(VENV)/bin/playwright install chromium"

texts:
	@mkdir -p output
	@$(PY) -c "import beats; [open(f'output/beat_{b[\"id\"]}.txt','w').write(b['text']) for b in beats.BEATS]; print('wrote output/beat_1.txt .. output/beat_%d.txt' % len(beats.BEATS))"

audio:
	$(PY) prepare_audio.py

record:
	$(PY) export_storyboard.py
	PW_DRIVER='$(DRIVER)/package' HEADED='$(HEADED)' '$(DRIVER)/node' record_tour.js

# preroll = seconds of page-load/settle before beat 1 (from record); trimmed so the video
# aligns with the narration. Defaults to 0 if meta.json is absent.
PREROLL = $(shell $(PY) -c "import json,os;print(json.load(open('output/meta.json'))['preroll'] if os.path.exists('output/meta.json') else 0)" 2>/dev/null || echo 0)

video:
	ffmpeg -y -ss $(PREROLL) -i output/tour.webm -i output/narration.wav \
		-c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest output/demo.mp4
	@echo "Wrote output/demo.mp4"

video-subs:
	ffmpeg -y -ss $(PREROLL) -i output/tour.webm -i output/narration.wav \
		-vf "subtitles=output/narration.srt:force_style='$(SUB_STYLE)'" \
		-c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest output/demo_subbed.mp4
	@echo "Wrote output/demo_subbed.mp4"

all: audio record video

clean:
	rm -rf output

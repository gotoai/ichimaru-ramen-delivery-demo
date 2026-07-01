# agent-service — Local Install (CUDA + PyTorch)

Target box: Ubuntu 24.04, NVIDIA GPU with ~16GB VRAM (Gemma 4 E4B runs 4-bit, ~5-7GB).
This service has its **own venv and dependencies**, separate from the data pipeline.

Follow the steps in order. After each **✅ Check**, confirm the expected output before
continuing. Report back at any step that fails.

`requirements.txt` covers the pip-resolvable libraries; the steps below add the parts a
requirements file can't portably capture — the GPU driver and the **CUDA-matched
PyTorch wheel** (which depend on your box).

---

## 0. Verify the GPU driver

```bash
nvidia-smi
```

**✅ Check:** you see your GPU, its memory, and a "CUDA Version: XX.X" (top-right) — the
*driver-supported* CUDA. You do **not** need to install the CUDA toolkit (`nvcc`)
separately; PyTorch ships its own CUDA runtime. If `nvidia-smi` is missing, install the
driver first (`sudo ubuntu-drivers autoinstall`, reboot) and re-run.

---

## 1. Create the venv (in this directory)

```bash
cd agent-service
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

**✅ Check:** your prompt is prefixed with `(.venv)`. Re-activate later with
`source .venv/bin/activate` in each new shell.

---

## 2. Install PyTorch (CUDA build), then the rest

Install torch **first and separately** — the right wheel depends on your driver's CUDA,
so it is deliberately kept out of `requirements.txt`:

```bash
pip install torch torchvision            # default wheel bundles a recent CUDA runtime
pip install -r requirements.txt          # transformers>=5.10.1, accelerate, bitsandbytes, pillow, timm, python-dotenv
```

**✅ Check — PyTorch sees the GPU:**

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Expected: a version, `True`, and your GPU name (e.g. `2.x.x+cu124 True NVIDIA GeForce RTX ...`).
If it prints `False`, the driver/wheel CUDA versions don't match — reinstall torch from the
matching index, e.g.:

```bash
pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu121
# or .../whl/cu126 — pick the one at or below your nvidia-smi CUDA version
```

---

## 3. Configure `.env` (model + local cache)

```bash
cp .env.example .env
```

Then edit `.env`:
- `MODEL_ID=google/gemma-4-E4B-it`
- `HF_HOME` — point at a **shared** Hugging Face cache so the ~8GB model isn't
  re-downloaded per venv (e.g. reuse the gemma test project's `.hf_cache`, or a
  home-level cache). The app loads `.env` **before** importing transformers, so `HF_HOME`
  takes effect for the download.
- `AGENT_API_KEY` — leave blank for now (used by the future web API, not the CLI):
  generate later with `python -c "import secrets; print('agt_' + secrets.token_urlsafe(32))"`.

**✅ Check:** `python -c "from agent.config import MODEL_ID, HF_HOME; print(MODEL_ID, HF_HOME)"`
prints your model id and cache path.

---

## 4. Smoke test — does the model load and reply?

```bash
python tests/smoke_test.py
```

**✅ Check:** after the one-time ~8GB download you see a `MODEL REPLY:` line and a peak
VRAM figure around 5–7 GB. Watch memory live in another terminal with `watch -n1 nvidia-smi`.

---

## 5. Run the tasks (spike)

```bash
mkdir -p .tmp
python -m agent.cli extract --location 東京都世田谷区 --limit-items 8 > .tmp/events.json
python -m agent.cli attendance --input .tmp/events.json
python -m agent.cli present --input .tmp/events.json --style bullet
```

`extract` reads the pipeline's `DATA/s08_search/searched_events.tsv` by default (run the
`search-events` skill first).

---

## Troubleshooting

- `KeyError: 'gemma4'` / unknown model type → transformers too old; `pip install -U "transformers>=5.10.1"`.
- `torch.cuda.is_available()` is `False` → driver/wheel CUDA mismatch (see step 2's `--index-url`).
- `CUDA out of memory` during generation (model loaded, OOM at the forward pass) → the
  prompt is too long. `extract` truncates each result's `content` to
  `MAX_CONTENT_CHARS` (in `agent/tasks/extract.py`); if it still OOMs, lower that or feed
  fewer items with `--limit-items 4`. `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`
  is set automatically to reduce fragmentation. Also confirm the 4-bit
  `BitsAndBytesConfig` is active (it is, in `agent/llm.py`) and close other GPU apps
  (`nvidia-smi`); or use the QAT variant `google/gemma-4-E4B-it-qat-mobile-transformers`.
- Slow / repeated downloads → make sure `HF_HOME` in `.env` points at your shared cache.

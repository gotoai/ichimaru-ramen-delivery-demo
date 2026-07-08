"""Smoke test — does Gemma 4 12B load and reply through agent.llm?

    cd agent-service && source .venv/bin/activate
    python tests/smoke_test.py

Prints the reply and peak VRAM. Requires the model download (~8GB) and a CUDA GPU.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # import `agent` without install

from agent.llm import get_llm, text_message  # noqa: E402


def main() -> int:
    llm = get_llm()
    reply = llm.generate(
        [text_message("user", "Reply with exactly: Gemma 4 is running locally.")],
        do_sample=False, max_new_tokens=32,
    )
    print("\nMODEL REPLY:", reply)
    try:
        import torch
        print("VRAM peak: %.1f GB" % (torch.cuda.max_memory_allocated() / 1e9))
        print("Max context:", llm.max_context())
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

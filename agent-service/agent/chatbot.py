#!/usr/bin/env python3
"""
chatbot.py — interactive REPL for the Ichimaru analytics agent (agent.analyst).

A thin front-end over agent.analyst: it loads the model, keeps a simple conversation
history, and drives one agentic turn per input — printing each tool call live. The agent
answers by querying the s10_analysis DuckDB layer (run_sql) through a sandbox. The same
agent.analyst.answer() backs the API's POST /v1/chat.

Run from agent-service/ (model + .env/HF_HOME come from agent.config / agent.llm):

    .venv/bin/python -m agent.chatbot         # or: .venv/bin/python agent/chatbot.py

Chat commands:  /reset  clear conversation   ·   /exit (Ctrl-D)  quit
"""
import sys
from pathlib import Path

_AGENT_SERVICE = Path(__file__).resolve().parents[1]
if str(_AGENT_SERVICE) not in sys.path:
    sys.path.insert(0, str(_AGENT_SERVICE))

from agent import analyst, config      # noqa: E402  (analyst is torch-free)
from agent.llm import get_llm          # noqa: E402


def _print_step(step: dict) -> None:
    arg = step["input"].get("query") or step["input"].get("command") or str(step["input"])
    print(f"\x1b[2m  [tool] {step['tool']}: {arg}\x1b[0m", flush=True)
    out = step["output"]
    preview = out if len(out) <= 800 else out[:800] + "\n[...]"
    print("\x1b[2m" + "\n".join("  | " + ln for ln in preview.splitlines()) + "\x1b[0m", flush=True)


def main() -> int:
    print(f"Loading {config.MODEL_ID} (4-bit) ... this takes a moment.", flush=True)
    llm = get_llm()

    history: list[dict] = []
    print("\nAnalytics agent ready. Ask about forecasts, accuracy, weather, events.")
    print("Type a message, or /reset, /exit.\n")

    while True:
        try:
            user_input = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not user_input:
            continue
        if user_input == "/exit":
            print("Bye.")
            break
        if user_input == "/reset":
            history = []
            print("(conversation cleared)\n")
            continue

        result = analyst.answer(user_input, history=history, llm=llm, on_step=_print_step)
        answer = result["message"]
        history.append({"role": "user", "text": user_input})
        history.append({"role": "assistant", "text": answer})
        print(f"\nAgent> {answer}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

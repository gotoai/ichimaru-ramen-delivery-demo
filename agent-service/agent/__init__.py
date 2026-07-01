"""agent-service — Gemma 4 E4B agent for the Ichimaru demo.

Public surface (import lazily; `llm`/`tasks` pull in torch/transformers on use):

    from agent.llm import get_llm
    from agent.tasks import extract, attendance, present
"""

__version__ = "0.1.0"

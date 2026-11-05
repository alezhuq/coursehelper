"""The chat pipeline's deterministic fast-lane suite.

Every module here is a CI-gating guardrail/eval test: real Postgres, a
stubbed LLM (no live Ollama required), and one focused failure-mode or
guarantee per module - the exact set of Critical/High checks for the chat pipeline. Run the whole
lane with `pytest api/tests/chat/eval_code/`.
"""

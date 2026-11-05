"""The grounding gate: a plain, pure-function check between
retrieve and generate.

Kept deliberately trivial and side-effect-free so the route's one
conditional branch is trivially unit-testable without any DB/HTTP
dependency. This is the code-level gate - not a prompted behavior - that
guarantees NO LLM call is ever made when retrieval finds zero relevant
chunks.
"""


def is_empty_retrieval(fused_ids: list[str]) -> bool:
    return not fused_ids

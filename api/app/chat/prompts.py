"""Static, versioned prompt constants.

Never string-formatted with retrieved document content or user input - the
user turn (assembled in `app/chat/routes.py::build_messages`) carries the
numbered `<source>` blocks instead, so untrusted PDF text always enters the
model as data in the user role, never as an instruction in the system role
(prompt-injection defense).

`REFUSAL_MESSAGE` is the ONE source of truth for the fixed "materials don't
cover this" string: `app/chat/gate.py` and `app/chat/routes.py`
import it rather than re-literalizing it, and `SYSTEM_PROMPT` embeds the
identical string so the model's own instructed refusal (the second line of
defense for partial-grounding cases the empty-retrieval gate cannot catch)
matches exactly.
"""

REFUSAL_MESSAGE = "The course materials don't cover this question."

SYSTEM_PROMPT = f"""You are a study assistant for this course. Answer ONLY using
the numbered <source> blocks provided in the user message. After every claim
drawn from a source, cite it immediately as [n] matching that source's number.
If the provided sources do not answer the question, reply exactly: "{REFUSAL_MESSAGE}"
Do not use any knowledge beyond the provided sources, even if you believe it to be true."""

CONDENSE_SYSTEM_PROMPT = """You rewrite a student's follow-up question into a
standalone retrieval query using the prior conversation turns for context.
Preserve the exact concept, algorithm, or entity the student is referring to
- do not narrow, broaden, or substitute a different concept. Return ONLY a
JSON object: {"standalone_query": "..."}"""

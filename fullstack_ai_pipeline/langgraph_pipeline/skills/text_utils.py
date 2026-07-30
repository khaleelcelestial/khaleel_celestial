"""
Shared helpers for pulling structured content out of raw LLM responses.
"""


def cap_report(text: str, limit: int = 600) -> str:
    """
    Cap a free-text agent report (testing_report/deployment_status) before
    it's stored in state. These get re-read as context by every later
    Supervisor round AND every Backend/Frontend retry round until the next
    Testing/Deployment pass overwrites them - left uncapped, the same large
    narrative blob gets re-sent as input tokens over and over across a
    20-round loop. The structured fields (review_issues, quality_passed,
    stage_status) already carry the actionable specifics; this text is
    supplementary color, not the primary routing signal, so a short prefix
    plus a note of how much was cut is enough.
    """
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"... [truncated, {len(text) - limit} more chars]"


def extract_code_block(text: str, lang: str = "") -> str:
    """
    Extract the outermost fenced code block from an LLM response.

    Uses the LAST ``` in the text as the closing fence (not the first),
    so nested/example fences inside the block's own content (e.g. a
    ```bash install snippet inside a generated README.md) don't truncate
    the real block early - which is what was causing "Unterminated
    string" JSON parse errors on every doc-generation call.
    """
    text = text.strip()

    marker = f"```{lang}" if lang else "```"
    start_idx = text.find(marker)

    if start_idx == -1 and lang:
        # no lang-tagged fence, fall back to a bare fence
        start_idx = text.find("```")
        marker = "```"

    if start_idx == -1:
        return text  # no fence at all, use the raw text as-is

    content_start = start_idx + len(marker)
    end_idx = text.rfind("```")

    if end_idx <= content_start:
        # no distinct closing fence after the opener - take the rest
        return text[content_start:].strip()

    return text[content_start:end_idx].strip()


def repair_truncated_json(text: str) -> str:
    """
    Best-effort repair for a JSON object/array cut off mid-generation (e.g.
    the response hit the model's max_tokens limit while still inside a
    string value). Closes any open string and any unclosed braces/brackets
    so json.loads has a chance of parsing the (truncated but now
    syntactically valid) result, instead of failing outright on
    "Unterminated string" / "Expecting ',' delimiter" etc.

    This can't recover content that never got generated - callers should
    still fall back to a default value if the repaired text also fails to
    parse.
    """
    in_string = False
    escape = False
    stack = []

    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()

    repaired = text
    if in_string:
        repaired += '"'
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"

    return repaired

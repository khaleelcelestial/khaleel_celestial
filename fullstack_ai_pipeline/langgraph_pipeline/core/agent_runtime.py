"""
Tool-calling agent runner built on LangGraph's own prebuilt ReAct agent
(langgraph.prebuilt.create_react_agent) - the actual LangChain/LangGraph
agent component, not a hand-rolled call/observe loop. It owns its own
internal message loop and tool-execution node; this module's job is just to
bind it to the right model (via model_router, preserving the provider-
fallback resilience the rest of the pipeline relies on) and hand back the
final text.
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langgraph.prebuilt import create_react_agent

from core.model_router import get_router
from core.logger import get_logger

# Safety backstop for the ReAct agent's own internal loop (model call + tool
# call counts as roughly 2 steps) - not the primary control (the model
# decides when it's done by not calling another tool), just a guard against
# a single agent looping forever.
# Reduced from 12 to 8 to prevent excessive API calls and rate limiting
MAX_TOOL_STEPS = 8

# Deliberately LOWER than MAX_TOOL_STEPS - the Gateway path (see
# _run_gateway_react_agent below) has no real "empty tool_calls" stop signal
# the way native tool-calling does, only a text pattern the model might not
# follow reliably. A tight step cap bounds the blast radius of that gap
# (confirmed by direct testing: the model once invented an extra, unrequested
# file after its actual task was already complete) - if it hasn't produced a
# clean non-tool-call final answer by then, treat the whole attempt as
# inconclusive and fall back to the proven native-tool-calling path instead
# of trusting an ambiguous, possibly-runaway Gateway exchange.
GATEWAY_MAX_TOOL_STEPS = 4

_FENCED_TOOL_CALL_PATTERN = re.compile(r"```tool_call\s*\n(.*?)```", re.DOTALL)
_CONTENT_BLOCK_PATTERN = re.compile(r"```content\s*\n(.*?)```", re.DOTALL)


def _extract_tool_call(content: str):
    """
    Looks for a tool_call JSON object in the Gateway's response. Tolerates
    the model omitting the ```tool_call fence entirely - confirmed by direct
    testing, it sometimes just writes "tool_call\\n{...}" with no backticks
    at all, which a strict fence-only regex silently misses and (worse)
    would then treat as if it were a genuine plain-text final answer instead
    of the tool-call attempt it actually was.

    Returns the parsed dict, or None if there's truly no tool-call intent
    anywhere (a real final answer). Raises ValueError if the word
    "tool_call" appears but no valid JSON follows it - that's a real parse
    failure, not "no tool call", and must propagate as one (the caller falls
    back to native tool-calling on any exception) rather than silently
    passing broken text through as if it were a valid response.
    """
    fenced = _FENCED_TOOL_CALL_PATTERN.search(content)
    if fenced:
        return json.loads(fenced.group(1).strip())

    idx = content.find("tool_call")
    if idx == -1:
        return None  # no tool-call intent anywhere - a genuine final answer

    brace_match = re.search(r"\{.*\}", content[idx:], re.DOTALL)
    if not brace_match:
        raise ValueError("response mentions 'tool_call' but no JSON object follows it")
    return json.loads(brace_match.group(0))


def _gateway_tools_prompt(tools: list) -> str:
    """Describe each tool's name/args to the model, in the exact text format it must use to call one."""
    lines = ["You have these tools available:"]
    for t in tools:
        args_desc = ", ".join(f'"{name}": <{info.get("type", "any")}>' for name, info in (t.args or {}).items())
        lines.append(f'- {t.name}({args_desc}): {t.description}')
    lines.append("""
To call a tool, output EXACTLY this and nothing else in your response:
```tool_call
{"name": "<tool name>", "arguments": {...}}
```
Only call a tool if the requested task is NOT yet complete. Once it genuinely is complete, respond
with a plain text summary and NO tool_call block - do not invent additional changes nobody asked for
just because you could keep going. One tool call per response, never more than one.

IMPORTANT for write_file specifically: do NOT put "content" inside the arguments JSON - multi-line
code containing quotes/backslashes/newlines is extremely error-prone to JSON-escape correctly and
frequently produces invalid JSON. Instead, leave "content" OUT of the arguments entirely and put the
raw file content, completely unescaped, in a separate fenced block immediately after, like this:
```tool_call
{"name": "write_file", "arguments": {"path": "backend/main.py"}}
```
```content
<the exact raw file content goes here - no escaping, no quotes needed>
```""")
    return "\n".join(lines)


_MUTATING_TOOLS = ("write_file", "delete_file")


def _snapshot_before_mutation(tools_by_name: dict, tool_name: str, arguments: dict):
    """
    Before a write_file/delete_file call, record what was at that path
    beforehand (via the read_file tool, if this agent has one) so a failed
    Gateway attempt can be rolled back instead of leaving real files
    corrupted for whatever runs next. Returns (path, prior_content_or_None),
    or None if there's no path argument / no way to snapshot it.
    """
    path = arguments.get("path")
    if not path:
        return None
    read_tool = tools_by_name.get("read_file")
    if read_tool is None:
        return (path, None)
    try:
        content = read_tool.invoke({"path": path})
    except Exception:
        return (path, None)
    return (path, None if content.startswith("ERROR") else content)


def _rollback(tools_by_name: dict, undo_log: list) -> None:
    """
    Best-effort restore of every write_file/delete_file this Gateway attempt
    made, in reverse order - confirmed necessary by direct testing: a Gateway
    exchange that hits its step cap partway through can have already
    overwritten a real, correct file with placeholder garbage (observed: a
    working 2.7KB router file replaced with 3 characters) before the whole
    attempt gets discarded as inconclusive. Without this, the native
    tool-calling fallback that runs next would inherit that corruption
    instead of the clean state the round actually started from.
    """
    write_tool = tools_by_name.get("write_file")
    delete_tool = tools_by_name.get("delete_file")
    logger = get_logger()
    for path, prior_content in reversed(undo_log):
        try:
            if prior_content is not None:
                if write_tool:
                    write_tool.invoke({"path": path, "content": prior_content})
            elif write_tool or delete_tool:
                # Didn't exist before this attempt touched it - remove
                # whatever's there now rather than leave a new, unvetted file.
                if delete_tool:
                    delete_tool.invoke({"path": path})
        except Exception as e:
            logger.warning(f"  Rollback of '{path}' after a failed Gateway attempt didn't fully succeed: "
                           f"{str(e)[:150]}")


def _run_gateway_react_agent(system_prompt: str, user_message: str, tools: list) -> str:
    """
    Text-based tool-calling emulation against the remote AI Gateway, which
    has no native structured tool-calling protocol (confirmed by direct
    testing - its /chat endpoint accepts a "tools" schema and silently
    ignores it, hallucinating a fake tool result as plain text instead of
    ever returning anything to actually parse). This teaches the model a
    specific parseable text pattern instead, and executes whatever tool call
    it emits ourselves.

    Every write_file/delete_file call is recorded before it happens and
    rolled back if the attempt as a whole ends up failing (see _rollback) -
    a partially-completed, ultimately-discarded Gateway attempt must not
    leave real files in a worse state than it found them.

    Raises on ANY ambiguity (a malformed/missing tool_call, an unknown tool
    name, the step cap reached without a clean finish, or the Gateway call
    itself erroring) rather than guessing - the caller (run_tool_agent) falls
    back to the proven native-tool-calling Ollama path on any exception here,
    so failing loudly and immediately is the safe choice, not a silent guess.
    """
    from core.model_router import _create_aigateway_client

    client = _create_aigateway_client(model_name="auto", api_key=None, temperature=0.1)
    tools_by_name = {t.name: t for t in tools}
    undo_log = []

    messages = [
        SystemMessage(content=system_prompt + "\n\n" + _gateway_tools_prompt(tools)),
        HumanMessage(content=user_message),
    ]

    try:
        for _step in range(GATEWAY_MAX_TOOL_STEPS):
            response = client.invoke(messages)
            content = response.content or ""

            try:
                call = _extract_tool_call(content)
            except (json.JSONDecodeError, ValueError) as e:
                raise RuntimeError(f"Gateway emitted an unparseable tool_call: {e}") from e

            if call is None:
                return content  # no tool-call intent - treat as the final answer

            try:
                tool_name, arguments = call["name"], call.get("arguments", {})
            except (KeyError, TypeError) as e:
                raise RuntimeError(f"Gateway's tool_call JSON is missing 'name': {e}") from e

            if tool_name not in tools_by_name:
                raise RuntimeError(f"Gateway tried to call unknown tool '{tool_name}'")

            # write_file's content may have been supplied as a separate raw
            # ```content block instead of JSON-escaped inside arguments (see
            # _gateway_tools_prompt) - this is the fix for the dominant
            # real-world failure mode: a model failing to perfectly
            # JSON-escape a multi-line file's quotes/backslashes/newlines.
            if tool_name == "write_file" and "content" not in arguments:
                content_match = _CONTENT_BLOCK_PATTERN.search(content)
                if not content_match:
                    raise RuntimeError("write_file call is missing 'content' and no ```content block follows it")
                arguments = dict(arguments)
                arguments["content"] = content_match.group(1)

            if tool_name in _MUTATING_TOOLS:
                snapshot = _snapshot_before_mutation(tools_by_name, tool_name, arguments)
                if snapshot is not None:
                    undo_log.append(snapshot)

            tool_result = tools_by_name[tool_name].invoke(arguments)
            messages.append(AIMessage(content=content))
            messages.append(HumanMessage(content=f"TOOL_RESULT: {tool_result}"))

        raise RuntimeError(f"Gateway exchange hit its {GATEWAY_MAX_TOOL_STEPS}-step cap without a clean "
                           f"final answer - treating as inconclusive rather than trusting a possibly-runaway exchange")
    except Exception:
        if undo_log:
            get_logger().info(f"  Rolling back {len(undo_log)} file write(s) from the failed Gateway attempt")
            _rollback(tools_by_name, undo_log)
        raise


# Per-process (i.e. per pipeline run - a fresh `main.py` invocation starts at
# zero) count of consecutive Gateway failures, keyed by skill_name. Once a
# skill has failed this many times in a row THIS run, stop attempting the
# Gateway for it and go straight to the native path - confirmed necessary by
# direct observation: every write_file-heavy round was paying for a doomed
# ~4-step, 10-90s Gateway attempt before falling back, over and over, for
# the same failure reason each time. Reset to 0 the moment the Gateway
# succeeds again for that skill (a transient issue shouldn't permanently
# disable it for the rest of the run).
#
# Write-heavy agents (backend_agent, frontend_agent) are pre-initialized to
# the threshold so they skip Gateway entirely from the start - Gateway's
# text-based tool emulation is unreliable for multi-file write operations.
_gateway_failure_streak: dict = {
    "backend_agent": 999,
    "frontend_agent": 999,
}
GATEWAY_CIRCUIT_BREAKER_THRESHOLD = 3


def run_tool_agent(skill_name: str, system_prompt: str, user_message: str, tools: list) -> tuple[str, bool]:
    """
    Run one agent to completion. Tries the remote AI Gateway first via a
    text-based tool-call emulation (see _run_gateway_react_agent) - if that
    raises for ANY reason (Gateway unreachable, malformed output, step cap
    hit), falls through unchanged to the proven path: a real LangGraph ReAct
    agent bound to model_router's native-tool-calling chat model for
    `skill_name` (Ollama primary, Groq/Mistral fallback). Skips the Gateway
    attempt entirely once it's failed GATEWAY_CIRCUIT_BREAKER_THRESHOLD times
    in a row for this skill_name this run (see _gateway_failure_streak).

    Returns (final_text, ok) - ok is False whenever every provider/fallback
    was exhausted, so callers can tell "the agent genuinely finished and
    reported a problem" apart from "the agent never ran at all" instead of
    guessing from the text (a rate-limit message doesn't reliably contain
    any particular keyword).
    """
    router = get_router()
    logger = get_logger()

    if _gateway_failure_streak.get(skill_name, 0) < GATEWAY_CIRCUIT_BREAKER_THRESHOLD:
        try:
            result = _run_gateway_react_agent(system_prompt, user_message, tools)
            _gateway_failure_streak[skill_name] = 0
            logger.info("  Gateway tool-call exchange succeeded - skipping native Ollama/cloud path this call")
            return result, True
        except Exception as e:
            _gateway_failure_streak[skill_name] = _gateway_failure_streak.get(skill_name, 0) + 1
            logger.warning(f"  Gateway tool-call attempt unavailable ({str(e)[:150]}) - "
                           f"falling back to native tool-calling (Ollama/cloud)")
    else:
        logger.info(f"  Skipping Gateway for '{skill_name}' - failed "
                    f"{GATEWAY_CIRCUIT_BREAKER_THRESHOLD}+ times in a row this run, going straight to native")

    chain_length = router.chain_length(skill_name)

    for attempt in range(chain_length):
        try:
            client, model_name, _temperature = router.get_client(skill_name, attempt)
            logger.model_attempt(model_name, attempt)

            react_agent = create_react_agent(client, tools, prompt=system_prompt)
            result = react_agent.invoke(
                {"messages": [HumanMessage(content=user_message)]},
                config={"recursion_limit": MAX_TOOL_STEPS * 2 + 2},
            )
            final_message = result["messages"][-1]
            return final_message.content or "", True

        except Exception as e:
            will_retry = attempt + 1 < chain_length
            logger.model_attempt_failed(attempt, str(e), will_retry)
            if will_retry:
                # Add small delay before retry to avoid rapid rate limit exhaustion
                import time
                time.sleep(2)
                continue
            return f"Agent unavailable - all {chain_length} configured credential(s) exhausted: {str(e)[:200]}", False

    return "Agent unavailable - no attempts succeeded.", False

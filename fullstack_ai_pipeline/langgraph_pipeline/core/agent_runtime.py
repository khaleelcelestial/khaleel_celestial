"""
Tool-calling agent runner built on LangGraph's own prebuilt ReAct agent
(langgraph.prebuilt.create_react_agent) - the actual LangChain/LangGraph
agent component, not a hand-rolled call/observe loop. It owns its own
internal message loop and tool-execution node; this module's job is just to
bind it to the right model (via model_router, preserving the provider-
fallback resilience the rest of the pipeline relies on) and hand back the
final text.

Every provider in a tool-calling skill's fallback chain (Azure, Mistral,
Groq - see core/model_config.py) has genuine native tool-calling support
via its LangChain integration, so this is just the ReAct loop plus
provider fallback - no text-emulation layer needed (a prior AI Gateway
text-based tool-call emulation path was removed once every tool-calling
tier moved to providers with real function-calling support).

The ONLY skill still going through this ReAct loop is deployment_agent
(CICD_RUN) - Database/Backend/Frontend generation was moved OFF this path
entirely (see skills/incremental_codegen.py's module docstring) after a
ReAct-based "incremental" generation mode was confirmed, live, to cause
quadratic token growth: a single round measured ~330K-460K input tokens
against ~9K tokens of actual unique file content, because ReAct resends the
ENTIRE accumulated conversation (every prior tool call and result) on every
internal step. Raising MAX_TOOL_STEPS to fix a DIFFERENT bug (rounds running
out of budget before writing anything) made that quadratic cost worse, not
better - the real fix was architectural (deterministic scope selection +
one generation call, no growing conversation), not a bigger step cap. Do
NOT raise this constant to solve a code-generation problem - if generation
ever needs more room, that is a signal something is routing through
run_tool_agent that should be using select_generation_scope +
run_batch_generation instead.
"""

from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

from core.model_router import get_router
from core.logger import get_logger

# Safety backstop for the ReAct agent's own internal loop (model call + tool
# call counts as roughly 2 steps) - not the primary control (the model
# decides when it's done by not calling another tool), just a guard against
# a single agent looping forever. deployment_agent's own prompt already
# asks it to minimize tool calls (one docker compose ps, logs only for
# unhealthy services), so this only needs to be large enough for that
# genuinely adaptive, bounded verification task - not code generation.
MAX_TOOL_STEPS = 8


def run_tool_agent(skill_name: str, system_prompt: str, user_message: str, tools: list) -> tuple[str, bool]:
    """
    Run one agent to completion: a real LangGraph ReAct agent bound to
    model_router's native-tool-calling chat model for `skill_name` (Azure
    primary, Mistral then Groq fallback - see core/model_config.py).

    Returns (final_text, ok) - ok is False whenever every provider/fallback
    was exhausted, so callers can tell "the agent genuinely finished and
    reported a problem" apart from "the agent never ran at all" instead of
    guessing from the text (a rate-limit message doesn't reliably contain
    any particular keyword).
    """
    from core.model_config import SKILL_TO_STAGE, SKILL_TO_OPERATION

    router = get_router()
    logger = get_logger()
    stage = SKILL_TO_STAGE.get(skill_name, "other")
    operation = SKILL_TO_OPERATION.get(skill_name, "other")

    chain_length = router.chain_length(skill_name)

    for attempt in range(chain_length):
        account, provider, _model_key = router.chain_info(skill_name, attempt)
        model_name = None
        call_id = logger.llm_call_start()
        try:
            client, model_name, _temperature = router.get_client(skill_name, attempt)
            logger.model_attempt(model_name, attempt, provider=provider, account=account, stage=stage)

            # Force one tool call per turn - both ChatGroq and ChatMistralAI
            # accept parallel_tool_calls on bind_tools() and pass it straight
            # through to their (OpenAI-compatible) request body. Without this,
            # the model is free to emit 2+ tool calls in a single turn, which
            # LangGraph's ToolNode then runs concurrently via a
            # ThreadPoolExecutor (langgraph/prebuilt/tool_node.py's
            # `executor.map`) - harmless on its own, but any print() from
            # those worker threads has no Streamlit session context when run
            # through the UI, which was surfacing as every credential in the
            # chain failing (see streamlit_app.py's _LiveLogCapture). Binding
            # here (rather than passing the raw client) is honored by
            # create_react_agent's _should_bind_tools check, which skips
            # re-binding a model that's already a RunnableBinding with the
            # same tool count.
            try:
                bound_client = client.bind_tools(tools, parallel_tool_calls=False)
            except TypeError:
                # A provider client that doesn't accept this kwarg - fall
                # back to normal binding rather than fail the whole attempt
                # over a UI-only concern.
                bound_client = client
            react_agent = create_react_agent(bound_client, tools, prompt=system_prompt)
            result = react_agent.invoke(
                {"messages": [HumanMessage(content=user_message)]},
                config={"recursion_limit": MAX_TOOL_STEPS * 2 + 2},
            )
            final_message = result["messages"][-1]
            # Sum usage_metadata across every AIMessage in this exchange (a
            # tool-calling round is several model turns, not one) - best
            # effort, "unknown" if none of them carry it.
            usage_msgs = [m for m in result["messages"] if getattr(m, "usage_metadata", None)]
            input_tok = sum(m.usage_metadata.get("input_tokens", 0) for m in usage_msgs) if usage_msgs else None
            output_tok = sum(m.usage_metadata.get("output_tokens", 0) for m in usage_msgs) if usage_msgs else None
            total_tok = sum(m.usage_metadata.get("total_tokens", 0) for m in usage_msgs) if usage_msgs else None
            logger.llm_call_completed(call_id, skill_name, stage, operation, model_name, provider, account, attempt,
                                      input_tokens=input_tok, output_tokens=output_tok, total_tokens=total_tok)
            # .text (not .content) - same reasoning as ModelRouter.invoke():
            # Azure's Responses API (see _create_azure_openai_client) returns
            # a list of content-block dicts instead of a plain string, and
            # .text normalizes both shapes.
            return final_message.text or "", True

        except Exception as e:
            will_retry = attempt + 1 < chain_length
            # str(e) is blank for some exception types (e.g. certain SDK/network
            # errors with no message body), which otherwise silently discards the
            # only diagnostic info about why the attempt actually failed - fall
            # back to type name + repr so there's always something to go on.
            detail = str(e) or repr(e) or type(e).__name__
            from core.logger import _classify_error
            logger.model_attempt_failed(attempt, detail, will_retry, provider=provider, model=model_name or skill_name)
            logger.llm_call_failed(call_id, skill_name, stage, operation, model_name or skill_name, provider,
                                   account, attempt, error_type=_classify_error(detail), error_message=detail)
            if will_retry:
                # Add small delay before retry to avoid rapid rate limit exhaustion
                import time
                time.sleep(2)
                continue
            return f"Agent unavailable - all {chain_length} configured credential(s) exhausted: {detail[:200]}", False

    return "Agent unavailable - no attempts succeeded.", False

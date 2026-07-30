"""
Model Configuration - Single source of truth for all model routing.

Every credential (one account's key for one provider, or the local Ollama
install) is a single entry in CREDENTIAL_POOL. A skill just picks a
"primary" entry and a TaskTier; the router automatically builds a fallback
chain that walks through EVERY OTHER configured credential before giving
up, ordered by TIER_FALLBACK_PROVIDER_ORDER (e.g. reasoning-tier calls
prefer Groq before Mistral once local Ollama is exhausted; coding-tier
calls prefer Mistral before Groq). Nothing needs hand-maintained fallback
lists per skill.

Adding an account: add its two entries (groq + mistral) to CREDENTIAL_POOL.
Adding a new provider (e.g. OpenAI): see the "HOW TO ADD A NEW PROVIDER"
note at the bottom - it's 3 small, additive steps, no existing code changes.
"""

import os
from enum import Enum


class TaskTier(Enum):
    """Task priority tiers - drives which model tier + temperature a skill gets."""
    PLANNING = "planning"        # Planner's 4 calls + supervisor routing - plain text/JSON, NO
                                 # tool-calling, safe to route through Gateway
    REASONING = "reasoning"      # Testing/Deployment agents ONLY now - these DO use tool-calling
                                 # (run_tool_agent), so this tier must never include a
                                 # non-tool-calling-capable provider like "aigateway"
    CODING = "coding"            # Backend/frontend tool-calling agents (needs reliable tool calls)
    STRUCTURED = "structured"    # SQL, OpenAPI, docs, code review - templated/code-adjacent, no tools
    UTILITY = "utility"          # Fast classification/parsing


class Provider(Enum):
    GROQ = "groq"
    MISTRAL = "mistral"
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    AIGATEWAY = "aigateway"


# ============================================================================
# CREDENTIAL POOL - every (account, provider) pair this pipeline can use.
#
# The "local" Ollama entry has api_key_env=None - it needs no API key (it's
# a local server), so configured_credentials() treats it as always present.
# If the Ollama server isn't actually running, calls to it fail with a
# connection error like any other provider failure, and the normal
# exception-driven fallback moves on to the next credential in the chain -
# no separate health-check needed.
# ============================================================================

CREDENTIAL_POOL = [
    {
        "account": "gateway", "provider": "aigateway", "api_key_env": None,
        # Remote REST gateway (https://aigateway-api.siddu.online) exposing
        # someone's Ollama install over the internet - no API key, unauthenticated
        # per its own docs. It auto-selects the best of its 5 hosted models per
        # request (task-type based), so there's only one "model key" here -
        # "auto" - meaning "don't pass a model field, let the gateway pick."
        # ONLY safe for plain prompt/JSON completions (its /chat endpoint takes
        # a messages array and returns free text) - it has NO documented
        # tool/function-calling support, so it must NEVER be given to a skill
        # that goes through run_tool_agent() (backend_agent/frontend_agent/
        # testing_agent/deployment_agent) - see TIER_MODEL_KEY_BY_PROVIDER's
        # REASONING/CODING tiers, which deliberately exclude it.
        "models": {"auto": "Gateway (auto-selected)"},
    },
    {
        "account": "local", "provider": "ollama", "api_key_env": None,
        # Confirmed by direct, repeated test (bind_tools + .invoke, not just
        # reading docs):
        #  - "qwen" (qwen2.5:7b, plain/non-coder) reliably emits real,
        #    LangChain-recognizable tool_calls (3/3) AND is a strong general
        #    coder - used for every tool-calling agent (Backend/Frontend/
        #    Testing/Deployment) plus plain reasoning (Planner/Supervisor).
        #  - "coder" (qwen2.5-coder:7b) does NOT reliably do this (0/3 across
        #    two separate test rounds) - it outputs the right JSON as plain
        #    text instead of a structured tool call, so create_react_agent
        #    would see a "final answer" and stop without ever calling
        #    write_file. Only used where nothing calls tools (schema/OpenAPI/
        #    docs generation, code review) - where its coding specialization
        #    is a pure asset with no downside, and it's a genuinely different
        #    model from "qwen" for review_code's independent-second-opinion
        #    purpose.
        #  - "instruct" (llama3.1:8b-instruct) - reliable tool-calling too,
        #    used for simple utility classification.
        "models": {
            "qwen": "qwen2.5:7b",
            "coder": "qwen2.5-coder:7b",
            "instruct": "llama3.1:8b-instruct-q4_K_M",
        },
    },
    {
        "account": "account1", "provider": "groq", "api_key_env": "GROQ_API_KEY_A1",
        "models": {"versatile": "llama-3.3-70b-versatile", "instant": "llama-3.1-8b-instant"},
    },
    {
        "account": "account1", "provider": "mistral", "api_key_env": "MISTRAL_API_KEY_A1",
        "models": {"small": "mistral-small-latest", "large": "mistral-large-latest"},
    },
    {
        "account": "account2", "provider": "groq", "api_key_env": "GROQ_API_KEY_A2",
        "models": {"versatile": "llama-3.3-70b-versatile", "instant": "llama-3.1-8b-instant"},
    },
    {
        "account": "account2", "provider": "mistral", "api_key_env": "MISTRAL_API_KEY_A2",
        "models": {"small": "mistral-small-latest", "large": "mistral-large-latest"},
    },
    {
        "account": "account3", "provider": "groq", "api_key_env": "GROQ_API_KEY_A3",
        "models": {"versatile": "llama-3.3-70b-versatile", "instant": "llama-3.1-8b-instant"},
    },
    {
        "account": "account3", "provider": "mistral", "api_key_env": "MISTRAL_API_KEY_A3",
        "models": {"small": "mistral-small-latest", "large": "mistral-large-latest"},
    },
    {
        "account": "account4", "provider": "groq", "api_key_env": "GROQ_API_KEY_A4",
        "models": {"versatile": "llama-3.3-70b-versatile", "instant": "llama-3.1-8b-instant"},
    },
    {
        "account": "account4", "provider": "mistral", "api_key_env": "MISTRAL_API_KEY_A4",
        "models": {"small": "mistral-small-latest", "large": "mistral-large-latest"},
    },
]


def configured_credentials() -> list[dict]:
    """CREDENTIAL_POOL entries that are usable: no key required (local Ollama), or a real key is set."""
    return [c for c in CREDENTIAL_POOL if not c.get("api_key_env") or os.getenv(c["api_key_env"])]


# ============================================================================
# TIER -> MODEL KEY, PER PROVIDER
#
# Each provider names its models differently, so this is the one place that
# maps a task's tier onto the right model for whichever provider a fallback
# attempt lands on. A provider not listed here for a given tier is simply
# skipped when building that tier's fallback chain.
# ============================================================================

TIER_MODEL_KEY_BY_PROVIDER = {
    # PLANNING/STRUCTURED/UTILITY are all plain-text/JSON completions (no
    # tool-calling anywhere in their call path) - safe to route through the
    # Gateway, which is now their primary (see SKILL_MODEL_MAP below).
    TaskTier.PLANNING: {"aigateway": "auto", "ollama": "qwen", "groq": "versatile", "mistral": "large"},
    TaskTier.STRUCTURED: {"aigateway": "auto", "ollama": "coder", "groq": "versatile", "mistral": "small"},
    TaskTier.UTILITY: {"aigateway": "auto", "ollama": "instruct", "groq": "instant", "mistral": "small"},
    # REASONING (testing_agent/deployment_agent) and CODING (backend_agent/
    # frontend_agent) are real tool-calling agents (run_tool_agent) -
    # "aigateway" is deliberately absent from both: it has no tool-calling
    # support, so create_react_agent's bind_tools() would break on it.
    #
    # "ollama" is commented out (not deleted) for BOTH tiers - local Ollama
    # was too slow for these agents in practice; Mistral is now primary and
    # Groq the fallback (both genuine, proven native tool-calling via mature
    # LangChain integrations - no text-emulation fragility). Uncomment the
    # "ollama" line in each dict below to bring it back into the fallback
    # chain if wanted later - nothing else needs to change to restore it.
    TaskTier.REASONING: {
        # "ollama": "qwen",
        "groq": "versatile", "mistral": "large",
    },
    TaskTier.CODING: {
        # "ollama": "qwen",
        "groq": "versatile", "mistral": "large",
    },
}

# After the primary fails, which provider to prefer next, per tier. Now that
# the Gateway is primary for PLANNING/STRUCTURED/UTILITY, local Ollama is a
# genuine fallback for them too (tried first since it's free/unlimited),
# then cloud in the same relative order as before. REASONING/CODING both
# now run Mistral-primary -> Groq fallback (ollama commented out above).
TIER_FALLBACK_PROVIDER_ORDER = {
    TaskTier.PLANNING: ["ollama", "groq", "mistral"],
    TaskTier.STRUCTURED: ["ollama", "mistral", "groq"],
    TaskTier.UTILITY: ["ollama", "groq", "mistral"],
    TaskTier.REASONING: ["mistral", "groq"],
    TaskTier.CODING: ["mistral", "groq"],
}


def build_chain(primary_account: str, primary_provider: str, tier: TaskTier) -> list[tuple]:
    """
    Returns the full ordered list of (account, provider, model_key) to try
    for this tier: the given primary first, then every other CONFIGURED
    credential (real key present, or no key required) ordered by
    TIER_FALLBACK_PROVIDER_ORDER - all accounts of the preferred cloud
    provider before the other one. Credentials for providers with no model
    mapping for this tier are skipped entirely.
    """
    tier_providers = TIER_MODEL_KEY_BY_PROVIDER.get(tier, {})
    pool = [c for c in configured_credentials() if c["provider"] in tier_providers]

    if not pool:
        return []

    fallback_order = TIER_FALLBACK_PROVIDER_ORDER.get(tier, [])

    def sort_key(c):
        is_primary = c["account"] == primary_account and c["provider"] == primary_provider
        provider_rank = fallback_order.index(c["provider"]) if c["provider"] in fallback_order else len(fallback_order)
        # CREDENTIAL_POOL.index(c) is a stable tiebreaker preserving account1->account4
        # order within the same provider rank.
        return (0 if is_primary else 1, provider_rank, CREDENTIAL_POOL.index(c))

    ordered = sorted(pool, key=sort_key)
    return [(c["account"], c["provider"], tier_providers[c["provider"]]) for c in ordered]


# ============================================================================
# SKILL -> (TIER, PRIMARY) MAPPING
#
# Every skill's primary is the local Ollama credential - it's free and has
# no rate limit, so there's no reason not to try it first. The tier alone
# picks which local model it gets (instruct vs coder, see
# TIER_MODEL_KEY_BY_PROVIDER) and which cloud provider it falls back to
# first (see TIER_FALLBACK_PROVIDER_ORDER).
# ============================================================================

_OLLAMA = ("local", "ollama")
_AIGATEWAY = ("gateway", "aigateway")
_MISTRAL_PRIMARY = ("account1", "mistral")

SKILL_MODEL_MAP = {
    # ---- Planner (4 sequential calls per project) - plain text/JSON, no
    # tool-calling, so the Gateway (auto-selecting its own hosted model) is
    # primary now; Ollama/Groq/Mistral remain as fallback if it's unreachable ----
    "analyze_request": {"tier": TaskTier.PLANNING, "primary": _AIGATEWAY},
    "extract_requirements": {"tier": TaskTier.PLANNING, "primary": _AIGATEWAY},
    "create_task_list": {"tier": TaskTier.PLANNING, "primary": _AIGATEWAY},
    "choose_stack": {"tier": TaskTier.PLANNING, "primary": _AIGATEWAY},

    # ---- Database agent (schema + OpenAPI contract - structured/templated,
    # no tool-calling involved) - same reasoning, Gateway primary ----
    "generate_schema": {"tier": TaskTier.STRUCTURED, "primary": _AIGATEWAY},
    "generate_openapi": {"tier": TaskTier.STRUCTURED, "primary": _AIGATEWAY},

    # ---- Deployment agent's docs step (also no tools) ----
    "generate_docs": {"tier": TaskTier.STRUCTURED, "primary": _AIGATEWAY},

    # ---- Simple-file fast path (bypasses the full agent workflow) ----
    "classify_intent": {"tier": TaskTier.UTILITY, "primary": _AIGATEWAY},
    "generate_simple_content": {"tier": TaskTier.STRUCTURED, "primary": _AIGATEWAY},

    # ---- Supervisor (plain JSON routing decision, no tools) ----
    "supervisor": {"tier": TaskTier.PLANNING, "primary": _AIGATEWAY},

    # ---- Backend/Frontend: REAL tool-calling agents (write_file/read_file/
    # delete_file) - Gateway has NO tool-calling support (confirmed by
    # direct testing), so these never touch it. Local Ollama was too slow in
    # practice, so it's commented out of CODING's provider map above -
    # Mistral is primary now (genuine, proven native tool-calling), Groq the
    # fallback. To bring Ollama back, uncomment its line in
    # TIER_MODEL_KEY_BY_PROVIDER[TaskTier.CODING] and switch primary back to
    # _OLLAMA here - nothing else needs to change. ----
    "backend_agent": {"tier": TaskTier.CODING, "primary": _MISTRAL_PRIMARY},
    "frontend_agent": {"tier": TaskTier.CODING, "primary": _MISTRAL_PRIMARY},

    # ---- Testing/Deployment: also real tool-calling agents - same reason,
    # same Mistral-primary/Groq-fallback switch (Ollama commented out of
    # REASONING's provider map above, Gateway still excluded entirely) ----
    "testing_agent": {"tier": TaskTier.REASONING, "primary": _MISTRAL_PRIMARY},
    "deployment_agent": {"tier": TaskTier.REASONING, "primary": _MISTRAL_PRIMARY},

    # ---- Optional deeper LLM code review, available to the testing agent
    # as a tool alongside its static (ast/pyflakes) checks - no tool-calling
    # itself (plain text review), so Gateway is primary here too ----
    "review_code": {"tier": TaskTier.STRUCTURED, "primary": _AIGATEWAY},
}


# ============================================================================
# TEMPERATURE SETTINGS (by tier)
# ============================================================================

TEMPERATURE_BY_TIER = {
    TaskTier.PLANNING: 0.0,       # Deterministic for critical decisions
    TaskTier.REASONING: 0.0,      # Deterministic for critical decisions
    TaskTier.CODING: 0.1,         # Slight creativity for code generation
    TaskTier.STRUCTURED: 0.0,     # Strict formatting
    TaskTier.UTILITY: 0.0,        # Deterministic utilities
}


# ============================================================================
# EASY CONFIGURATION SHORTCUTS
# ============================================================================

def use_account_only(account: str):
    """
    Quick switch: force every skill's primary onto one cloud account
    (testing/debugging). Skills primarily on "gateway" or "local" (no
    per-account credential exists for those) are left untouched - there's
    nothing to switch them to under a cloud account.
    """
    for skill in SKILL_MODEL_MAP:
        current_account, provider = SKILL_MODEL_MAP[skill]["primary"]
        if current_account in ("gateway", "local"):
            continue
        SKILL_MODEL_MAP[skill]["primary"] = (account, provider)


def use_cloud_only():
    """Quick switch: skip the Gateway and local Ollama entirely, go straight to cloud (e.g. both are down)."""
    for skill, config in SKILL_MODEL_MAP.items():
        order = [p for p in TIER_FALLBACK_PROVIDER_ORDER.get(config["tier"], []) if p not in ("ollama", "aigateway")]
        if order:
            config["primary"] = ("account1", order[0])


# ============================================================================
# NOTES FOR FUTURE MODIFICATIONS
# ============================================================================

"""
HOW TO ADD A NEW ACCOUNT (e.g. a 5th Groq+Mistral account):
1. Add its two entries (groq + mistral) to CREDENTIAL_POOL, following the
   same shape as the existing ones.
2. Add the matching env vars (e.g. GROQ_API_KEY_A5, MISTRAL_API_KEY_A5) to
   .env - configured_credentials() picks it up automatically once the key
   is set; every skill's fallback chain now includes it with no other
   changes needed.

HOW TO ADD A NEW MODEL PROVIDER (e.g. OpenAI):
1. Add one CREDENTIAL_POOL entry per account that has a key for it, e.g.
   {"account": "account1", "provider": "openai", "api_key_env": "OPENAI_API_KEY_A1",
    "models": {"reasoning-model": "o3-mini", "fast-model": "gpt-4o-mini"}}
2. Add "openai": "<model key>" under each relevant TaskTier in
   TIER_MODEL_KEY_BY_PROVIDER, and add "openai" somewhere in each relevant
   TIER_FALLBACK_PROVIDER_ORDER list (position = how eagerly it's preferred
   over the other cloud provider once local/primary fails).
3. Register a client factory for it in core/model_router.py's
   PROVIDER_CLIENT_FACTORIES dict (needs the actual LangChain integration,
   e.g. langchain_openai.ChatOpenAI - the one piece that can't be pure
   config, since each provider's LangChain wrapper is a different import).
That's it - no SKILL_MODEL_MAP entry needs touching; every skill using a
tier that now includes the new provider will fall back to it automatically.

HOW TO CHANGE AN AGENT'S MODEL:
1. Find its entry in SKILL_MODEL_MAP (matches the skill_name passed to
   router.invoke()/run_tool_agent())
2. Change "tier" and/or "primary"
3. Done - the fallback chain is derived, not hand-typed.

IMPORTANT: if a skill goes through run_tool_agent() (i.e. it's a real
tool-calling agent, not a plain router.invoke() text/JSON completion),
its Ollama model MUST be one confirmed to emit real tool_calls - currently
"qwen" (qwen2.5:7b) and "instruct" (llama3.1) both do, "coder"
(qwen2.5-coder:7b) does NOT (verified by direct, repeated test - it
outputs the right JSON as plain text instead of a structured tool call).
Giving a tool-calling skill the "coder" model key would make it silently
stop after one text response, never calling a single tool. Before trusting
any NEW local model for a tool-calling skill, verify it the same way:
  llm = ChatOllama(model=name).bind_tools([some_tool])
  llm.invoke([...]).tool_calls   # must be non-empty, not just correct-looking text

Every skill_name here must have a real caller - if you remove a capability/
agent, remove its orphaned entry too (an unused entry here just adds
confusion, since nothing will ever invoke it).
"""

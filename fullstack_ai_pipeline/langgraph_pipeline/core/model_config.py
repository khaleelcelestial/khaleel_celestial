"""
Model Configuration - Single source of truth for all model routing.

Every credential (one account's key for one provider) is a single entry in
CREDENTIAL_POOL. A skill just picks a "primary" entry and a TaskTier; the
router automatically builds a fallback chain that walks through EVERY OTHER
configured credential before giving up, ordered by
TIER_FALLBACK_PROVIDER_ORDER. Nothing needs hand-maintained fallback lists
per skill.

Provider lineup, uniform across every tier/skill: Azure (gpt-5.4-mini)
primary, then all 4 Mistral accounts, then all 4 Groq accounts. No local
Ollama and no remote AI Gateway - both were dropped once the pipeline
became tool-calling-heavy across every agent (Backend/Frontend/Testing/
Deployment/incremental codegen): the Gateway never had real tool-calling
support at all (its /chat endpoint has no function-calling protocol, and
every tool-calling skill already force-disabled it via
_gateway_failure_streak before this change), and local Ollama's tool-calling
reliability was inconsistent model-to-model (confirmed by direct test - see
git history) and depends on a local server this environment doesn't
reliably run. Mistral and Groq both have mature, proven native tool-calling
via LangChain, so standardizing on Azure/Mistral/Groq for every tier is
simpler and more reliable than keeping either dormant path around.

Adding an account: add its two entries (groq + mistral) to CREDENTIAL_POOL.
Adding a new provider (e.g. OpenAI): see the "HOW TO ADD A NEW PROVIDER"
note at the bottom - it's 3 small, additive steps, no existing code changes.
"""

import os
from enum import Enum


class TaskTier(Enum):
    """Task priority tiers - drives which model tier + temperature a skill gets."""
    PLANNING = "planning"        # Planner's 4 calls + supervisor routing - plain text/JSON, no tools
    REASONING = "reasoning"      # Testing/Deployment agents - real tool-calling (run_tool_agent)
    CODING = "coding"            # Backend/frontend tool-calling agents (needs reliable tool calls)
    STRUCTURED = "structured"    # SQL, OpenAPI, docs, code review - templated/code-adjacent, no tools
    UTILITY = "utility"          # Fast classification/parsing


class Provider(Enum):
    GROQ = "groq"
    MISTRAL = "mistral"
    ANTHROPIC = "anthropic"
    AZURE_OPENAI = "azure_openai"


# ============================================================================
# CREDENTIAL POOL - every (account, provider) pair this pipeline can use.
# ============================================================================

CREDENTIAL_POOL = [
    {
        "account": "azure1", "provider": "azure_openai", "api_key_env": "AZURE_OPENAI_API_KEY",
        # Azure AI Foundry deployment of gpt-5.4-mini, served over the
        # OpenAI-v1-compatible /responses endpoint (see
        # _create_azure_openai_client in model_router.py). Primary for
        # EVERY skill/tier (see SKILL_MODEL_MAP below) - genuinely supports
        # tool-calling (through use_responses_api=True), so it's safe as
        # primary for REASONING/CODING too, not just plain-text tiers.
        "models": {"mini": "gpt-5.4-mini"},
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
    """CREDENTIAL_POOL entries that are usable: a real key is set for them."""
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
    # azure_openai (gpt-5.4-mini) is primary for EVERY tier, with the SAME
    # fallback lineup everywhere - all 4 Mistral accounts, then all 4 Groq
    # accounts (see TIER_FALLBACK_PROVIDER_ORDER) - uniform across every
    # tier/skill/agent per explicit instruction, not tuned per tier. Both
    # Mistral and Groq have proven native tool-calling support, so this same
    # lineup is safe for the real tool-calling tiers (REASONING/CODING) too,
    # not just the plain-text ones.
    TaskTier.PLANNING: {"azure_openai": "mini", "mistral": "large", "groq": "versatile"},
    TaskTier.STRUCTURED: {"azure_openai": "mini", "mistral": "small", "groq": "versatile"},
    TaskTier.UTILITY: {"azure_openai": "mini", "mistral": "small", "groq": "instant"},
    TaskTier.REASONING: {"azure_openai": "mini", "mistral": "large", "groq": "versatile"},
    TaskTier.CODING: {"azure_openai": "mini", "mistral": "large", "groq": "versatile"},
}

# Same order for every tier, per explicit instruction: Azure primary, then
# Mistral (all 4 accounts), then Groq (all 4 accounts).
_UNIFORM_FALLBACK_ORDER = ["azure_openai", "mistral", "groq"]
TIER_FALLBACK_PROVIDER_ORDER = {tier: _UNIFORM_FALLBACK_ORDER for tier in TaskTier}


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
# picks which model each provider gets (see TIER_MODEL_KEY_BY_PROVIDER) -
# every skill's primary is Azure, so the tier's only other job is choosing
# the right Mistral/Groq model name for the uniform fallback chain (see
# TIER_FALLBACK_PROVIDER_ORDER).
# ============================================================================

_AZURE_PRIMARY = ("azure1", "azure_openai")

SKILL_MODEL_MAP = {
    # ---- Planner (4 sequential calls per project) - plain text/JSON, no
    # tool-calling. Azure GPT-5.4-mini is primary for every skill; Mistral
    # (all 4 accounts) then Groq (all 4 accounts) are the fallback ----
    "analyze_request": {"tier": TaskTier.PLANNING, "primary": _AZURE_PRIMARY},
    "extract_requirements": {"tier": TaskTier.PLANNING, "primary": _AZURE_PRIMARY},
    "create_task_list": {"tier": TaskTier.PLANNING, "primary": _AZURE_PRIMARY},
    "choose_stack": {"tier": TaskTier.PLANNING, "primary": _AZURE_PRIMARY},
    "extract_acceptance_criteria": {"tier": TaskTier.PLANNING, "primary": _AZURE_PRIMARY},

    # ---- Database agent (schema + OpenAPI contract - structured/templated,
    # no tool-calling involved) - same reasoning, Azure primary ----
    "generate_schema": {"tier": TaskTier.STRUCTURED, "primary": _AZURE_PRIMARY},
    "generate_openapi": {"tier": TaskTier.STRUCTURED, "primary": _AZURE_PRIMARY},

    # ---- Deployment agent's docs step (also no tools) ----
    "generate_docs": {"tier": TaskTier.STRUCTURED, "primary": _AZURE_PRIMARY},

    # ---- Simple-file fast path (bypasses the full agent workflow) ----
    "classify_intent": {"tier": TaskTier.UTILITY, "primary": _AZURE_PRIMARY},
    "generate_simple_content": {"tier": TaskTier.STRUCTURED, "primary": _AZURE_PRIMARY},

    # ---- Task-checklist self-reporting (database/backend/frontend RUN
    # nodes call this after a real write) - cheap, plain JSON, no tools ----
    "mark_tasks_complete": {"tier": TaskTier.UTILITY, "primary": _AZURE_PRIMARY},

    # ---- Scope selection for incremental generation (skills/
    # incremental_codegen.py) - ONE cheap call, paths only, never file
    # content, made ONLY when free deterministic signals (Project Index +
    # literal path mentions) find nothing. Same tier/cost class as
    # classify_intent/mark_tasks_complete above. ----
    "select_generation_scope": {"tier": TaskTier.UTILITY, "primary": _AZURE_PRIMARY},

    # ---- Shared Acceptance Test Generator (skills/acceptance_test_generator.py)
    # - one skill, called by Database now and meant to be called by
    # Backend/Frontend/E2E/CI-CD later. Single-shot code generation, no
    # tool-calling, same tier as schema/docs generation. ----
    "generate_acceptance_tests": {"tier": TaskTier.STRUCTURED, "primary": _AZURE_PRIMARY},

    # ---- Supervisor (plain JSON routing decision, no tools) ----
    "supervisor": {"tier": TaskTier.PLANNING, "primary": _AZURE_PRIMARY},

    # ---- Backend/Frontend: single-shot generation calls (skills/
    # batch_codegen.py's run_batch_generation) - NOT tool-calling. Both
    # BATCH and INCREMENTAL strategies now go through this same one-call
    # path, differing only in how much existing-file context gets inlined
    # (see skills/incremental_codegen.py's select_generation_scope) - a
    # prior ReAct tool-calling implementation was removed after it was
    # confirmed to cause quadratic token growth (see that module's
    # docstring for the measured numbers). ----
    "backend_agent": {"tier": TaskTier.CODING, "primary": _AZURE_PRIMARY},
    "frontend_agent": {"tier": TaskTier.CODING, "primary": _AZURE_PRIMARY},

    # ---- Deployment: a REAL tool-calling agent (run_tool_agent) - this is
    # the one place in the pipeline where the next step genuinely depends
    # on unpredictable prior output (docker compose ps's real result), so
    # ReAct's shape is actually justified here. Same Azure-primary/Mistral/
    # Groq-fallback lineup. ("testing_agent" was removed here - E2E's
    # rewrite this session dropped tool-calling entirely in favor of direct
    # validator calls, so it had no real caller left; per this file's own
    # rule, an orphaned entry just adds confusion.) ----
    "deployment_agent": {"tier": TaskTier.REASONING, "primary": _AZURE_PRIMARY},

    # ---- Optional deeper LLM code review, available to the testing agent
    # as a tool alongside its static (ast/pyflakes) checks - no tool-calling
    # itself (plain text review), Azure primary here too ----
    "review_code": {"tier": TaskTier.STRUCTURED, "primary": _AZURE_PRIMARY},
}


# ============================================================================
# SKILL -> (PIPELINE STAGE, OPERATION) - for LLM call observability
# (core/logger.py's llm_call_completed/failed) ONLY. Lets per-agent/per-
# stage LLM call breakdowns (Planner calls, Backend calls, etc. - see the
# LLM USAGE OBSERVABILITY spec) use the real pipeline stage a skill belongs
# to, without threading an explicit stage argument through every one of
# these skills' own call sites (a much bigger refactor for no added
# accuracy - skill_name already uniquely identifies its stage).
# ============================================================================

SKILL_TO_STAGE = {
    "analyze_request": "planner", "extract_requirements": "planner", "create_task_list": "planner",
    "choose_stack": "planner", "extract_acceptance_criteria": "planner",
    "classify_intent": "planner", "generate_simple_content": "planner",
    "generate_schema": "database", "generate_openapi": "database",
    "generate_docs": "deployment",
    "mark_tasks_complete": "other",
    "select_generation_scope": "other",  # shared across backend/frontend - not attributable to one stage
    "generate_acceptance_tests": "acceptance_test_generator",
    "supervisor": "supervisor",
    "backend_agent": "backend", "frontend_agent": "frontend",
    "deployment_agent": "deployment",
    "review_code": "other",
}

SKILL_TO_OPERATION = {
    "analyze_request": "planning", "extract_requirements": "planning", "create_task_list": "planning",
    "choose_stack": "planning", "extract_acceptance_criteria": "planning",
    "generate_schema": "generation", "generate_openapi": "generation", "generate_docs": "generation",
    "generate_simple_content": "generation", "generate_acceptance_tests": "generation",
    "classify_intent": "utility", "mark_tasks_complete": "utility",
    "select_generation_scope": "utility",
    "supervisor": "routing",
    "backend_agent": "generation", "frontend_agent": "generation",
    "deployment_agent": "verification",
    "review_code": "review",
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
    """Quick switch: force every skill's primary onto one account (testing/debugging)."""
    for skill in SKILL_MODEL_MAP:
        _current_account, provider = SKILL_MODEL_MAP[skill]["primary"]
        SKILL_MODEL_MAP[skill]["primary"] = (account, provider)


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
   TIER_MODEL_KEY_BY_PROVIDER, and add "openai" to _UNIFORM_FALLBACK_ORDER
   at the position that reflects how eagerly it should be tried relative
   to Mistral/Groq once the Azure primary fails.
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
every provider in its fallback chain must have genuine native tool-calling
support - confirmed for Azure (use_responses_api=True), Mistral, and Groq
via their mature LangChain integrations. Before adding any new provider to
a tool-calling tier (REASONING/CODING), verify it the same way:
  llm = ChatWhatever(model=name).bind_tools([some_tool])
  llm.invoke([...]).tool_calls   # must be non-empty, not just correct-looking text

Every skill_name here must have a real caller - if you remove a capability/
agent, remove its orphaned entry too (an unused entry here just adds
confusion, since nothing will ever invoke it).
"""

"""
Model Router - Abstracts LLM provider details from skills.
Skills ask for models by skill name, router returns configured client.
"""

import os
from typing import Any, Tuple
from core.model_config import SKILL_MODEL_MAP, TEMPERATURE_BY_TIER, build_chain


def _create_groq_client(model_name: str, api_key: str, temperature: float):
    from langchain_groq import ChatGroq
    return ChatGroq(model=model_name, api_key=api_key, temperature=temperature, max_tokens=8000)


def _create_mistral_client(model_name: str, api_key: str, temperature: float):
    from langchain_mistralai import ChatMistralAI
    return ChatMistralAI(model=model_name, api_key=api_key, temperature=temperature, max_tokens=8000)


def _create_anthropic_client(model_name: str, api_key: str, temperature: float):
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=model_name, api_key=api_key, temperature=temperature, max_tokens=8000)


# Azure AI Foundry endpoint hosting this deployment - not a secret (only the
# API key is), so it's a constant here rather than another .env entry.
_AZURE_OPENAI_BASE_URL = "https://ganesham03-3557-resource.services.ai.azure.com/openai/v1"


def _create_azure_openai_client(model_name: str, api_key: str, temperature: float):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model_name,
        base_url=_AZURE_OPENAI_BASE_URL,
        api_key=api_key,
        temperature=temperature,
        # This deployment (gpt-5.4-mini) is served through Azure AI Foundry's
        # OpenAI-v1-compatible /responses endpoint, not the older
        # /chat/completions one - confirmed by the user's own working sample
        # (client.responses.create(...)). use_responses_api=True makes
        # ChatOpenAI route through /responses while still translating
        # LangChain's normal message/tool-calling interface transparently,
        # so bind_tools() (needed by backend_agent/frontend_agent/
        # deployment_agent) still works the same as any other provider here.
        use_responses_api=True,
    )


# Every provider is a LangChain chat model behind the same .invoke(messages)
# interface, so adding a new one is registering one factory here (see the
# "HOW TO ADD A NEW PROVIDER" note in model_config.py for the other steps -
# a CREDENTIAL_POOL entry and a TIER_MODEL_KEY_BY_PROVIDER mapping).
PROVIDER_CLIENT_FACTORIES = {
    "groq": _create_groq_client,
    "mistral": _create_mistral_client,
    "anthropic": _create_anthropic_client,
    "azure_openai": _create_azure_openai_client,
}


class ModelRouter:
    """
    Central router that maps skill names to LLM clients, with automatic
    fallback across every configured credential (all accounts, all
    providers) for that skill's tier - see model_config.build_chain().
    """

    def __init__(self):
        self._client_cache = {}       # (account, provider, model_name, temperature) -> client
        self._chain_cache = {}        # skill_name -> [(account, provider, model_key), ...]
        self._fallback_used = {}      # skill_name -> count of fallback attempts used

    def _chain(self, skill_name: str) -> list:
        if skill_name not in SKILL_MODEL_MAP:
            raise ValueError(f"Skill '{skill_name}' not configured in SKILL_MODEL_MAP")

        if skill_name not in self._chain_cache:
            config = SKILL_MODEL_MAP[skill_name]
            primary_account, primary_provider = config["primary"]
            chain = build_chain(primary_account, primary_provider, config["tier"])
            if not chain:
                raise ValueError(
                    f"No configured credentials available for skill '{skill_name}' "
                    f"(tier={config['tier'].value}) - check that at least one relevant "
                    f"API key is set in .env."
                )
            self._chain_cache[skill_name] = chain

        return self._chain_cache[skill_name]

    def chain_length(self, skill_name: str) -> int:
        """How many (account, provider) attempts are available for this skill."""
        return len(self._chain(skill_name))

    def chain_info(self, skill_name: str, attempt: int) -> Tuple[str, str, str]:
        """(account, provider, model_key) for a given attempt - public accessor
        so callers (agent_runtime.py) can log which credential is in use
        without reaching into the "private" chain cache directly."""
        return self._chain(skill_name)[attempt]

    def get_client(self, skill_name: str, attempt: int = 0) -> Tuple[Any, str, float]:
        """
        Get a LangChain chat model for a skill.

        Args:
            skill_name: The skill requesting a model (e.g., "analyze_request")
            attempt: 0 = primary, 1+ = the next configured credential in the
                     fallback chain (every other account/provider for this
                     skill's tier, in order)

        Returns:
            (client, model_name, temperature)

        Raises:
            ValueError: If skill not configured or the chain is exhausted
        """
        chain = self._chain(skill_name)
        if attempt >= len(chain):
            raise ValueError(
                f"All {len(chain)} configured credential(s) exhausted for skill '{skill_name}'."
            )

        if attempt > 0:
            self._fallback_used[skill_name] = self._fallback_used.get(skill_name, 0) + 1

        config = SKILL_MODEL_MAP[skill_name]
        temperature = TEMPERATURE_BY_TIER[config["tier"]]
        account, provider, model_key = chain[attempt]

        credential = _credential_for(account, provider)
        model_name = credential["models"][model_key]

        cache_key = (account, provider, model_name, temperature)
        if cache_key not in self._client_cache:
            self._client_cache[cache_key] = self._create_client(account, provider, model_name, temperature)

        return self._client_cache[cache_key], model_name, temperature

    def _create_client(self, account: str, provider: str, model_name: str, temperature: float) -> Any:
        credential = _credential_for(account, provider)
        api_key = None
        if credential.get("api_key_env"):
            api_key = os.getenv(credential["api_key_env"])
            if not api_key:
                raise ValueError(f"No API key found for {account}/{provider} ({credential['api_key_env']})")

        factory = PROVIDER_CLIENT_FACTORIES.get(provider)
        if factory is None:
            raise ValueError(f"Unknown provider: {provider} (no client factory registered)")

        return factory(model_name, api_key, temperature)

    def invoke(self, skill_name: str, messages: list, attempt: int = 0) -> str:
        """
        Invoke a model for a skill, automatically falling back through every
        other configured credential for this skill's tier on failure.
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        from core.logger import get_logger
        from core.model_config import SKILL_TO_STAGE, SKILL_TO_OPERATION
        logger = get_logger()

        stage = SKILL_TO_STAGE.get(skill_name, "other")
        operation = SKILL_TO_OPERATION.get(skill_name, "other")
        account, provider, _model_key = self.chain_info(skill_name, attempt)
        model_name = None
        call_id = logger.llm_call_start()
        try:
            client, model_name, _temperature = self.get_client(skill_name, attempt)
            logger.model_attempt(model_name, attempt, provider=provider, account=account, stage=stage)

            lc_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    lc_messages.append(SystemMessage(content=msg["content"]))
                else:
                    lc_messages.append(HumanMessage(content=msg["content"]))

            response = client.invoke(lc_messages)
            usage = getattr(response, "usage_metadata", None) or {}
            logger.llm_call_completed(call_id, skill_name, stage, operation, model_name, provider, account, attempt,
                                      input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                                      total_tokens=usage.get("total_tokens"))
            # .text (not .content) - every existing provider returns a plain
            # string in .content, but Azure's Responses API (see
            # _create_azure_openai_client) returns a list of content-block
            # dicts instead. .text normalizes both shapes to a plain string,
            # which every skill calling this (analyze_request_skill,
            # generate_schema_skill, etc.) requires - they all do
            # .strip()/regex/json.loads() on the return value directly.
            return response.text

        except Exception as e:
            chain_length = self.chain_length(skill_name)
            will_retry = attempt + 1 < chain_length
            detail = str(e)
            from core.logger import _classify_error
            logger.model_attempt_failed(attempt, detail, will_retry, provider=provider, model=model_name or skill_name)
            logger.llm_call_failed(call_id, skill_name, stage, operation, model_name or skill_name, provider,
                                   account, attempt, error_type=_classify_error(detail), error_message=detail)

            if will_retry:
                return self.invoke(skill_name, messages, attempt + 1)
            else:
                raise RuntimeError(
                    f"All {chain_length} configured credential(s) failed for skill '{skill_name}'. "
                    f"Last error: {e}"
                )

    def get_stats(self) -> dict:
        return {
            "cached_clients": len(self._client_cache),
            "fallback_usage": self._fallback_used.copy(),
        }


def _credential_for(account: str, provider: str) -> dict:
    from core.model_config import CREDENTIAL_POOL
    return next(c for c in CREDENTIAL_POOL if c["account"] == account and c["provider"] == provider)


# Global singleton router
_router = None


def get_router() -> ModelRouter:
    """Get the global model router instance."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router

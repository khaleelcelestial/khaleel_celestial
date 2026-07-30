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


_AIGATEWAY_BASE_URL = "https://aigateway-api.siddu.online"


class _AIGatewayResponse:
    """Minimal stand-in for a LangChain AIMessage - ModelRouter.invoke() only ever reads .content."""
    def __init__(self, content: str):
        self.content = content


class _AIGatewayClient:
    """
    Lightweight, non-LangChain client for the remote AI Gateway REST API
    (see CONNECT.md) - deliberately NOT a real LangChain BaseChatModel,
    because it doesn't need to be: every caller of this client goes through
    ModelRouter.invoke(), which only ever calls client.invoke(lc_messages)
    and reads response.content. It must NEVER be handed to run_tool_agent()
    (create_react_agent calls .bind_tools() on the model, which this class
    doesn't implement) - that's enforced by keeping "aigateway" out of the
    REASONING/CODING tiers in model_config.py, not by anything in this class.

    No model name is ever sent - the whole point of this gateway is that it
    auto-selects the best of its own hosted models per request; forcing a
    specific one would defeat that.
    """

    def __init__(self, temperature: float, timeout: float = 90.0):
        self.temperature = temperature
        self.timeout = timeout

    def invoke(self, lc_messages: list) -> "_AIGatewayResponse":
        import json
        import urllib.request
        import urllib.error

        payload_messages = []
        for m in lc_messages:
            role = "system" if m.__class__.__name__ == "SystemMessage" else (
                "assistant" if m.__class__.__name__ == "AIMessage" else "user"
            )
            payload_messages.append({"role": role, "content": m.content})

        body = json.dumps({
            "messages": payload_messages,
            "temperature": self.temperature,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{_AIGATEWAY_BASE_URL}/chat",
            data=body,
            headers={
                "Content-Type": "application/json",
                # Without a browser-like UA, whatever WAF/proxy sits in
                # front of this gateway 403s the request outright - curl
                # (and a normal browser) pass fine, urllib's default
                # "Python-urllib/3.x" UA gets blocked as a bot. Confirmed by
                # direct side-by-side test: identical payload, curl -> 200,
                # urllib default UA -> 403.
                "User-Agent": "Mozilla/5.0 (compatible; langgraph-pipeline/1.0)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"AI Gateway request failed: {e}") from e

        # /chat's "message" field is an object ({"role":...,"content":...}),
        # not a plain string - despite CONNECT.md's own Python example
        # printing r.json()["message"] directly as if it were one. /generate
        # (not used here) returns a plain string under "response" instead.
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else (message or data.get("response") or "")
        if not content:
            raise RuntimeError(f"AI Gateway returned no content: {data}")

        # Surface which model it actually picked - not known until the
        # response comes back, so this is a follow-up log line rather than
        # part of the "Using: ..." line printed before the call.
        from core.logger import get_logger
        routing = data.get("routing_decision") or {}
        picked = data.get("model_used") or routing.get("selected_model")
        if picked:
            get_logger().info(f"  Gateway auto-selected: {picked}")

        return _AIGatewayResponse(content)


def _create_aigateway_client(model_name: str, api_key: str, temperature: float):
    return _AIGatewayClient(temperature=temperature)


def _create_ollama_client(model_name: str, api_key: str, temperature: float):
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=model_name,
        temperature=temperature,
        # Big enough to hold a pre-injected schema.sql/openapi.yaml plus a
        # growing tool-call history without silently truncating context -
        # Ollama's own default (2048-4096) is too small for that once an
        # agent is several tool-call steps in.
        num_ctx=8192,
        # A generous ceiling, not a target - generation still stops at the
        # model's own end-of-response token; this just bounds worst-case
        # latency against a runaway/looping completion.
        num_predict=4096,
        # Keeps the model loaded in memory between calls instead of Ollama's
        # default ~5-minute idle unload - a cold load costs ~15-20s (measured
        # directly), which would otherwise be paid again on every single
        # call across a long supervisor loop.
        keep_alive="30m",
    )


# Every provider is a LangChain chat model behind the same .invoke(messages)
# interface, so adding a new one is registering one factory here (see the
# "HOW TO ADD A NEW PROVIDER" note in model_config.py for the other steps -
# a CREDENTIAL_POOL entry and a TIER_MODEL_KEY_BY_PROVIDER mapping).
PROVIDER_CLIENT_FACTORIES = {
    "groq": _create_groq_client,
    "mistral": _create_mistral_client,
    "anthropic": _create_anthropic_client,
    "ollama": _create_ollama_client,
    "aigateway": _create_aigateway_client,
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
        logger = get_logger()

        try:
            client, model_name, _temperature = self.get_client(skill_name, attempt)
            logger.model_attempt(model_name, attempt)

            lc_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    lc_messages.append(SystemMessage(content=msg["content"]))
                else:
                    lc_messages.append(HumanMessage(content=msg["content"]))

            response = client.invoke(lc_messages)
            return response.content

        except Exception as e:
            chain_length = self.chain_length(skill_name)
            will_retry = attempt + 1 < chain_length
            logger.model_attempt_failed(attempt, str(e), will_retry)

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

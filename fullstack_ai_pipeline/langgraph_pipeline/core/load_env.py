"""
Load environment variables from .env file
Call this at the start of your scripts to load API keys
"""

import os
import sys
from pathlib import Path

# The pipeline's console output is emoji-heavy (📄, 🤖, ✅, ...). On Windows,
# stdout/stderr default to the system codepage (often cp1252) unless the
# terminal or PYTHONIOENCODING happens to force UTF-8, which crashes on the
# first emoji print - reliably reproducible when Streamlit (or any other
# non-terminal launcher) spawns the process. Force UTF-8 as early as
# possible, regardless of what's importing this module.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_dotenv():
    """Load environment variables from .env file."""
    
    # This module lives in core/, but .env belongs at the project root
    # (langgraph_pipeline/) alongside main.py/requirements.txt/etc.
    env_file = Path(__file__).resolve().parent.parent / ".env"
    
    if not env_file.exists():
        print("⚠️  No .env file found!")
        print(f"   Expected location: {env_file}")
        print("")
        print("   To create one:")
        print("   1. Copy .env.template to .env")
        print("   2. Fill in your API keys")
        print("   3. Run this script again")
        return False
    
    print(f"📄 Loading environment variables from {env_file}")
    
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            
            # Parse KEY=VALUE
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                
                # Only set if not already in environment and value is not empty
                if value and key not in os.environ:
                    os.environ[key] = value
    
    # Report what was loaded - every credential the model router can actually
    # use (see core/model_config.py CREDENTIAL_POOL), not a fixed A1/A2 pair,
    # so adding a 3rd/4th account here just works without touching this file.
    # The local Ollama entry has no api_key_env (needs no key) - skip it here.
    from core.model_config import CREDENTIAL_POOL
    keys_to_check = [c["api_key_env"] for c in CREDENTIAL_POOL if c.get("api_key_env")] + ["ANTHROPIC_API_KEY"]
    
    loaded = []
    missing = []
    
    for key in keys_to_check:
        if os.getenv(key):
            loaded.append(key)
        else:
            missing.append(key)
    
    print("")
    print("✅ Loaded API keys:")
    for key in loaded:
        # Mask the key value for security
        value = os.getenv(key)
        if value:
            masked = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
            print(f"   {key} = {masked}")
    
    if missing:
        print("")
        print("ℹ️  Missing API keys (optional):")
        for key in missing:
            print(f"   {key}")
    
    print("")
    
    has_local_ollama = any(c["provider"] == "ollama" for c in CREDENTIAL_POOL)
    if has_local_ollama:
        print("✅ Local Ollama configured as primary (no API key needed - assumes the server is running)")

    # Check minimum requirements - how many CLOUD accounts have BOTH a Groq
    # and a Mistral key set (a "fully configured" account). Local Ollama
    # needs no key, so it's reported separately above, not counted here.
    cloud_credentials = [c for c in CREDENTIAL_POOL if c.get("api_key_env")]
    configured_by_account = {}
    for c in cloud_credentials:
        if os.getenv(c["api_key_env"]):
            configured_by_account.setdefault(c["account"], set()).add(c["provider"])
    full_accounts = [a for a, providers in configured_by_account.items()
                     if {"groq", "mistral"} <= providers]
    total_accounts = len({c["account"] for c in cloud_credentials})
    has_any_groq = any("groq" in providers for providers in configured_by_account.values())
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))

    if len(full_accounts) == total_accounts:
        print(f"✅ FULL SETUP: All {total_accounts} cloud accounts configured (as fallback)!")
    elif full_accounts:
        print(f"✅ PARTIAL SETUP: {len(full_accounts)}/{total_accounts} cloud account(s) fully configured "
              f"(Groq + Mistral) - fallback chains just have fewer hops to walk through.")
    elif has_any_groq:
        print("✅ GROQ SETUP: Can use Groq models")
        print("   (Consider adding Mistral keys for full functionality)")
    elif has_anthropic:
        print("✅ ANTHROPIC SETUP: Can use Claude as fallback")
        print("   (add an 'anthropic' CREDENTIAL_POOL entry in core/model_config.py to route to it)")
    elif not has_local_ollama:
        print("❌ NO SETUP: No API keys found!")
        print("   Set at least GROQ_API_KEY or ANTHROPIC_API_KEY")
        return False
    
    return True


if __name__ == "__main__":
    load_dotenv()

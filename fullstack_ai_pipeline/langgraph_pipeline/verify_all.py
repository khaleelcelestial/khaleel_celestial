"""
Complete system verification - Check all connections
"""

# Load environment variables first
from core.load_env import load_dotenv
load_dotenv()

print("=" * 80)
print("SYSTEM VERIFICATION")
print("=" * 80)

# 1. Check imports
print("\n1️⃣  Checking imports...")
try:
    from core.state import ProjectState, BuildStatus
    print("   ✅ State")

    from core.graph import app
    print("   ✅ Graph")

    from core.model_router import get_router
    print("   ✅ Model Router")

    from core.logger import get_logger
    print("   ✅ Logger")

    from capabilities.planner import PlannerCapability
    from capabilities.database import DatabaseCapability
    from capabilities.supervisor import SupervisorCapability
    from capabilities.backend import BackendCapability
    from capabilities.frontend import FrontendCapability
    from capabilities.e2e import E2ECapability
    from capabilities.cicd import CICDCapability
    print("   ✅ All 7 supervisor-workflow agents (separate importable modules)")

    from skills.agent_tools import make_agent_tools
    from core.agent_runtime import run_tool_agent
    print("   ✅ Agent tools + tool-calling runtime")

    from skills.docker_skills import get_postgres_config
    from skills.project_registry import list_projects, allocate_ports, db_name_for, sync_workspace_from_disk
    from skills.simple_task_skills import classify_intent_skill
    print("   ✅ Docker/project-registry/simple-task skills")

except Exception as e:
    print(f"   ❌ Import failed: {e}")
    exit(1)

# 2. Check LangChain provider packages (Groq/Mistral/Anthropic all go
#    through LangChain chat models now, not raw provider SDKs)
print("\n2️⃣  Checking LangChain provider packages...")
provider_packages = [
    ("langchain_groq", "langchain-groq (ChatGroq)"),
    ("langchain_mistralai", "langchain-mistralai (ChatMistralAI)"),
    ("langchain_anthropic", "langchain-anthropic (ChatAnthropic)"),
]
for module_name, label in provider_packages:
    try:
        __import__(module_name)
        print(f"   ✅ {label}")
    except ImportError:
        print(f"   ❌ {label} - NOT INSTALLED (pip install -r requirements.txt)")

# 3. Check graph structure
print("\n3️⃣  Checking graph structure...")
try:
    nodes = app.get_graph().nodes
    print(f"   ✅ Graph has {len(nodes)} nodes")

    expected_nodes = [
        "planner", "supervisor",
        "database_run", "database_ut", "database_val",
        "backend_run", "backend_ut", "backend_val",
        "frontend_run", "frontend_ut", "frontend_val",
        "e2e_run", "e2e_ut", "e2e_val",
        "cicd_run", "cicd_ut", "cicd_val",
    ]

    for node in expected_nodes:
        if node in nodes.keys():
            print(f"      ✓ {node}")
        else:
            print(f"      ✗ {node} MISSING!")

except Exception as e:
    print(f"   ❌ Graph check failed: {e}")
    exit(1)

# 4. Check routing functions
print("\n4️⃣  Checking routing functions...")
try:
    from core.graph import route_entry, route_after_supervisor
    print("   ✅ All routing functions defined")
except Exception as e:
    print(f"   ❌ Routing check failed: {e}")
    exit(1)

# 5. Check model router config
print("\n5️⃣  Checking model router configuration...")
try:
    from core.model_config import SKILL_MODEL_MAP, CREDENTIAL_POOL
    accounts_configured = sorted({c["account"] for c in CREDENTIAL_POOL})
    print(f"   ✅ {len(SKILL_MODEL_MAP)} skills configured")
    print(f"   ✅ {len(accounts_configured)} account slot(s) defined ({len(CREDENTIAL_POOL)} credentials total)")
except Exception as e:
    print(f"   ❌ Config check failed: {e}")
    exit(1)

# 6. Check API keys - every (account, provider) credential the pipeline can
# actually fall back through, not just a fixed A1/A2 pair. Local Ollama
# needs no key (api_key_env is None) - reported separately, not counted
# among the keyed cloud credentials.
print("\n6️⃣  Checking LLM API keys...")
import os
from core.model_config import configured_credentials

ollama_entries = [c for c in CREDENTIAL_POOL if not c.get("api_key_env")]
cloud_credentials = [c for c in CREDENTIAL_POOL if c.get("api_key_env")]

if ollama_entries:
    print(f"   ✅ Local Ollama configured as primary (no key needed - assumes the server is running)")

keys_found = [c["api_key_env"] for c in cloud_credentials if os.getenv(c["api_key_env"])]
keys_missing = [c["api_key_env"] for c in cloud_credentials if not os.getenv(c["api_key_env"])]

if keys_found:
    print(f"   ✅ Found {len(keys_found)}/{len(cloud_credentials)} cloud credential(s)")
    for key in keys_found:
        print(f"      ✓ {key}")

if keys_missing:
    print(f"   ℹ️  Not configured ({len(keys_missing)}) - fine if intentional, fallback chains just skip these:")
    for key in keys_missing:
        print(f"      ✗ {key}")

configured_by_account = {}
for c in cloud_credentials:
    if os.getenv(c["api_key_env"]):
        configured_by_account.setdefault(c["account"], []).append(c["provider"])
full_accounts = [a for a, providers in configured_by_account.items() if len(providers) == 2]
cloud_accounts = len({c["account"] for c in cloud_credentials})
print(f"   ℹ️  {len(full_accounts)}/{cloud_accounts} cloud account(s) fully configured (both Groq + Mistral)")

# 7. Check Postgres credentials (used by every generated project's docker-compose)
print("\n7️⃣  Checking Postgres credentials (.env)...")
pg = get_postgres_config()
if pg["user"] and pg["password"]:
    print(f"   ✅ POSTGRES_USER / POSTGRES_PASSWORD set (user={pg['user']})")
else:
    print("   ⚠️  POSTGRES_USER/POSTGRES_PASSWORD not set - defaults will be used, "
          "but set them in .env for a real deployment")

# 8. Check Docker availability (optional - generated projects still package
#    fine without it, just won't auto-start)
print("\n8️⃣  Checking Docker...")
import shutil
if shutil.which("docker"):
    print("   ✅ docker CLI found on PATH")
else:
    print("   ℹ️  docker CLI not found - projects will still be generated and "
          "dockerized, just won't auto-start. Install Docker Desktop to enable that.")

# Final verdict
print("\n" + "=" * 80)
if ollama_entries or full_accounts:
    print("✅ SYSTEM READY!")
    print("=" * 80)
    if not ollama_entries and keys_missing:
        print(f"\n({len(keys_missing)}/{len(cloud_credentials)} possible cloud credentials not configured - "
              f"fine, fallback chains just have fewer hops to walk through.)")
    print("\nYou can now run:")
    print('  python main.py "Build a simple todo list website"')
    print("  python main.py                              (interactive mode)")
    print("  streamlit run streamlit_app.py               (web UI)")
    print()
elif keys_found:
    print("⚠️  SYSTEM PARTIALLY READY")
    print("=" * 80)
    print("\nYou have some API keys, but no single account has BOTH a Groq and a")
    print("Mistral key set - some tiers/providers won't have a working credential.")
    print("\nYou can still run:")
    print('  python main.py "Build a simple todo list website"')
    print()
else:
    print("❌ SYSTEM NOT READY")
    print("=" * 80)
    print("\nNo local Ollama and no API keys found. Set at least GROQ_API_KEY_A1 + "
          "MISTRAL_API_KEY_A1 in .env, or install/run Ollama.")
    print()

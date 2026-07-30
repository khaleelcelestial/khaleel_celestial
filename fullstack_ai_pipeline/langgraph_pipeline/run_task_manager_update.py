import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from core.load_env import load_dotenv
load_dotenv()

from main import run_pipeline

with open("task_manager_request.txt", "r", encoding="utf-8") as f:
    user_request = f.read().strip()

project_id = "build_a_full_stack_task_management_application_usi"

run_pipeline(user_request, project_id=project_id, update=True)

import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from core.load_env import load_dotenv
load_dotenv()

from main import dispatch_new_request

with open("task_manager_request.txt", "r", encoding="utf-8") as f:
    user_request = f.read().strip()

dispatch_new_request(user_request)

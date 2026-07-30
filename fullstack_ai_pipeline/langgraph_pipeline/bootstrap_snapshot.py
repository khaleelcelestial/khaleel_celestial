import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from skills.project_registry import project_dir_for, sync_workspace_from_disk, save_project_snapshot

project_id = "build_a_full_stack_task_management_application_usi"
project_dir = project_dir_for(project_id)

with open("task_manager_request.txt", "r", encoding="utf-8") as f:
    user_request = f.read().strip()

tasks = [
    "Create React + Vite frontend project with environment variables for API URL",
    "Design modern UI components for task creation, viewing, updating, and deletion",
    "Implement clear visual distinction between priority levels (low/medium/high) and statuses (pending/in-progress/done)",
    "Add error handling to display clear error messages on failed actions (e.g. task creation fails)",
    "Set up PostgreSQL database with Docker container for backend API",
    "Create FastAPI backend API with RESTful endpoints for CRUD operations on tasks",
    "Implement proper input validation for task fields (e.g. reject empty title)",
    "Enable CORS in FastAPI backend to allow frontend calls from any origin",
    "Package entire application with Dockerfile and docker-compose.yml for easy deployment",
    "Create environment variables for API URL, database connection string, and other sensitive data",
    "Implement filtering by status (pending/in-progress/done) on task list page",
    "Update existing task fields, including marking it complete, using PUT request to backend API",
    "Delete a task from the system using DELETE request to backend API",
]

execution_plan = {"database": True, "contract": True, "backend": True, "frontend": True, "release": True}

# database and frontend passed their VAL checks in the last observed run;
# backend was mid-RUN when the process was killed - "pending" forces a
# fresh, full attempt rather than guessing it "failed".
stage_status = {"database": "done", "backend": "pending", "frontend": "done",
                 "testing": "pending", "deployment": "pending"}

workspace = sync_workspace_from_disk(project_dir, {})

save_project_snapshot(
    project_dir=project_dir,
    project_id=project_id,
    user_request=user_request,
    execution_plan=execution_plan,
    requirements="",
    architecture="",
    tasks=tasks,
    workspace=workspace,
    stage_status=stage_status,
)

print(f"Snapshot bootstrapped for {project_id}")
print(f"stage_status: {stage_status}")

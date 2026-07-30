"""
Docker skills - Containerize the generated project (Dockerfiles, docker-
compose.yml, and the per-project .env). Actually bringing the stack up now
lives in capabilities/deployment.py (a real tool-calling agent that runs
`docker compose up`/`ps`/`logs` itself and diagnoses failures) - this module
only handles the deterministic generation step, reused by both the full
agent workflow and the simple-file packaging path.
"""

import json
import os
from pathlib import Path


def get_postgres_config() -> dict:
    """
    Postgres LOGIN credentials for generated projects, sourced from the
    pipeline's own .env (loaded into os.environ at startup) - reused as-is
    across every project, same as a master username/password used to create
    a new, separate lock (database) each time. The database NAME and the
    host PORT are NOT reused as-is - see db_name_for()/allocate_ports() in
    project_registry.py, which give each project its own unique values so
    multiple projects don't collide or share data.
    """
    return {
        "user": os.environ.get("POSTGRES_USER", "postgres"),
        "password": os.environ.get("POSTGRES_PASSWORD", "postgres"),
    }


def detect_backend_stack(files: dict[str, str]) -> str:
    """Inspect generated backend files to guess the runtime stack."""
    if "requirements.txt" in files or any(f.endswith(".py") for f in files):
        return "python"
    if "package.json" in files:
        return "node"
    return "unknown"


def detect_frontend_stack(files: dict[str, str]) -> str:
    """Inspect generated frontend files to guess how to serve them."""
    if "package.json" in files:
        return "node"
    return "static"


def _backend_run_command(files: dict[str, str], port: int = 8000) -> tuple[str, int]:
    """Pick a start command + port for the backend based on its dependencies."""
    requirements = files.get("requirements.txt", "").lower()
    if "fastapi" in requirements:
        return f"uvicorn main:app --host 0.0.0.0 --port {port}", port
    if "flask" in requirements:
        return f"flask --app main run --host=0.0.0.0 --port={port}", port
    if "django" in requirements:
        return f"python manage.py runserver 0.0.0.0:{port}", port
    return "python main.py", port


def _node_script(files: dict[str, str], preferred: list[str]) -> str:
    """Find the first matching npm script name in package.json, else fall back."""
    try:
        pkg = json.loads(files.get("package.json", "{}"))
        scripts = pkg.get("scripts", {})
        for name in preferred:
            if name in scripts:
                return name
    except (json.JSONDecodeError, TypeError):
        pass
    return preferred[0]


# A container that starts but never actually works (hung, crash-looping,
# every request 500ing) is otherwise indistinguishable from a healthy one to
# `docker compose up`'s own exit code - it only reports whether the START
# succeeded, not whether the process inside is actually serving traffic. A
# real HEALTHCHECK is what lets `docker compose ps`/`docker inspect` (and
# Docker Desktop's own UI) report "healthy"/"unhealthy" for real, and is what
# lets `depends_on: condition: service_healthy` mean something for frontend
# waiting on backend, the same way it already does for backend waiting on db.
# python:3.11-slim (Debian-based, no busybox/wget by default) always has its
# own interpreter, so urllib is the one dependency-free way to probe an HTTP
# port without installing curl/wget just for this. node:20-alpine and
# nginx:alpine are both Alpine-based and both ship BusyBox wget by default.
#
# Both of the first versions of these checks had the SAME real bug, confirmed
# live: a container that's genuinely alive and correctly responding with a
# perfectly ordinary 404 (e.g. a FastAPI backend with no route registered at
# bare "/", which is completely normal - nothing requires one) was reported
# "unhealthy" anyway. urllib.request.urlopen() raises HTTPError for ANY
# non-2xx/3xx status instead of just returning it, and an uncaught exception
# in the CMD script is exit-code-1 same as a real failure; wget --spider
# treats a non-2xx response as "page doesn't exist" for the exact same
# reason. A Docker HEALTHCHECK's job is "is this process alive and accepting
# connections", not "does every route return 2xx" - that finer-grained,
# correct-treats-404-as-fine check already exists separately in Python at
# skills/e2e_skills.py's http_reachable() (used by E2E/CICD_VAL), so the
# in-container check doesn't need that nuance - a bare TCP connect is
# simpler AND can never be confused by a non-5xx response, so that's what
# the Python check does now. wget has no equivalent bare-TCP mode, so its
# fix instead explicitly tolerates exit code 8 (BusyBox wget's own code for
# "got a real HTTP response, it just wasn't 2xx/3xx" - i.e. still alive).
#
# Both use 127.0.0.1, NEVER "localhost" - real, confirmed bug: inside these
# minimal images, "localhost" resolves to "::1" (IPv6) before "127.0.0.1"
# (confirmed via `getent hosts localhost`), and the dev server only listens
# on IPv4 "0.0.0.0". Python's socket.create_connection() silently tries every
# address getaddrinfo returns and falls back to the next on failure, so it
# never showed this bug - but BusyBox wget only tries the first resolved
# address and fails outright ("Connection refused") with no fallback, even
# though `netstat` inside the very same container showed node genuinely
# listening on 0.0.0.0:{port} the whole time. Using the literal IPv4 address
# sidesteps the DNS resolution order entirely for both checks.
_PYTHON_HEALTHCHECK = ('HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=5 '
                       'CMD python -c "import socket,sys; '
                       's=socket.create_connection((\'127.0.0.1\',{port}),timeout=3); s.close()" '
                       '|| exit 1')
_WGET_HEALTHCHECK = ('HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=5 '
                     'CMD sh -c \'wget -q -O /dev/null http://127.0.0.1:{port}/; '
                     'code=$?; [ "$code" -eq 0 ] || [ "$code" -eq 8 ] || exit 1\'')


def generate_backend_dockerfile(files: dict[str, str]) -> tuple[str, int]:
    """Returns (dockerfile_content, container_port)."""
    stack = detect_backend_stack(files)

    if stack == "python":
        command, port = _backend_run_command(files)
        dockerfile = f"""FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE {port}
{_PYTHON_HEALTHCHECK.format(port=port)}
CMD [{", ".join(f'"{part}"' for part in command.split(" "))}]
"""
        return dockerfile, port

    if stack == "node":
        script = _node_script(files, ["start", "serve"])
        port = 3000
        dockerfile = f"""FROM node:20-alpine
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
EXPOSE {port}
{_WGET_HEALTHCHECK.format(port=port)}
CMD ["npm", "run", "{script}"]
"""
        return dockerfile, port

    # Unknown stack - still produce something runnable-ish rather than failing packaging
    dockerfile = f"""FROM python:3.11-slim
WORKDIR /app
COPY . .
{_PYTHON_HEALTHCHECK.format(port=8000)}
CMD ["python", "main.py"]
"""
    return dockerfile, 8000


def generate_frontend_dockerfile(files: dict[str, str]) -> tuple[str, int]:
    """Returns (dockerfile_content, container_port)."""
    stack = detect_frontend_stack(files)

    if stack == "node":
        script = _node_script(files, ["dev", "start"])
        port = 5173
        # --port is explicit here, not just --host, because the frontend
        # agent's own vite.config.js is free to set server.port to whatever
        # it wants (nothing currently checks/constrains that) - confirmed by
        # direct testing: a generated vite.config.js set port 3000 while
        # this Dockerfile only EXPOSEd/docker-compose only forwarded 5173,
        # so Vite listened on 3000 inside the container while Docker forwarded
        # host->5173 (nothing listening there) - the connection was accepted
        # at the TCP level then immediately dropped ("remote end closed
        # connection", not a timeout or refused-connection). A CLI --port flag
        # always overrides vite.config.js's server.port, so this guarantees
        # Vite actually listens on the exact port this Dockerfile/compose
        # assumes, regardless of whatever port number ends up in the config file.
        dockerfile = f"""FROM node:20-alpine
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
EXPOSE {port}
{_WGET_HEALTHCHECK.format(port=port)}
CMD ["npm", "run", "{script}", "--", "--host", "0.0.0.0", "--port", "{port}"]
"""
        return dockerfile, port

    # Static HTML/CSS/JS - serve with nginx
    dockerfile = f"""FROM nginx:alpine
COPY . /usr/share/nginx/html
EXPOSE 80
{_WGET_HEALTHCHECK.format(port=80)}
"""
    return dockerfile, 80


def generate_docker_compose(has_backend: bool, has_frontend: bool, has_database: bool,
                            backend_container_port: int, backend_host_port: int,
                            frontend_container_port: int, frontend_host_port: int,
                            db_host_port: int) -> str:
    """
    Credentials/db name are referenced as ${VAR} - docker compose auto-loads
    the .env file written alongside this compose file (see write_docker_assets).

    Host ports are baked in directly (one per project, from allocate_ports())
    so multiple projects' containers can run at the same time without
    clashing; the CONTAINER-internal ports stay fixed (each project has its
    own isolated network namespace, so those never collide).
    """
    services = []

    if has_database:
        # db_data is a NAMED volume, not the anonymous one Postgres's image
        # would otherwise create implicitly - real, observed gap: without an
        # explicit name, the data directory's persistence across an update's
        # container recreation depended on undocumented anonymous-volume
        # reuse behavior rather than being guaranteed by the compose file
        # itself. schema.sql is only ever a docker-entrypoint-initdb.d
        # script - Postgres runs it ONCE, only against a truly empty data
        # directory - so an update's real requirement ("do NOT recreate the
        # database", "preserve data") depends entirely on this volume
        # surviving `docker compose up --build -d` across updates. Naming it
        # makes that an explicit guarantee, not an accident of default
        # behavior, and makes the volume visible/manageable in Docker
        # Desktop by name instead of an opaque hash.
        services.append(f"""  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${{POSTGRES_USER}}
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD}}
      POSTGRES_DB: ${{POSTGRES_DB}}
    ports:
      - "{db_host_port}:5432"
    volumes:
      - db_data:/var/lib/postgresql/data
      - ./schema.sql:/docker-entrypoint-initdb.d/schema.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${{POSTGRES_USER}}"]
      interval: 5s
      timeout: 5s
      retries: 5""")

    if has_backend:
        depends_on = "\n    depends_on:\n      db:\n        condition: service_healthy" if has_database else ""
        db_env = "\n      DATABASE_URL: ${DATABASE_URL}" if has_database else ""
        services.append(f"""  backend:
    build: ./backend
    ports:
      - "{backend_host_port}:{backend_container_port}"
    environment:
      PORT: "{backend_container_port}"{db_env}{depends_on}
    restart: on-failure""")

    if has_frontend:
        # condition: service_healthy, not a bare list-style depends_on - now
        # that the backend Dockerfile defines a real HEALTHCHECK, "started"
        # and "actually serving traffic" are different things worth telling
        # apart; a bare depends_on only waits for the container to START.
        depends_on = "\n    depends_on:\n      backend:\n        condition: service_healthy" if has_backend else ""
        # The browser (not the frontend container) is what actually calls this
        # URL, so it must be the HOST-mapped backend port, not the container-
        # internal one or the "backend" service DNS name (neither is reachable
        # from the browser running on the host machine).
        api_env = (f"\n    environment:\n      VITE_API_BASE_URL: http://localhost:{backend_host_port}"
                  if has_backend else "")
        services.append(f"""  frontend:
    build: ./frontend
    ports:
      - "{frontend_host_port}:{frontend_container_port}"{api_env}{depends_on}
    restart: on-failure""")

    volumes_section = "\nvolumes:\n  db_data:\n" if has_database else ""
    return "services:\n" + "\n".join(services) + "\n" + volumes_section


def _parse_compose_ps_json(raw: str) -> list[dict]:
    """
    `docker compose ps --format json` output shape varies by Compose
    version - some versions emit one JSON array, others emit NDJSON (one
    object per line). Tolerant of both rather than assuming one.
    """
    raw = raw.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        pass
    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def check_containers_healthy(project_dir: Path, expected_services: list[str],
                             timeout_s: int = 60, poll_interval_s: float = 2.0) -> dict:
    """
    Real, observed gap this closes: `docker compose up --build -d` returning
    exit code 0 only means the containers STARTED - it says nothing about
    whether the process inside is actually working (hung, crash-looping,
    every request 500ing), and CICD_VAL previously only checked backend/
    frontend reachability with a single instant HTTP probe and never checked
    the database's own container state at all. A container can pass one
    lucky HTTP probe mid-crash-loop and still not be a real, stable
    deployment.

    Polls `docker compose ps --format json` every poll_interval_s, up to
    timeout_s total, waiting for EVERY service in expected_services to reach
    State == "running" AND (Health == "healthy" OR that service has no
    healthcheck defined at all, i.e. Health is empty/absent - not every
    Compose version reports a Health field for services with no
    HEALTHCHECK, so its absence is not itself a failure). A service that's
    still "starting" keeps polling instead of failing immediately - matches
    each Dockerfile's own HEALTHCHECK start_period, so the check doesn't
    fail on a service that's genuinely just still warming up.

    Returns {"passed": bool, "services": {name: {"state":.., "health":.., "ok": bool}},
    "missing": [...], "detail": str}.
    """
    import subprocess
    import time as _time

    deadline = _time.time() + timeout_s
    last_seen = {}

    while True:
        try:
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                cwd=str(project_dir), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15,
            )
            entries = _parse_compose_ps_json(result.stdout) if result.returncode == 0 else []
        except (subprocess.TimeoutExpired, OSError):
            entries = []

        by_service = {e.get("Service"): e for e in entries if e.get("Service")}
        last_seen = by_service

        services = {}
        all_ok = True
        missing = []
        for name in expected_services:
            entry = by_service.get(name)
            if not entry:
                missing.append(name)
                all_ok = False
                continue
            state = (entry.get("State") or "").lower()
            health = (entry.get("Health") or "").lower()
            ok = state == "running" and health in ("", "healthy")
            services[name] = {"state": state or "unknown", "health": health or "none", "ok": ok}
            if not ok:
                all_ok = False

        if all_ok or _time.time() >= deadline:
            detail_parts = [f"{name}: state={info['state']}, health={info['health']}"
                            for name, info in services.items()]
            if missing:
                detail_parts.append(f"missing from `docker compose ps`: {missing}")
            return {
                "passed": all_ok,
                "services": services,
                "missing": missing,
                "detail": "; ".join(detail_parts) or "no service data available",
            }

        _time.sleep(poll_interval_s)


def apply_schema_to_running_db(project_dir: Path) -> tuple[bool, str]:
    """
    Real gap this closes: schema.sql is mounted as docker-entrypoint-
    initdb.d, which Postgres runs EXACTLY ONCE - only against a completely
    empty data directory. Once db_data (the named volume - see
    generate_docker_compose) has been initialized by the very first deploy,
    Postgres silently skips docker-entrypoint-initdb.d on every later boot,
    even though schema.sql may have since changed. Without this, an update
    that changes schema.sql would update the FILE and any FUTURE fresh
    volume, but never the actual already-running, already-populated
    database - directly contradicting "generate migrations instead of
    replacing schema.sql" / "do not recreate the database."

    This is what makes that requirement real: every DDL statement in
    schema.sql is now generated to be idempotent (CREATE TABLE/INDEX IF NOT
    EXISTS, ADD COLUMN IF NOT EXISTS - see database_skills.py's generation
    prompts), so safely re-running the whole file against a database that
    already has some or all of it applied only takes effect for what's
    genuinely new - existing tables/data are left untouched. Safe to call
    on every deploy (fresh build or update alike), not just updates - a
    no-op the first time, since initdb already applied the identical file.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "sh", "-c",
             'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
             '-f /docker-entrypoint-initdb.d/schema.sql'],
            cwd=str(project_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        ok = result.returncode == 0
        detail = result.stdout[-500:] if ok else (result.stderr or result.stdout)[-500:]
        return ok, detail
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)[:300]


def get_service_logs(project_dir: Path, service: str, tail: int = 30) -> str:
    """
    Real, observed gap this closes: CICD_VAL previously only ever reported
    "frontend: state=running, health=unhealthy" - true, but useless for
    routing a fix, since neither the Supervisor nor whichever agent it routes
    to can act on that alone. The ACTUAL cause (confirmed live: a frontend
    importing "@tanstack/react-query" that was never added to package.json,
    so Vite crashed on every request) only ever showed up in
    `docker compose logs frontend`, which nothing captured anywhere in the
    pipeline's own state/report. This pulls the real container output so a
    failure's stage_feedback names the real problem, not just a status word.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", service, "--tail", str(tail)],
            cwd=str(project_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        return (result.stdout or result.stderr or "(no log output)")[-1500:]
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"(couldn't fetch logs: {e})"


def write_docker_assets(project_dir: Path, workspace: dict) -> list[str]:
    """
    Write Dockerfile(s) + docker-compose.yml into the packaged project.
    Returns the list of artifact paths written (for the package manifest).
    """
    from skills.project_registry import allocate_ports, db_name_for

    written = []
    project_id = project_dir.name

    has_backend = "backend" in workspace and bool(workspace["backend"].get("files"))
    has_frontend = "frontend" in workspace and bool(workspace["frontend"].get("files"))
    has_database = "database" in workspace and bool(workspace["database"].get("schema"))

    backend_container_port = 8000
    frontend_container_port = 5173

    if has_backend:
        backend_dockerfile, backend_container_port = generate_backend_dockerfile(workspace["backend"]["files"])
        (project_dir / "backend" / "Dockerfile").write_text(backend_dockerfile, encoding="utf-8")
        written.append("backend/Dockerfile")

    if has_frontend:
        frontend_dockerfile, frontend_container_port = generate_frontend_dockerfile(workspace["frontend"]["files"])
        (project_dir / "frontend" / "Dockerfile").write_text(frontend_dockerfile, encoding="utf-8")
        written.append("frontend/Dockerfile")

    if has_backend or has_frontend:
        host_ports = allocate_ports(project_id)  # stable per project_id, unique across projects

        compose = generate_docker_compose(
            has_backend, has_frontend, has_database,
            backend_container_port, host_ports["backend"],
            frontend_container_port, host_ports["frontend"],
            host_ports["db"],
        )
        (project_dir / "docker-compose.yml").write_text(compose, encoding="utf-8")
        written.append("docker-compose.yml")

        if has_database:
            pg = get_postgres_config()
            db_name = db_name_for(project_id)  # unique per project - no cross-project name collisions
            # host is "db" (the compose service name, port 5432 inside the
            # compose network) - this is how the backend container reaches
            # Postgres, independent of whatever host port is exposed outside.
            database_url = f"postgresql://{pg['user']}:{pg['password']}@db:5432/{db_name}"
            env_content = (
                f"POSTGRES_USER={pg['user']}\n"
                f"POSTGRES_PASSWORD={pg['password']}\n"
                f"POSTGRES_DB={db_name}\n"
                f"DATABASE_URL={database_url}\n"
            )
            (project_dir / ".env").write_text(env_content, encoding="utf-8")
            written.append(".env")

        if has_frontend and has_backend:
            # Vite reads .env from the directory `npm run dev`/`vite` is
            # invoked from - this makes VITE_API_BASE_URL resolve correctly
            # both inside the frontend container and for a plain local
            # `npm run dev` outside Docker, matching whatever backend port
            # was actually allocated for this project (never hardcoded).
            frontend_env = f"VITE_API_BASE_URL=http://localhost:{host_ports['backend']}\n"
            (project_dir / "frontend" / ".env").write_text(frontend_env, encoding="utf-8")
            written.append("frontend/.env")

    return written


def generate_start_scripts(project_dir: Path, workspace: dict) -> list[str]:
    """
    Generate start.sh + start.bat - a fallback that runs this project as
    plain local processes instead of `docker compose up`, for whenever
    Docker isn't installed or Docker Desktop isn't running. Reuses the exact
    same stack-detection logic as the Dockerfiles, so the commands always
    match what was actually generated, and the same allocated ports as
    docker-compose.yml, so both paths are interchangeable.

    Unlike write_docker_assets' root .env (whose DATABASE_URL points at the
    Docker-Compose-internal "db" hostname - meaningless outside that
    network), this points at localhost:<allocated db port>, since these
    scripts run backend/frontend as native processes with no Docker network
    to hide behind.

    Returns the list of artifact paths written (for the package manifest).
    """
    from skills.project_registry import allocate_ports, db_name_for

    written = []
    project_id = project_dir.name

    backend_files = workspace.get("backend", {}).get("files", {})
    frontend_files = workspace.get("frontend", {}).get("files", {})
    has_backend = bool(backend_files)
    has_frontend = bool(frontend_files)
    has_database = bool(workspace.get("database", {}).get("schema"))

    if not (has_backend or has_frontend):
        return written

    host_ports = allocate_ports(project_id)  # same allocation write_docker_assets uses
    pg = get_postgres_config()
    db_name = db_name_for(project_id)
    database_url = f"postgresql://{pg['user']}:{pg['password']}@localhost:{host_ports['db']}/{db_name}"

    backend_cmd = None
    if has_backend:
        stack = detect_backend_stack(backend_files)
        if stack == "node":
            script = _node_script(backend_files, ["start", "serve"])
            backend_cmd = f"npm run {script}"
        else:
            backend_cmd, _ = _backend_run_command(backend_files, port=host_ports["backend"])

    frontend_is_node = has_frontend and detect_frontend_stack(frontend_files) == "node"
    frontend_cmd = None
    if frontend_is_node:
        script = _node_script(frontend_files, ["dev", "start"])
        frontend_cmd = f"npm run {script} -- --host 0.0.0.0 --port {host_ports['frontend']}"
    elif has_frontend:
        # Static HTML/CSS/JS, no build step - Python's stdlib http.server is
        # enough to actually serve it, no npm/node/nginx required locally.
        frontend_cmd = f"python3 -m http.server {host_ports['frontend']}"

    db_hint = (
        f'# Needs Postgres reachable at the DATABASE_URL below. Two ways to get that:\n'
        f'#   1. A local Postgres install - create a database named "{db_name}" there.\n'
        f'#   2. If Docker itself works but `docker compose` specifically is the problem,\n'
        f'#      start JUST the database as one container (schema.sql auto-applies):\n'
        f'#        docker run --name {project_id}_db --rm -d \\\n'
        f'#          -e POSTGRES_USER={pg["user"]} -e POSTGRES_PASSWORD={pg["password"]} -e POSTGRES_DB={db_name} \\\n'
        f'#          -p {host_ports["db"]}:5432 -v "$(pwd)/schema.sql:/docker-entrypoint-initdb.d/schema.sql:ro" \\\n'
        f'#          postgres:16-alpine\n'
    ) if has_database else ""

    # ---- start.sh (bash - Linux/Mac/Git Bash) ----
    sh_lines = [
        "#!/usr/bin/env bash",
        f"# Fallback launcher for {project_id} - runs backend/frontend as plain local",
        "# processes instead of `docker compose up`. Use this if Docker isn't installed",
        "# or Docker Desktop isn't running.",
        "#",
        "# Requires: Python 3.11+ and Node.js 20+ on PATH.",
        (db_hint.rstrip("\n") if db_hint else "").rstrip(),
        "set -e",
        'cd "$(dirname "$0")"',
        "",
    ]
    if has_database:
        sh_lines += [f'export DATABASE_URL="{database_url}"', ""]

    backend_pid_line = ""
    if has_backend:
        sh_lines += [
            f'echo "==> Backend starting on http://localhost:{host_ports["backend"]}"',
            "cd backend",
        ]
        if detect_backend_stack(backend_files) == "node":
            sh_lines += ["npm install"]
        else:
            sh_lines += [
                "if [ ! -d venv ]; then python3 -m venv venv; fi",
                "source venv/bin/activate",
                "pip install -q -r requirements.txt",
            ]
        sh_lines += [f"{backend_cmd} &", "BACKEND_PID=$!", "cd ..", ""]
        backend_pid_line = 'trap "kill $BACKEND_PID 2>/dev/null" EXIT'
        sh_lines += [backend_pid_line, ""]

    if has_frontend:
        sh_lines += [
            f'echo "==> Frontend starting on http://localhost:{host_ports["frontend"]}"',
            "cd frontend",
        ]
        if frontend_is_node:
            frontend_run_line = (
                f'VITE_API_BASE_URL="http://localhost:{host_ports["backend"]}" {frontend_cmd}'
                if has_backend else frontend_cmd
            )
            sh_lines += ["npm install", frontend_run_line]
        elif frontend_cmd:
            sh_lines += [frontend_cmd]
        else:
            sh_lines += ['echo "No dev server command detected for this frontend - serve frontend/ manually."']
        sh_lines += ["cd .."]
    elif has_backend:
        sh_lines += ["wait $BACKEND_PID"]

    (project_dir / "start.sh").write_text("\n".join(sh_lines) + "\n", encoding="utf-8")
    written.append("start.sh")

    # ---- start.bat (Windows) ----
    bat_lines = [
        "@echo off",
        f"REM Fallback launcher for {project_id} - runs backend/frontend as plain local",
        "REM processes instead of `docker compose up`. Use this if Docker isn't installed",
        "REM or Docker Desktop isn't running.",
        "REM",
        "REM Requires: Python 3.11+ and Node.js 20+ on PATH.",
    ]
    if has_database:
        bat_lines += [
            "REM Needs Postgres reachable at the DATABASE_URL below - a local install",
            f'REM (database name "{db_name}"), or if Docker itself works but `docker compose`',
            "REM specifically is the problem, start just the database:",
            f"REM   docker run --name {project_id}_db --rm -d -e POSTGRES_USER={pg['user']} "
            f"-e POSTGRES_PASSWORD={pg['password']} -e POSTGRES_DB={db_name} -p {host_ports['db']}:5432 "
            f'-v "%cd%\\schema.sql:/docker-entrypoint-initdb.d/schema.sql:ro" postgres:16-alpine',
            f'set DATABASE_URL={database_url}',
        ]
    bat_lines += ["cd /d %~dp0", ""]

    if has_backend:
        bat_lines += [f'echo Backend starting on http://localhost:{host_ports["backend"]}', "cd backend"]
        if detect_backend_stack(backend_files) == "node":
            bat_lines += ["call npm install", f'start "backend" cmd /k "{backend_cmd}"']
        else:
            bat_lines += [
                "if not exist venv (python -m venv venv)",
                "call venv\\Scripts\\activate.bat",
                "pip install -q -r requirements.txt",
                f'start "backend" cmd /k "{backend_cmd}"',
            ]
        bat_lines += ["cd ..", ""]

    if has_frontend:
        bat_lines += [f'echo Frontend starting on http://localhost:{host_ports["frontend"]}', "cd frontend"]
        if frontend_is_node:
            api_base = f"http://localhost:{host_ports['backend']}" if has_backend else "http://localhost:8000"
            bat_lines += [
                "call npm install",
                f"set VITE_API_BASE_URL={api_base}",
                f'call {frontend_cmd}',
            ]
        elif frontend_cmd:
            bat_lines += [f'start "frontend" cmd /k "{frontend_cmd}"']
        else:
            bat_lines += ["echo No dev server command detected for this frontend - serve frontend/ manually."]
        bat_lines += ["cd .."]

    (project_dir / "start.bat").write_text("\r\n".join(bat_lines) + "\r\n", encoding="utf-8")
    written.append("start.bat")

    return written

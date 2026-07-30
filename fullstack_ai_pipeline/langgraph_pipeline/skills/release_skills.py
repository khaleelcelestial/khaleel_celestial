"""
Release skills - Package and run project locally
"""

import shutil
from pathlib import Path

from core.logger import get_logger
from skills.docker_skills import write_docker_assets
from skills.project_registry import OUTPUT_BASE


def package_project_skill(workspace: dict, project_name: str) -> tuple[str, dict]:
    """
    Package all workspace files into an output directory.
    Returns: (output_path, package_manifest)
    """

    file_counts = {k: len(v["files"]) for k, v in workspace.items()
                  if isinstance(v, dict) and "files" in v}
    get_logger().info(f"Packaging workspace: {file_counts or 'no file-bearing sections'}")

    # Create output directory (anchored to this package, not the process cwd)
    output_base = OUTPUT_BASE
    output_base.mkdir(exist_ok=True)
    
    # Create project directory
    project_dir = output_base / project_name.replace(" ", "_").lower()
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True)
    
    package_manifest = {
        "project_name": project_name,
        "artifacts": []
    }
    
    # Write database schema (only if the database stage actually ran)
    if "database" in workspace and workspace["database"].get("schema"):
        schema_file = project_dir / "schema.sql"
        schema_file.write_text(workspace["database"]["schema"], encoding="utf-8")
        package_manifest["artifacts"].append("schema.sql")

    # Write contract (only if it actually ran)
    if "contract" in workspace and workspace["contract"].get("openapi_spec"):
        contract_file = project_dir / "openapi.yaml"
        contract_file.write_text(workspace["contract"]["openapi_spec"], encoding="utf-8")
        package_manifest["artifacts"].append("openapi.yaml")
    
    # Write backend files
    if "backend" in workspace and "files" in workspace["backend"]:
        backend_dir = project_dir / "backend"
        backend_dir.mkdir(exist_ok=True)
        for file_path, content in workspace["backend"]["files"].items():
            file_full_path = backend_dir / file_path
            file_full_path.parent.mkdir(parents=True, exist_ok=True)
            file_full_path.write_text(content, encoding="utf-8")
            package_manifest["artifacts"].append(f"backend/{file_path}")
    
    # Write frontend files
    if "frontend" in workspace and "files" in workspace["frontend"]:
        frontend_dir = project_dir / "frontend"
        frontend_dir.mkdir(exist_ok=True)
        for file_path, content in workspace["frontend"]["files"].items():
            file_full_path = frontend_dir / file_path
            file_full_path.parent.mkdir(parents=True, exist_ok=True)
            file_full_path.write_text(content, encoding="utf-8")
            package_manifest["artifacts"].append(f"frontend/{file_path}")
    
    # Write docs
    if "docs" in workspace:
        for doc_name, doc_content in workspace["docs"].items():
            doc_file = project_dir / doc_name
            doc_file.write_text(doc_content, encoding="utf-8")
            package_manifest["artifacts"].append(doc_name)

    # Write Dockerfile(s) + docker-compose.yml so the project is containerized
    docker_artifacts = write_docker_assets(project_dir, workspace)
    package_manifest["artifacts"].extend(docker_artifacts)

    return str(project_dir.absolute()), package_manifest

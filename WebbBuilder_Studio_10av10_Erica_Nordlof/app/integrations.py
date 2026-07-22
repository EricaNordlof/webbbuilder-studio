from __future__ import annotations

import base64
import os
import re
from typing import Any

import httpx


GITHUB_API = "https://api.github.com"
RENDER_API = "https://api.render.com/v1"
VERCEL_API = "https://api.vercel.com"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "EricaNordlof").strip()

RENDER_API_KEY = os.getenv("RENDER_API_KEY", "").strip()
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID", "").strip()
RENDER_REGION = os.getenv("RENDER_REGION", "frankfurt").strip() or "frankfurt"

VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "").strip()
VERCEL_TEAM_ID = os.getenv("VERCEL_TEAM_ID", "").strip()


class IntegrationError(RuntimeError):
    pass


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9åäö-]+", "-", value)
    value = (
        value.replace("å", "a")
        .replace("ä", "a")
        .replace("ö", "o")
    )
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] or "webbprojekt"


def integration_status() -> dict[str, bool | str]:
    return {
        "github_ready": bool(GITHUB_TOKEN),
        "github_owner": GITHUB_OWNER,
        "render_ready": bool(RENDER_API_KEY and RENDER_OWNER_ID),
        "vercel_ready": bool(VERCEL_TOKEN),
    }


def _github_headers() -> dict[str, str]:
    if not GITHUB_TOKEN:
        raise IntegrationError(
            "GITHUB_TOKEN saknas. Lägg en GitHub-token som servermiljövariabel."
        )
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2026-03-10",
    }


async def github_identity() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(
            f"{GITHUB_API}/user",
            headers=_github_headers(),
        )
    if response.status_code >= 400:
        raise IntegrationError(
            f"GitHub-inloggningen misslyckades ({response.status_code}): "
            f"{response.text[:500]}"
        )
    return response.json()


async def get_github_repo(
    owner: str,
    repo: str,
) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repo}",
            headers=_github_headers(),
        )

    if response.status_code == 404:
        return None

    if response.status_code >= 400:
        raise IntegrationError(
            f"GitHub kunde inte läsa repositoryt ({response.status_code}): "
            f"{response.text[:500]}"
        )

    return response.json()


async def create_github_repo(
    repo_name: str,
    description: str,
    private: bool = True,
) -> dict[str, Any]:
    payload = {
        "name": repo_name,
        "description": description[:300],
        "private": private,
        "auto_init": True,
        "has_issues": True,
        "has_projects": False,
        "has_wiki": False,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{GITHUB_API}/user/repos",
            headers=_github_headers(),
            json=payload,
        )

    if response.status_code >= 400:
        raise IntegrationError(
            f"GitHub kunde inte skapa repositoryt ({response.status_code}): "
            f"{response.text[:700]}"
        )

    return response.json()


async def ensure_github_repo(
    repo_name: str,
    description: str,
    private: bool,
) -> dict[str, Any]:
    repo = await get_github_repo(GITHUB_OWNER, repo_name)
    if repo:
        return repo

    created = await create_github_repo(
        repo_name=repo_name,
        description=description,
        private=private,
    )

    return created


async def _github_json(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.request(
            method,
            url,
            headers=_github_headers(),
            json=json_body,
        )

    if response.status_code >= 400:
        raise IntegrationError(
            f"GitHub API-fel ({response.status_code}): {response.text[:900]}"
        )

    if not response.content:
        return {}

    return response.json()


async def publish_files_to_github(
    repo_name: str,
    files: dict[str, str],
    commit_message: str,
    *,
    private: bool = True,
    description: str = "",
) -> dict[str, Any]:
    repo = await ensure_github_repo(
        repo_name=repo_name,
        description=description,
        private=private,
    )

    owner = repo["owner"]["login"]
    default_branch = repo.get("default_branch") or "main"

    # Repository is auto-initialized, so a branch/ref exists.
    ref = await _github_json(
        "GET",
        f"{GITHUB_API}/repos/{owner}/{repo_name}/git/ref/heads/{default_branch}",
    )
    parent_sha = ref["object"]["sha"]

    # Create one blob per file.
    tree_items: list[dict[str, str]] = []

    for path, content in sorted(files.items()):
        blob = await _github_json(
            "POST",
            f"{GITHUB_API}/repos/{owner}/{repo_name}/git/blobs",
            json_body={
                "content": base64.b64encode(
                    content.encode("utf-8")
                ).decode("ascii"),
                "encoding": "base64",
            },
        )
        tree_items.append(
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )

    # No base_tree: commit represents exactly the builder's current file set.
    tree = await _github_json(
        "POST",
        f"{GITHUB_API}/repos/{owner}/{repo_name}/git/trees",
        json_body={"tree": tree_items},
    )

    commit = await _github_json(
        "POST",
        f"{GITHUB_API}/repos/{owner}/{repo_name}/git/commits",
        json_body={
            "message": commit_message,
            "tree": tree["sha"],
            "parents": [parent_sha],
        },
    )

    await _github_json(
        "PATCH",
        f"{GITHUB_API}/repos/{owner}/{repo_name}/git/refs/heads/{default_branch}",
        json_body={
            "sha": commit["sha"],
            "force": False,
        },
    )

    return {
        "owner": owner,
        "repo": repo_name,
        "repo_id": str(repo["id"]),
        "repo_url": repo["html_url"],
        "branch": default_branch,
        "commit_sha": commit["sha"],
        "private": bool(repo.get("private")),
    }


def infer_deploy_config(
    files: dict[str, str],
    stack: str,
) -> dict[str, str]:
    names = {name.lower(): name for name in files}
    stack_lower = stack.lower()

    if "dockerfile" in names:
        return {
            "kind": "web_service",
            "runtime": "docker",
            "build_command": "",
            "start_command": "",
            "publish_path": "",
            "health_check_path": "/",
        }

    if "package.json" in names:
        package = files[names["package.json"]].lower()

        if "next" in package or "next.js" in stack_lower:
            return {
                "kind": "web_service",
                "runtime": "node",
                "build_command": "npm ci && npm run build",
                "start_command": "npm run start",
                "publish_path": "",
                "health_check_path": "/",
            }

        if (
            "vite" in package
            or "react" in stack_lower
            or "vue" in package
            or "svelte" in package
        ):
            return {
                "kind": "static_site",
                "runtime": "",
                "build_command": "npm ci && npm run build",
                "start_command": "",
                "publish_path": "dist",
                "health_check_path": "",
            }

        return {
            "kind": "web_service",
            "runtime": "node",
            "build_command": "npm ci",
            "start_command": "npm start",
            "publish_path": "",
            "health_check_path": "/",
        }

    if (
        "requirements.txt" in names
        or "pyproject.toml" in names
        or "fastapi" in stack_lower
        or "python" in stack_lower
    ):
        return {
            "kind": "web_service",
            "runtime": "python",
            "build_command": "pip install -r requirements.txt",
            "start_command": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
            "publish_path": "",
            "health_check_path": "/",
        }

    return {
        "kind": "static_site",
        "runtime": "",
        "build_command": "",
        "start_command": "",
        "publish_path": ".",
        "health_check_path": "",
    }


def _render_headers() -> dict[str, str]:
    if not RENDER_API_KEY or not RENDER_OWNER_ID:
        raise IntegrationError(
            "Render är inte konfigurerat. Sätt RENDER_API_KEY och RENDER_OWNER_ID."
        )
    return {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def create_render_service(
    *,
    service_name: str,
    repo_url: str,
    branch: str,
    files: dict[str, str],
    stack: str,
) -> dict[str, Any]:
    config = infer_deploy_config(files, stack)

    payload: dict[str, Any] = {
        "type": config["kind"],
        "name": slugify(service_name),
        "ownerId": RENDER_OWNER_ID,
        "repo": repo_url,
        "autoDeploy": "yes",
        "branch": branch,
    }

    if config["kind"] == "static_site":
        payload["serviceDetails"] = {
            "buildCommand": config["build_command"],
            "publishPath": config["publish_path"],
        }
    else:
        if config["runtime"] == "docker":
            service_details: dict[str, Any] = {
                "runtime": "docker",
                "envSpecificDetails": {
                    "dockerContext": ".",
                    "dockerfilePath": "./Dockerfile",
                },
                "plan": "free",
                "region": RENDER_REGION,
                "numInstances": 1,
                "healthCheckPath": config["health_check_path"],
            }
        else:
            service_details = {
                "runtime": config["runtime"],
                "envSpecificDetails": {
                    "buildCommand": config["build_command"],
                    "startCommand": config["start_command"],
                },
                "plan": "free",
                "region": RENDER_REGION,
                "numInstances": 1,
                "healthCheckPath": config["health_check_path"],
            }

        payload["serviceDetails"] = service_details

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{RENDER_API}/services",
            headers=_render_headers(),
            json=payload,
        )

    if response.status_code >= 400:
        raise IntegrationError(
            f"Render kunde inte skapa tjänsten ({response.status_code}): "
            f"{response.text[:1000]}"
        )

    data = response.json()
    service = data.get("service", data)

    return {
        "provider": "render",
        "service_id": service.get("id", ""),
        "url": service.get("url", ""),
        "dashboard_url": service.get("dashboardUrl", ""),
        "raw": data,
    }


async def trigger_render_deploy(
    service_id: str,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "clearCache": "do_not_clear",
    }
    if commit_sha:
        body["commitId"] = commit_sha

    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            f"{RENDER_API}/services/{service_id}/deploys",
            headers=_render_headers(),
            json=body,
        )

    if response.status_code >= 400:
        raise IntegrationError(
            f"Render deploy misslyckades ({response.status_code}): "
            f"{response.text[:1000]}"
        )

    data = response.json()
    deploy = data.get("deploy", data)

    return {
        "provider": "render",
        "deployment_id": deploy.get("id", ""),
        "status": deploy.get("status", "queued"),
        "url": "",
        "raw": data,
    }


def _vercel_headers() -> dict[str, str]:
    if not VERCEL_TOKEN:
        raise IntegrationError(
            "VERCEL_TOKEN saknas. Lägg token som servermiljövariabel."
        )
    return {
        "Authorization": f"Bearer {VERCEL_TOKEN}",
        "Content-Type": "application/json",
    }


async def deploy_vercel_from_github(
    *,
    project_name: str,
    github_repo_id: str,
    branch: str = "main",
    commit_sha: str | None = None,
) -> dict[str, Any]:
    git_source: dict[str, Any] = {
        "type": "github",
        "repoId": int(github_repo_id),
        "ref": branch,
    }
    if commit_sha:
        git_source["sha"] = commit_sha

    payload = {
        "name": slugify(project_name),
        "target": "production",
        "gitSource": git_source,
    }

    params: dict[str, str] = {"forceNew": "1"}
    if VERCEL_TEAM_ID:
        params["teamId"] = VERCEL_TEAM_ID

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{VERCEL_API}/v13/deployments",
            headers=_vercel_headers(),
            params=params,
            json=payload,
        )

    if response.status_code >= 400:
        raise IntegrationError(
            f"Vercel deploy misslyckades ({response.status_code}): "
            f"{response.text[:1000]}"
        )

    data = response.json()
    url = data.get("url", "")
    if url and not url.startswith("http"):
        url = f"https://{url}"

    return {
        "provider": "vercel",
        "deployment_id": data.get("id", ""),
        "status": data.get("readyState", data.get("status", "QUEUED")),
        "url": url,
        "raw": data,
    }

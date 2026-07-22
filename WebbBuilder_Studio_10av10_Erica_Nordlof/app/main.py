from __future__ import annotations

import difflib
import io
import os
import secrets
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote_plus

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db as database
from .generator import (
    build_preview_html,
    generate_project,
    refine_project,
)
from .integrations import (
    IntegrationError,
    create_render_service,
    deploy_vercel_from_github,
    github_identity,
    integration_status,
    publish_files_to_github,
    slugify,
    trigger_render_deploy,
)
from .quality import analyze_project


BASE_DIR = Path(__file__).resolve().parent

APP_NAME = os.getenv("APP_NAME", "WebbBuilder Studio")
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()
SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
    secrets.token_urlsafe(48),
)
SESSION_SECURE = (
    os.getenv("SESSION_SECURE", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

app = FastAPI(title=APP_NAME)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)


@app.on_event("startup")
def startup() -> None:
    database.init_db()


def is_authenticated(request: Request) -> bool:
    if not APP_PASSWORD:
        return True

    return request.session.get("authenticated") is True


def require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(401, "Logga in först.")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            forwarded_proto = request.headers.get("x-forwarded-proto")
            if forwarded_proto:
                expected = f"{forwarded_proto}://{request.headers.get('host', '')}"

            if origin.rstrip("/") != expected.rstrip("/"):
                raise HTTPException(
                    403,
                    "Begäran blockerades av säkerhetsskäl.",
                )

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    return response


@app.middleware("http")
async def protect_builder(request: Request, call_next):
    public_paths = {
        "/login",
        "/health",
    }

    if (
        APP_PASSWORD
        and request.url.path not in public_paths
        and not request.url.path.startswith("/static/")
        and not is_authenticated(request)
    ):
        return RedirectResponse(
            url="/login",
            status_code=303,
        )

    return await call_next(request)


# SessionMiddleware registreras efter de egna HTTP-middleware-funktionerna.
# Starlette bygger middleware-stacken i omvänd registreringsordning, vilket
# gör att sessionsdata då finns innan protect_builder läser request.session.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SESSION_SECURE,
    same_site="lax",
)


@app.get("/health")
def health():
    return {
        "ok": True,
        "app": APP_NAME,
    }


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "app_name": APP_NAME,
            "error": None,
        },
    )


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    password: str = Form(...),
):
    if not APP_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=303)

    if not secrets.compare_digest(
        password.encode("utf-8"),
        APP_PASSWORD.encode("utf-8"),
    ):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "app_name": APP_NAME,
                "error": "Fel lösenord.",
            },
            status_code=400,
        )

    request.session.clear()
    request.session["authenticated"] = True

    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    projects = database.list_projects()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "app_name": APP_NAME,
            "projects": projects,
            "ai_ready": bool(
                os.getenv("OPENAI_API_KEY", "").strip()
            ),
            "integrations": integration_status(),
        },
    )


@app.get("/integrations", response_class=HTMLResponse)
async def integrations_page(request: Request):
    status = integration_status()
    github_user = None
    github_error = None

    if status["github_ready"]:
        try:
            github_user = await github_identity()
        except Exception as exc:
            github_error = str(exc)

    return templates.TemplateResponse(
        request,
        "integrations.html",
        {
            "app_name": APP_NAME,
            "integrations": status,
            "github_user": github_user,
            "github_error": github_error,
        },
    )


@app.get("/projects/new", response_class=HTMLResponse)
def new_project_page(request: Request):
    return templates.TemplateResponse(
        request,
        "new_project.html",
        {
            "app_name": APP_NAME,
        },
    )


@app.post("/projects/new")
def create_project(
    request: Request,
    name: str = Form(...),
    project_type: str = Form(...),
    stack: str = Form(...),
    brief: str = Form(...),
):
    require_auth(request)

    if not name.strip():
        raise HTTPException(400, "Projektnamn krävs.")

    if len(brief.strip()) < 20:
        raise HTTPException(
            400,
            "Beskriv projektet lite tydligare.",
        )

    project_id = database.create_project(
        name=name,
        project_type=project_type,
        stack=stack,
        brief=brief,
    )

    return RedirectResponse(
        f"/projects/{project_id}",
        status_code=303,
    )


def project_template_context(
    request: Request,
    project_id: str,
) -> dict:
    project = database.get_project(project_id)

    if not project:
        raise HTTPException(404, "Projektet hittades inte.")

    revision = database.latest_revision(project_id)
    revisions = database.list_revisions(project_id)
    quality = (
        analyze_project(revision["files"], project["stack"])
        if revision
        else None
    )

    return {
        "app_name": APP_NAME,
        "project": project,
        "revision": revision,
        "revisions": revisions,
        "file_names": (
            sorted(revision["files"].keys())
            if revision
            else []
        ),
        "quality": quality,
        "ai_ready": bool(
            os.getenv("OPENAI_API_KEY", "").strip()
        ),
        "integrations": integration_status(),
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    }


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(
    request: Request,
    project_id: str,
):
    return templates.TemplateResponse(
        request,
        "project.html",
        project_template_context(request, project_id),
    )


async def publish_pipeline(
    project_id: str,
    *,
    provider: str,
    repo_name: str | None = None,
    private_repo: bool = True,
) -> dict:
    project = database.get_project(project_id)
    revision = database.latest_revision(project_id)

    if not project or not revision:
        raise IntegrationError(
            "Projektet måste ha minst en genererad revision."
        )

    quality = analyze_project(
        revision["files"],
        project["stack"],
    )

    if quality["deploy_blocked"]:
        raise IntegrationError(
            "Publicering stoppades eftersom kvalitetskontrollen "
            "hittade en möjlig hemlighet eller annan kritisk risk."
        )

    chosen_repo = (
        repo_name.strip()
        if repo_name and repo_name.strip()
        else project.get("github_repo")
        or slugify(project["name"])
    )

    github = await publish_files_to_github(
        repo_name=chosen_repo,
        files=revision["files"],
        commit_message=(
            f"WebbBuilder revision {revision['revision_number']}: "
            f"{revision['instruction'][:120]}"
        ),
        private=private_repo,
        description=(
            f"{project['name']} – generated and maintained "
            "with WebbBuilder Studio"
        ),
    )

    database.update_project(
        project_id,
        github_repo=github["repo"],
        github_repo_id=github["repo_id"],
        github_repo_url=github["repo_url"],
        github_branch=github["branch"],
        last_commit_sha=github["commit_sha"],
    )

    result = {
        "github": github,
        "provider": "github",
        "deployment": None,
    }

    if provider == "github":
        database.add_deployment(
            project_id,
            "github",
            "published",
            url=github["repo_url"],
            commit_sha=github["commit_sha"],
            message="Kod publicerad till GitHub.",
        )
        return result

    if provider == "render":
        refreshed = database.get_project(project_id) or project

        if refreshed.get("deploy_service_id"):
            deployment = await trigger_render_deploy(
                refreshed["deploy_service_id"],
                github["commit_sha"],
            )
            service_id = refreshed["deploy_service_id"]
            live_url = refreshed.get("live_url") or ""
        else:
            deployment = await create_render_service(
                service_name=project["name"],
                repo_url=github["repo_url"],
                branch=github["branch"],
                files=revision["files"],
                stack=project["stack"],
            )
            service_id = deployment.get("service_id", "")
            live_url = deployment.get("url", "")

        database.update_project(
            project_id,
            deploy_provider="render",
            deploy_service_id=service_id,
            live_url=live_url,
        )

        database.add_deployment(
            project_id,
            "render",
            deployment.get("status", "created"),
            deployment_id=deployment.get("deployment_id", ""),
            service_id=service_id,
            url=live_url or deployment.get("url", ""),
            commit_sha=github["commit_sha"],
            message="Kod synkad till GitHub och skickad till Render.",
        )

        result["provider"] = "render"
        result["deployment"] = deployment
        return result

    if provider == "vercel":
        deployment = await deploy_vercel_from_github(
            project_name=project["name"],
            github_repo_id=github["repo_id"],
            branch=github["branch"],
            commit_sha=github["commit_sha"],
        )

        live_url = deployment.get("url", "")

        database.update_project(
            project_id,
            deploy_provider="vercel",
            deploy_service_id=deployment.get(
                "deployment_id",
                "",
            ),
            live_url=live_url,
        )

        database.add_deployment(
            project_id,
            "vercel",
            deployment.get("status", "created"),
            deployment_id=deployment.get(
                "deployment_id",
                "",
            ),
            url=live_url,
            commit_sha=github["commit_sha"],
            message="Kod synkad till GitHub och skickad till Vercel.",
        )

        result["provider"] = "vercel"
        result["deployment"] = deployment
        return result

    raise IntegrationError(
        "Okänd publiceringsleverantör."
    )


async def maybe_auto_publish(project_id: str) -> str | None:
    project = database.get_project(project_id)

    if not project or not project.get("auto_publish"):
        return None

    provider = project.get("deploy_provider") or "github"

    try:
        await publish_pipeline(
            project_id,
            provider=provider,
            repo_name=project.get("github_repo"),
            private_repo=True,
        )
        return None
    except Exception as exc:
        return str(exc)[:700]


@app.post("/projects/{project_id}/generate")
async def generate(
    request: Request,
    project_id: str,
):
    require_auth(request)

    project = database.get_project(project_id)

    if not project:
        raise HTTPException(404, "Projektet hittades inte.")

    try:
        result = await generate_project(
            name=project["name"],
            project_type=project["project_type"],
            stack=project["stack"],
            brief=project["brief"],
        )

        database.add_revision(
            project_id=project_id,
            instruction="Första genereringen",
            summary=result["summary"],
            files=result["files"],
            notes=result["notes"],
        )

        auto_error = await maybe_auto_publish(project_id)

    except Exception as exc:
        message = str(exc)[:700]
        return RedirectResponse(
            f"/projects/{project_id}?error={quote_plus(message)}",
            status_code=303,
        )

    if auto_error:
        return RedirectResponse(
            f"/projects/{project_id}?success=Projektet+har+genererats"
            f"&error={quote_plus('Auto-publicering misslyckades: ' + auto_error)}",
            status_code=303,
        )

    return RedirectResponse(
        f"/projects/{project_id}?success=Projektet+har+genererats",
        status_code=303,
    )


@app.post("/projects/{project_id}/refine")
async def refine(
    request: Request,
    project_id: str,
    instruction: str = Form(...),
):
    require_auth(request)

    project = database.get_project(project_id)
    current = database.latest_revision(project_id)

    if not project:
        raise HTTPException(404, "Projektet hittades inte.")

    if not current:
        raise HTTPException(
            400,
            "Generera projektet innan du finslipar det.",
        )

    if len(instruction.strip()) < 3:
        raise HTTPException(
            400,
            "Skriv vad du vill ändra.",
        )

    try:
        result = await refine_project(
            name=project["name"],
            project_type=project["project_type"],
            stack=project["stack"],
            original_brief=project["brief"],
            instruction=instruction,
            current_files=current["files"],
        )

        database.add_revision(
            project_id=project_id,
            instruction=instruction,
            summary=result["summary"],
            files=result["files"],
            notes=result["notes"],
        )

        auto_error = await maybe_auto_publish(project_id)

    except Exception as exc:
        message = str(exc)[:700]
        return RedirectResponse(
            f"/projects/{project_id}?error={quote_plus(message)}",
            status_code=303,
        )

    if auto_error:
        return RedirectResponse(
            f"/projects/{project_id}?success=Ändringen+är+sparad"
            f"&error={quote_plus('Auto-publicering misslyckades: ' + auto_error)}",
            status_code=303,
        )

    return RedirectResponse(
        f"/projects/{project_id}?success=Ändringen+är+sparad",
        status_code=303,
    )


@app.get(
    "/projects/{project_id}/preview",
    response_class=HTMLResponse,
)
def preview_project(
    request: Request,
    project_id: str,
):
    project = database.get_project(project_id)
    revision = database.latest_revision(project_id)

    if not project or not revision:
        raise HTTPException(
            404,
            "Ingen preview finns ännu.",
        )

    preview = build_preview_html(revision["files"])

    return HTMLResponse(
        preview,
        headers={
            "Content-Security-Policy": (
                "default-src 'self' data: blob: https:; "
                "img-src 'self' data: blob: https:; "
                "style-src 'unsafe-inline' 'self' https:; "
                "script-src 'unsafe-inline' 'self' https:; "
                "font-src 'self' data: https:;"
            )
        },
    )


@app.get(
    "/projects/{project_id}/file",
    response_class=HTMLResponse,
)
def edit_file_page(
    request: Request,
    project_id: str,
    path: str,
):
    project = database.get_project(project_id)
    revision = database.latest_revision(project_id)

    if not project or not revision:
        raise HTTPException(
            404,
            "Projektet hittades inte.",
        )

    if path not in revision["files"]:
        raise HTTPException(
            404,
            "Filen hittades inte.",
        )

    return templates.TemplateResponse(
        request,
        "edit_file.html",
        {
            "app_name": APP_NAME,
            "project": project,
            "path": path,
            "content": revision["files"][path],
        },
    )


@app.post("/projects/{project_id}/file")
async def save_file(
    request: Request,
    project_id: str,
    path: str = Form(...),
    content: str = Form(...),
):
    require_auth(request)

    project = database.get_project(project_id)
    revision = database.latest_revision(project_id)

    if not project or not revision:
        raise HTTPException(
            404,
            "Projektet hittades inte.",
        )

    if path not in revision["files"]:
        raise HTTPException(
            404,
            "Filen hittades inte.",
        )

    files = dict(revision["files"])
    files[path] = content

    database.add_revision(
        project_id=project_id,
        instruction=f"Manuell redigering av {path}",
        summary=f"Filen {path} redigerades manuellt.",
        files=files,
        notes=[],
    )

    auto_error = await maybe_auto_publish(project_id)

    if auto_error:
        return RedirectResponse(
            f"/projects/{project_id}?success=Filen+är+sparad"
            f"&error={quote_plus('Auto-publicering misslyckades: ' + auto_error)}",
            status_code=303,
        )

    return RedirectResponse(
        f"/projects/{project_id}?success=Filen+är+sparad",
        status_code=303,
    )


@app.post(
    "/projects/{project_id}/rollback/{revision_number}"
)
async def rollback(
    request: Request,
    project_id: str,
    revision_number: int,
):
    require_auth(request)

    revision = database.get_revision(
        project_id,
        revision_number,
    )

    if not revision:
        raise HTTPException(
            404,
            "Revisionen hittades inte.",
        )

    database.add_revision(
        project_id=project_id,
        instruction=(
            f"Återställning till revision "
            f"{revision_number}"
        ),
        summary=(
            f"Projektet återställdes till revision "
            f"{revision_number}."
        ),
        files=revision["files"],
        notes=[
            "Detta är en ny revision baserad på en äldre version."
        ],
    )

    auto_error = await maybe_auto_publish(project_id)

    if auto_error:
        return RedirectResponse(
            f"/projects/{project_id}?success=Revisionen+är+återställd"
            f"&error={quote_plus('Auto-publicering misslyckades: ' + auto_error)}",
            status_code=303,
        )

    return RedirectResponse(
        f"/projects/{project_id}?success=Revisionen+är+återställd",
        status_code=303,
    )


@app.get(
    "/projects/{project_id}/diff",
    response_class=HTMLResponse,
)
def revision_diff(
    request: Request,
    project_id: str,
    from_rev: int | None = Query(None),
    to_rev: int | None = Query(None),
):
    project = database.get_project(project_id)
    revisions = database.list_revisions(project_id)

    if not project or not revisions:
        raise HTTPException(
            404,
            "Projekt eller revisioner saknas.",
        )

    latest_number = revisions[0]["revision_number"]

    chosen_to = to_rev or latest_number
    chosen_from = (
        from_rev
        if from_rev is not None
        else max(1, chosen_to - 1)
    )

    old = database.get_revision(
        project_id,
        chosen_from,
    )
    new = database.get_revision(
        project_id,
        chosen_to,
    )

    if not old or not new:
        raise HTTPException(
            404,
            "Vald revision hittades inte.",
        )

    paths = sorted(
        set(old["files"])
        | set(new["files"])
    )

    diffs = []

    for path in paths:
        before = old["files"].get(path, "")
        after = new["files"].get(path, "")

        if before == after:
            continue

        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"rev-{chosen_from}/{path}",
                tofile=f"rev-{chosen_to}/{path}",
                n=3,
            )
        )

        diffs.append(
            {
                "path": path,
                "diff": diff,
            }
        )

    return templates.TemplateResponse(
        request,
        "diff.html",
        {
            "app_name": APP_NAME,
            "project": project,
            "revisions": revisions,
            "from_rev": chosen_from,
            "to_rev": chosen_to,
            "diffs": diffs,
        },
    )


@app.get(
    "/projects/{project_id}/publish",
    response_class=HTMLResponse,
)
def publish_page(
    request: Request,
    project_id: str,
):
    project = database.get_project(project_id)
    revision = database.latest_revision(project_id)

    if not project:
        raise HTTPException(
            404,
            "Projektet hittades inte.",
        )

    quality = (
        analyze_project(
            revision["files"],
            project["stack"],
        )
        if revision
        else None
    )

    return templates.TemplateResponse(
        request,
        "publish.html",
        {
            "app_name": APP_NAME,
            "project": project,
            "revision": revision,
            "quality": quality,
            "integrations": integration_status(),
            "deployments": database.list_deployments(
                project_id
            ),
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )


@app.post("/projects/{project_id}/publish")
async def publish_project(
    request: Request,
    project_id: str,
    provider: str = Form(...),
    repo_name: str = Form(""),
    visibility: str = Form("private"),
    confirm_publish: str = Form(...),
):
    require_auth(request)

    if confirm_publish != "yes":
        raise HTTPException(
            400,
            "Bekräfta publiceringen.",
        )

    try:
        result = await publish_pipeline(
            project_id,
            provider=provider,
            repo_name=repo_name or None,
            private_repo=visibility != "public",
        )
    except Exception as exc:
        return RedirectResponse(
            f"/projects/{project_id}/publish"
            f"?error={quote_plus(str(exc)[:1000])}",
            status_code=303,
        )

    message = "Publiceringen är startad."

    github = result.get("github")
    if github:
        message = (
            f"GitHub uppdaterat: "
            f"{github['owner']}/{github['repo']}."
        )

    if result.get("provider") in {"render", "vercel"}:
        message += (
            f" Deploy till "
            f"{result['provider'].title()} startad."
        )

    return RedirectResponse(
        f"/projects/{project_id}/publish"
        f"?success={quote_plus(message)}",
        status_code=303,
    )


@app.post("/projects/{project_id}/auto-publish")
def toggle_auto_publish(
    request: Request,
    project_id: str,
    enabled: str = Form("no"),
    provider: str = Form("github"),
):
    require_auth(request)

    if provider not in {
        "github",
        "render",
        "vercel",
    }:
        raise HTTPException(
            400,
            "Ogiltig leverantör.",
        )

    database.update_project(
        project_id,
        auto_publish=1 if enabled == "yes" else 0,
        deploy_provider=provider,
    )

    return RedirectResponse(
        f"/projects/{project_id}/publish"
        "?success=Auto-publicering+uppdaterad",
        status_code=303,
    )


@app.get("/projects/{project_id}/download")
def download_project(
    request: Request,
    project_id: str,
):
    project = database.get_project(project_id)
    revision = database.latest_revision(project_id)

    if not project or not revision:
        raise HTTPException(
            404,
            "Projektet saknar filer.",
        )

    buffer = io.BytesIO()

    with zipfile.ZipFile(
        buffer,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        for path, content in revision["files"].items():
            archive.writestr(
                path,
                content.encode("utf-8"),
            )

        delivery = (
            f"Projekt: {project['name']}\n"
            f"Revision: {revision['revision_number']}\n"
            f"Sammanfattning: {revision['summary']}\n"
            f"Senaste ändring: {revision['instruction']}\n"
            f"GitHub: {project.get('github_repo_url') or '-'}\n"
            f"Live: {project.get('live_url') or '-'}\n"
        )

        archive.writestr(
            "_WEBBBUILDER_DELIVERY.txt",
            delivery.encode("utf-8"),
        )

    buffer.seek(0)

    safe_name = "".join(
        ch if ch.isalnum() or ch in "-_" else "-"
        for ch in project["name"]
    ).strip("-") or "webbprojekt"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{safe_name}-rev'
                f'{revision["revision_number"]}.zip"'
            )
        },
    )


def safe_zip_path(raw: str) -> str | None:
    path = PurePosixPath(
        raw.replace("\\", "/")
    )

    if (
        path.is_absolute()
        or ".." in path.parts
        or raw.endswith("/")
    ):
        return None

    clean = "/".join(
        part
        for part in path.parts
        if part not in {"", "."}
    )

    return clean or None


@app.post("/projects/import")
async def import_project(
    request: Request,
    name: str = Form(...),
    stack: str = Form("Befintligt projekt"),
    archive: UploadFile = File(...),
):
    require_auth(request)

    if (
        not archive.filename
        or not archive.filename.lower().endswith(".zip")
    ):
        raise HTTPException(
            400,
            "Ladda upp en ZIP-fil.",
        )

    raw = await archive.read()

    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(
            413,
            "ZIP-filen är för stor. Max 25 MB.",
        )

    project_files: dict[str, str] = {}

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            members = z.infolist()

            if len(members) > 200:
                raise HTTPException(
                    400,
                    "ZIP-filen innehåller för många filer.",
                )

            for member in members:
                path = safe_zip_path(
                    member.filename
                )

                if not path:
                    continue

                if member.file_size > 1_500_000:
                    continue

                data = z.read(member)

                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue

                project_files[path] = text

    except zipfile.BadZipFile as exc:
        raise HTTPException(
            400,
            "Ogiltig ZIP-fil.",
        ) from exc

    if not project_files:
        raise HTTPException(
            400,
            "Ingen läsbar text-/kodfil hittades.",
        )

    project_id = database.create_project(
        name=name,
        project_type="Importerat projekt",
        stack=stack,
        brief=(
            "Importerat befintligt projekt. "
            "Finslipa det vidare utan att förstöra "
            "fungerande funktioner."
        ),
    )

    database.add_revision(
        project_id=project_id,
        instruction="Importerat ZIP-projekt",
        summary=(
            "Projektet importerades och är redo "
            "att finslipas."
        ),
        files=project_files,
        notes=[
            "Binära filer importeras inte i denna version."
        ],
    )

    return RedirectResponse(
        f"/projects/{project_id}",
        status_code=303,
    )


@app.post("/projects/{project_id}/delete")
def delete_project(
    request: Request,
    project_id: str,
):
    require_auth(request)

    database.delete_project(project_id)

    return RedirectResponse(
        "/",
        status_code=303,
    )

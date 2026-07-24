from __future__ import annotations

import asyncio
import io
import os
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .db import (
    add_revision,
    create_project,
    delete_project,
    get_project,
    init_db,
    latest_revision,
    list_projects,
    list_revisions,
    set_project_status,
)
from .generator import generate_project, refine_project

APP_NAME = os.getenv(
    "APP_NAME",
    "WebbBuilder Studio v2",
)

APP_PASSWORD = os.getenv(
    "APP_PASSWORD",
    "",
)

SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
    "dev-only-change-me",
)

SESSION_SECURE = (
    os.getenv(
        "SESSION_SECURE",
        "false",
    ).lower()
    == "true"
)

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(
    directory=str(
        BASE_DIR / "templates"
    )
)

RUNNING_TASKS: dict[str, asyncio.Task[Any]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=APP_NAME,
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SESSION_SECURE,
    same_site="lax",
)

app.mount(
    "/static",
    StaticFiles(
        directory=str(
            BASE_DIR / "static"
        )
    ),
    name="static",
)


def logged_in(request: Request) -> bool:
    return bool(
        request.session.get(
            "authenticated"
        )
    )


def require_login(request: Request) -> None:
    if not logged_in(request):
        raise HTTPException(
            status_code=401,
            detail="Inte inloggad.",
        )


def page_context(
    request: Request,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "request": request,
        "app_name": APP_NAME,
        **extra,
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "app": APP_NAME,
    }


@app.get(
    "/login",
    response_class=HTMLResponse,
)
def login_page(request: Request):
    if logged_in(request):
        return RedirectResponse(
            "/",
            status_code=303,
        )

    return templates.TemplateResponse(
        request,
        "login.html",
        page_context(
            request,
            error="",
        ),
    )


@app.post("/login")
def login(
    request: Request,
    password: str = Form(...),
):
    if not APP_PASSWORD:
        return templates.TemplateResponse(
            request,
            "login.html",
            page_context(
                request,
                error=(
                    "APP_PASSWORD saknas i serverns miljövariabler."
                ),
            ),
            status_code=500,
        )

    if password != APP_PASSWORD:
        return templates.TemplateResponse(
            request,
            "login.html",
            page_context(
                request,
                error="Fel lösenord.",
            ),
            status_code=400,
        )

    request.session[
        "authenticated"
    ] = True

    return RedirectResponse(
        "/",
        status_code=303,
    )


@app.post("/logout")
def logout(request: Request):
    request.session.clear()

    return RedirectResponse(
        "/login",
        status_code=303,
    )


@app.get(
    "/",
    response_class=HTMLResponse,
)
def home(request: Request):
    if not logged_in(request):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    return templates.TemplateResponse(
        request,
        "index.html",
        page_context(
            request,
            projects=list_projects(),
        ),
    )


@app.post("/projects")
def new_project(
    request: Request,
    name: str = Form(...),
    project_type: str = Form(...),
    brief: str = Form(...),
):
    require_login(request)

    name = name.strip()
    brief = brief.strip()

    if len(name) < 2:
        raise HTTPException(
            status_code=400,
            detail="Projektnamnet är för kort.",
        )

    if len(brief) < 10:
        raise HTTPException(
            status_code=400,
            detail="Beskriv projektet lite tydligare.",
        )

    project_id = create_project(
        name=name,
        project_type=project_type,
        brief=brief,
    )

    return RedirectResponse(
        f"/projects/{project_id}",
        status_code=303,
    )


@app.get(
    "/projects/{project_id}",
    response_class=HTMLResponse,
)
def project_page(
    request: Request,
    project_id: str,
):
    if not logged_in(request):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    project = get_project(project_id)

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Projektet hittades inte.",
        )

    revision = latest_revision(
        project_id
    )

    return templates.TemplateResponse(
        request,
        "project.html",
        page_context(
            request,
            project=project,
            revision=revision,
            revisions=list_revisions(
                project_id
            ),
        ),
    )


async def generation_worker(
    project_id: str,
) -> None:
    project = get_project(
        project_id
    )

    if not project:
        return

    try:
        set_project_status(
            project_id,
            "generating",
        )

        result = await generate_project(
            name=project["name"],
            project_type=project[
                "project_type"
            ],
            brief=project["brief"],
        )

        add_revision(
            project_id=project_id,
            instruction="Första versionen",
            summary=result["summary"],
            files=result["files"],
            notes=result["notes"],
        )

    except Exception as exc:
        print(
            f"[v2] Generation failed "
            f"project={project_id}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        set_project_status(
            project_id,
            "error",
            str(exc),
        )

    finally:
        RUNNING_TASKS.pop(
            project_id,
            None,
        )


async def refinement_worker(
    project_id: str,
    instruction: str,
) -> None:
    project = get_project(
        project_id
    )

    current = latest_revision(
        project_id
    )

    if not project or not current:
        return

    try:
        set_project_status(
            project_id,
            "generating",
        )

        result = await refine_project(
            name=project["name"],
            project_type=project[
                "project_type"
            ],
            original_brief=project["brief"],
            instruction=instruction,
            current_files=current["files"],
        )

        add_revision(
            project_id=project_id,
            instruction=instruction,
            summary=result["summary"],
            files=result["files"],
            notes=result["notes"],
        )

    except Exception as exc:
        print(
            f"[v2] Refinement failed "
            f"project={project_id}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        set_project_status(
            project_id,
            "error",
            str(exc),
        )

    finally:
        RUNNING_TASKS.pop(
            project_id,
            None,
        )


@app.post(
    "/api/projects/{project_id}/generate",
)
async def start_generation(
    request: Request,
    project_id: str,
):
    require_login(request)

    project = get_project(
        project_id
    )

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Projektet hittades inte.",
        )

    existing = RUNNING_TASKS.get(
        project_id
    )

    if existing and not existing.done():
        return {
            "ok": True,
            "status": "generating",
            "message": "Generering pågår redan.",
        }

    set_project_status(
        project_id,
        "queued",
    )

    task = asyncio.create_task(
        generation_worker(
            project_id
        )
    )

    RUNNING_TASKS[
        project_id
    ] = task

    return {
        "ok": True,
        "status": "queued",
    }


@app.post(
    "/api/projects/{project_id}/refine",
)
async def start_refinement(
    request: Request,
    project_id: str,
    instruction: str = Form(...),
):
    require_login(request)

    instruction = instruction.strip()

    if len(instruction) < 3:
        raise HTTPException(
            status_code=400,
            detail="Beskriv vad du vill ändra.",
        )

    project = get_project(
        project_id
    )

    current = latest_revision(
        project_id
    )

    if not project or not current:
        raise HTTPException(
            status_code=404,
            detail="Projekt eller revision saknas.",
        )

    existing = RUNNING_TASKS.get(
        project_id
    )

    if existing and not existing.done():
        return JSONResponse(
            {
                "ok": False,
                "error": "Ett AI-jobb pågår redan.",
            },
            status_code=409,
        )

    set_project_status(
        project_id,
        "queued",
    )

    task = asyncio.create_task(
        refinement_worker(
            project_id,
            instruction,
        )
    )

    RUNNING_TASKS[
        project_id
    ] = task

    return {
        "ok": True,
        "status": "queued",
    }


@app.get(
    "/api/projects/{project_id}/status",
)
def project_status(
    request: Request,
    project_id: str,
):
    require_login(request)

    project = get_project(
        project_id
    )

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Projektet hittades inte.",
        )

    revision = latest_revision(
        project_id
    )

    return {
        "id": project_id,
        "status": project["status"],
        "error": project["error"],
        "revision_number": (
            revision["revision_number"]
            if revision
            else 0
        ),
    }


@app.get(
    "/projects/{project_id}/preview",
    response_class=HTMLResponse,
)
def preview(
    request: Request,
    project_id: str,
):
    require_login(request)

    revision = latest_revision(
        project_id
    )

    if not revision:
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="sv">
            <meta charset="utf-8">
            <style>
              body{
                font-family:system-ui;
                padding:40px;
                background:#f4f7fb;
              }
            </style>
            <h1>Ingen version genererad ännu</h1>
            """
        )

    files = revision["files"]

    html_file = (
        files.get("preview.html")
        or files.get("index.html")
    )

    if not html_file:
        names = "".join(
            f"<li><code>{path}</code></li>"
            for path in sorted(files)
        )

        return HTMLResponse(
            f"""
            <!doctype html>
            <html lang="sv">
            <meta charset="utf-8">
            <style>
              body{{
                font-family:system-ui;
                padding:40px;
                background:#f4f7fb;
              }}
            </style>
            <h1>Ingen HTML-preview hittades</h1>
            <ul>{names}</ul>
            """
        )

    css = "\n".join(
        content
        for path, content in files.items()
        if path.lower().endswith(".css")
        and len(content) < 200000
    )

    js = "\n".join(
        content
        for path, content in files.items()
        if path.lower().endswith(".js")
        and not path.lower().endswith(".min.js")
        and len(content) < 150000
    )

    preview_html = html_file

    if css:
        block = "<style>" + css + "</style>"

        if "</head>" in preview_html.lower():
            index = preview_html.lower().rfind(
                "</head>"
            )

            preview_html = (
                preview_html[:index]
                + block
                + preview_html[index:]
            )
        else:
            preview_html = (
                block
                + preview_html
            )

    if js:
        block = "<script>" + js + "</script>"

        if "</body>" in preview_html.lower():
            index = preview_html.lower().rfind(
                "</body>"
            )

            preview_html = (
                preview_html[:index]
                + block
                + preview_html[index:]
            )
        else:
            preview_html += block

    return HTMLResponse(
        preview_html
    )


@app.get(
    "/projects/{project_id}/download",
)
def download_project(
    request: Request,
    project_id: str,
):
    require_login(request)

    project = get_project(
        project_id
    )

    revision = latest_revision(
        project_id
    )

    if not project or not revision:
        raise HTTPException(
            status_code=404,
            detail="Ingen genererad version hittades.",
        )

    buffer = io.BytesIO()

    with zipfile.ZipFile(
        buffer,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:

        for path, content in revision[
            "files"
        ].items():

            safe = Path(path)

            if (
                safe.is_absolute()
                or ".." in safe.parts
            ):
                continue

            archive.writestr(
                str(safe),
                content,
            )

    buffer.seek(0)

    filename = (
        "".join(
            char
            if char.isalnum()
            or char in {"-", "_"}
            else "-"
            for char in project["name"]
        ).strip("-")
        or "webbprojekt"
    )

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition":
                (
                    f'attachment; filename="'
                    f'{filename}.zip"'
                )
        },
    )


@app.post(
    "/projects/{project_id}/delete",
)
def remove_project(
    request: Request,
    project_id: str,
):
    require_login(request)

    delete_project(
        project_id
    )

    return RedirectResponse(
        "/",
        status_code=303,
    )

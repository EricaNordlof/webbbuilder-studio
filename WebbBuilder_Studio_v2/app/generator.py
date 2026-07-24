from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

import httpx

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
OPENAI_REQUEST_TIMEOUT = max(
    30,
    min(int(os.getenv("OPENAI_REQUEST_TIMEOUT", "180")), 600),
)
OPENAI_MAX_OUTPUT_TOKENS = max(
    3000,
    min(int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "12000")), 40000),
)
OPENAI_REASONING_EFFORT = os.getenv(
    "OPENAI_REASONING_EFFORT",
    "low",
).strip().lower()

MAX_PROJECT_FILES = 60
MAX_FILE_CHARS = 120000

PROJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        "notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "files", "notes"],
}

SYSTEM_PROMPT = """
Du är en senior fullstackutvecklare och UX-designer.

Du bygger kompletta webbprojekt från en användares specifikation.

VIKTIGT:
- Leverera faktiska kompletta filer, inte pseudokod.
- Skriv aldrig "... resten oförändrat".
- Prioritera fungerande, enkel och tydlig kod framför onödig komplexitet.
- Gör projektet responsivt och mobilvänligt.
- Lägg aldrig hemligheter eller API-nycklar i klientkod.
- För ren HTML/CSS/JS ska index.html kunna öppnas direkt.
- För projekt som kräver byggsteg eller backend ska preview.html finnas
  som fristående UI-förhandsvisning.
- Skapa README.md om startinstruktioner behövs.
- Skapa .env.example när miljövariabler krävs.
- Använd inga binära filer.
- Returnera endast data enligt JSON-schemat.
"""


def _safe_path(path: str) -> str:
    path = path.replace("\\", "/").strip().lstrip("/")
    path = re.sub(r"/+", "/", path)
    parts = [
        part
        for part in path.split("/")
        if part not in {"", ".", ".."}
    ]
    safe = "/".join(parts)
    if not safe:
        raise ValueError("Ogiltig filsökväg.")
    return safe[:300]


def _extract_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    texts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") not in {"output_text", "text"}:
                continue
            text = content.get("text", "")
            if isinstance(text, str):
                texts.append(text)
    return "".join(texts)


def _normalize_manifest(data: dict[str, Any]) -> dict[str, Any]:
    raw_files = data.get("files", [])
    if not isinstance(raw_files, list):
        raise RuntimeError("AI-svaret saknar en giltig fillista.")

    if len(raw_files) > MAX_PROJECT_FILES:
        raise RuntimeError("AI:n försökte skapa för många filer.")

    files: dict[str, str] = {}
    for item in raw_files:
        if not isinstance(item, dict):
            continue

        path = _safe_path(str(item.get("path", "")))
        content = str(item.get("content", ""))

        if len(content) > MAX_FILE_CHARS:
            raise RuntimeError(f"Filen {path} blev för stor.")

        files[path] = content

    if not files:
        raise RuntimeError("AI:n returnerade inga projektfiler.")

    notes = data.get("notes", [])
    if not isinstance(notes, list):
        notes = []

    return {
        "summary": str(data.get("summary", "")).strip()
        or "Projektet genererades.",
        "files": files,
        "notes": [
            str(note).strip()
            for note in notes
            if str(note).strip()
        ],
    }


def _api_error(response: httpx.Response) -> str:
    request_id = response.headers.get("x-request-id", "")

    try:
        payload = response.json()
    except Exception:
        payload = {}

    error = payload.get("error") if isinstance(payload, dict) else None
    message = ""

    if isinstance(error, dict):
        message = str(error.get("message", "")).strip()

    suffix = f" Request-id: {request_id}" if request_id else ""

    if response.status_code == 401:
        return "OpenAI API-nyckeln nekades." + suffix

    if response.status_code == 429:
        return (
            "OpenAI-krediten eller rate limit räcker inte för anropet. "
            "Kontrollera OpenAI Billing/Credits."
            + suffix
        )

    if response.status_code == 404:
        return (
            f"Modellen {OPENAI_MODEL!r} kunde inte användas."
            + suffix
        )

    return (
        f"OpenAI svarade med HTTP {response.status_code}: "
        f"{message or response.text[:700]}"
        + suffix
    )


async def call_openai(prompt: str) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY saknas i serverns miljövariabler."
        )

    payload: dict[str, Any] = {
        "model": OPENAI_MODEL,
        "instructions": SYSTEM_PROMPT,
        "input": prompt,
        "store": False,
        "max_output_tokens": OPENAI_MAX_OUTPUT_TOKENS,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "web_project_manifest",
                "strict": True,
                "schema": PROJECT_SCHEMA,
            }
        },
    }

    if (
        OPENAI_REASONING_EFFORT
        in {"none", "minimal", "low", "medium", "high", "xhigh"}
        and (
            OPENAI_MODEL.startswith("gpt-5")
            or OPENAI_MODEL.startswith("o")
        )
    ):
        payload["reasoning"] = {
            "effort": OPENAI_REASONING_EFFORT
        }

    timeout = httpx.Timeout(
        connect=20.0,
        read=float(OPENAI_REQUEST_TIMEOUT),
        write=30.0,
        pool=20.0,
    )

    started = time.monotonic()
    print(
        f"[v2] OpenAI start model={OPENAI_MODEL} "
        f"prompt_chars={len(prompt)}",
        flush=True,
    )

    try:
        async with asyncio.timeout(OPENAI_REQUEST_TIMEOUT):
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
    except TimeoutError as exc:
        raise RuntimeError(
            f"AI-genereringen överskred "
            f"{OPENAI_REQUEST_TIMEOUT} sekunder."
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(
            f"Kunde inte ansluta till OpenAI API: {exc}"
        ) from exc

    elapsed = int(time.monotonic() - started)
    print(
        f"[v2] OpenAI response status={response.status_code} "
        f"elapsed={elapsed}s",
        flush=True,
    )

    if response.status_code >= 400:
        raise RuntimeError(_api_error(response))

    response_payload = response.json()
    status = str(response_payload.get("status", "")).lower()

    if status == "incomplete":
        details = response_payload.get("incomplete_details") or {}
        reason = (
            details.get("reason")
            if isinstance(details, dict)
            else ""
        )
        raise RuntimeError(
            "AI-svaret blev ofullständigt"
            + (f": {reason}" if reason else ".")
        )

    if status in {"failed", "cancelled"}:
        raise RuntimeError(
            f"OpenAI avslutade anropet med status {status}."
        )

    output_text = _extract_output_text(response_payload)

    if not output_text.strip():
        raise RuntimeError(
            "AI-svaret saknade projektmanifest."
        )

    try:
        data = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "AI-svaret kunde inte tolkas som JSON."
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "AI-svaret hade fel format."
        )

    return _normalize_manifest(data)


async def generate_project(
    name: str,
    project_type: str,
    brief: str,
) -> dict[str, Any]:
    prompt = f"""
SKAPA ETT NYTT WEBBPROJEKT.

Namn:
{name}

Typ:
{project_type}

Önskemål:
{brief}

Välj själv den enklaste lämpliga tekniken.

För en vanlig informationswebbplats ska du föredra ren HTML, CSS och
JavaScript så att projektet blir lätt att förstå, ladda ner och publicera.

För en riktig webbapp får du välja en lämplig modern stack.

Skapa en komplett första version.
"""
    return await call_openai(prompt)


def _context_files(files: dict[str, str]) -> str:
    chunks: list[str] = []
    total = 0
    limit = 100000

    for path, content in sorted(files.items()):
        piece = f"\n--- {path} ---\n{content}\n"
        if total + len(piece) > limit:
            chunks.append(
                "\n[Övriga filer utelämnade ur prompten.]\n"
            )
            break
        chunks.append(piece)
        total += len(piece)

    return "".join(chunks)


async def refine_project(
    name: str,
    project_type: str,
    original_brief: str,
    instruction: str,
    current_files: dict[str, str],
) -> dict[str, Any]:
    prompt = f"""
FINSLIPA ETT BEFINTLIGT WEBBPROJEKT.

Namn:
{name}

Typ:
{project_type}

Ursprungligt önskemål:
{original_brief}

Ny ändring:
{instruction}

Returnera hela aktuella projektet efter ändringen.
Skriv kompletta filer, inte diffar.
Behåll fungerande funktionalitet.

Nuvarande filer:
{_context_files(current_files)}
"""

    result = await call_openai(prompt)

    merged = dict(current_files)
    merged.update(result["files"])
    result["files"] = merged

    return result

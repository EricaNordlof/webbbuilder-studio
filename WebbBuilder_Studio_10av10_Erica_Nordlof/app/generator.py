from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
from typing import Any

import httpx

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()
OPENAI_REQUEST_TIMEOUT = max(30, min(int(os.getenv("OPENAI_REQUEST_TIMEOUT", "180")), 600))
OPENAI_MAX_OUTPUT_TOKENS = max(4000, min(int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "20000")), 60000))
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low").strip().lower()
MAX_PROJECT_FILES = int(os.getenv("MAX_PROJECT_FILES", "80"))
MAX_FILE_CHARS = int(os.getenv("MAX_FILE_CHARS", "120000"))

PROJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "files": {
            "type": "array",
            "maxItems": MAX_PROJECT_FILES,
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
Du är en senior fullstackutvecklare, UX-designer och teknisk leveransansvarig.
Du bygger kompletta webbprojekt utifrån användarens specifikation.

MÅL:
Leverera faktiska filer som användaren kan ladda ner och fortsätta utveckla.
Gör inte bara mockups eller pseudokod.

REGLER:
1. Följ användarens önskemål exakt.
2. Skriv kompletta filer. Använd aldrig "... resten oförändrat".
3. Skapa responsiv, mobilvänlig och tillgänglig grundstruktur.
4. Lägg aldrig API-nycklar, lösenord eller andra hemligheter i klientkod.
5. Skapa README.md med start- och deployinstruktioner när projektet kräver det.
6. För en ren HTML/CSS/JS-sida ska index.html kunna öppnas direkt.
7. För React, Next, FastAPI, Node eller annan stack ska du dessutom skapa
   preview.html som är en självständig statisk förhandsvisning av UI:t.
8. preview.html får ha inline CSS/JS och får inte kräva byggsteg.
9. Använd inga binära filer.
10. För fullstackprojekt: skapa rimlig mappstruktur, validering, felhantering,
    .env.example, .gitignore och säker grundkonfiguration.
11. Gör projektet deploybart. Lägg till Dockerfile när backend/server kräver det.
    För statiska/Vite-projekt ska build/output vara tydligt dokumenterade.
12. Skapa en enkel /health eller motsvarande health endpoint för backendprojekt
    när det passar den valda stacken.
13. Behåll fungerande delar vid en refinering. Ändra inte sådant användaren
    inte bett om utan god teknisk anledning.
14. Ge korta notes om sådant som kräver externa konton, API-nycklar,
    databas, betalning eller juridisk granskning.
15. Lämna inte kvar lorem ipsum, example.com, TODO-platshållare eller falska
    kontaktuppgifter i en leveransklar version om användaren inte uttryckligen
    bett om dem.
16. Skriv README så att en annan utvecklare kan starta projektet utan att fråga.

SVAR:
Returnera endast data enligt det angivna JSON-schemat.
"""

def _safe_path(path: str) -> str:
    path = path.replace("\\", "/").strip().lstrip("/")
    path = re.sub(r"/+", "/", path)
    parts = [p for p in path.split("/") if p not in {"", ".", ".."}]
    safe = "/".join(parts)
    if not safe:
        raise ValueError("Tom eller ogiltig filsökväg.")
    return safe[:300]

def _extract_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    texts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                text = content.get("text", "")
                if isinstance(text, str):
                    texts.append(text)
    return "".join(texts)

def _normalize_manifest(data: dict[str, Any]) -> dict[str, Any]:
    files: dict[str, str] = {}
    for item in data.get("files", []):
        path = _safe_path(str(item.get("path", "")))
        content = str(item.get("content", ""))
        if len(content) > MAX_FILE_CHARS:
            raise ValueError(f"Filen {path} blev för stor.")
        files[path] = content
    if not files:
        raise ValueError("AI-svaret innehöll inga projektfiler.")
    return {
        "summary": str(data.get("summary", "")).strip() or "Projektet genererades.",
        "files": files,
        "notes": [
            str(note).strip()
            for note in data.get("notes", [])
            if str(note).strip()
        ],
    }

def _openai_error_message(response: httpx.Response) -> str:
    request_id = response.headers.get("x-request-id", "").strip()

    try:
        payload = response.json()
    except Exception:
        payload = {}

    error = payload.get("error") if isinstance(payload, dict) else None
    code = ""
    message = ""

    if isinstance(error, dict):
        code = str(error.get("code") or error.get("type") or "").strip()
        message = str(error.get("message") or "").strip()

    suffix = f" OpenAI request-id: {request_id}" if request_id else ""

    if response.status_code == 401:
        return (
            "OpenAI API-nyckeln nekades. Kontrollera OPENAI_API_KEY i Render."
            + suffix
        )

    if response.status_code == 429:
        lowered = (code + " " + message).lower()
        if "insufficient_quota" in lowered or "billing" in lowered or "quota" in lowered:
            return (
                "OpenAI API saknar tillgänglig kredit/betalningskvot. "
                "Kontrollera Billing/Credits i OpenAI Platform."
                + suffix
            )
        return (
            "OpenAI rate limit nåddes. Vänta en stund och försök igen."
            + suffix
        )

    if response.status_code == 404 and "model" in message.lower():
        return (
            f"Modellen {OPENAI_MODEL!r} är inte tillgänglig för API-nyckeln. "
            "Ändra OPENAI_MODEL i Render."
            + suffix
        )

    clean = message or str(payload)[:700] or response.text[:700]
    return (
        f"AI-anropet misslyckades ({response.status_code}): {clean}"
        + suffix
    )


async def _call_openai(prompt: str) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY saknas. Lägg nyckeln som en servermiljövariabel."
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
        OPENAI_REASONING_EFFORT in {"none", "minimal", "low", "medium", "high", "xhigh"}
        and (OPENAI_MODEL.startswith("gpt-5") or OPENAI_MODEL.startswith("o"))
    ):
        payload["reasoning"] = {"effort": OPENAI_REASONING_EFFORT}

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(
        connect=20.0,
        read=float(OPENAI_REQUEST_TIMEOUT),
        write=30.0,
        pool=20.0,
    )

    started = time.monotonic()
    print(
        f"[webbbuilder] OpenAI generation start: model={OPENAI_MODEL}, "
        f"max_output_tokens={OPENAI_MAX_OUTPUT_TOKENS}",
        flush=True,
    )

    try:
        async with asyncio.timeout(OPENAI_REQUEST_TIMEOUT):
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers=headers,
                    json=payload,
                )
    except TimeoutError as exc:
        elapsed = int(time.monotonic() - started)
        print(
            f"[webbbuilder] OpenAI generation hard-timeout after {elapsed}s",
            flush=True,
        )
        raise RuntimeError(
            f"AI-genereringen tog längre än {OPENAI_REQUEST_TIMEOUT} sekunder "
            "och avbröts. Försök igen med ett mindre första projekt eller en "
            "snabbare modell."
        ) from exc
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            "Tidsgränsen mot OpenAI API överskreds. Försök igen."
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(
            f"Kunde inte ansluta stabilt till OpenAI API: {exc}"
        ) from exc

    elapsed = int(time.monotonic() - started)
    print(
        f"[webbbuilder] OpenAI response received: "
        f"status={response.status_code}, elapsed={elapsed}s",
        flush=True,
    )

    if response.status_code >= 400:
        raise RuntimeError(_openai_error_message(response))

    response_payload = response.json()
    status = str(response_payload.get("status") or "").lower()

    if status in {"failed", "cancelled"}:
        raise RuntimeError(
            f"OpenAI avslutade genereringen med status: {status}."
        )

    if status == "incomplete":
        reason = (
            (response_payload.get("incomplete_details") or {}).get("reason")
            or "okänd orsak"
        )
        raise RuntimeError(
            "AI-svaret blev ofullständigt "
            f"({reason}). Höj OPENAI_MAX_OUTPUT_TOKENS eller be om ett mindre projekt."
        )

    output_text = _extract_output_text(response_payload)
    if not output_text.strip():
        raise RuntimeError("AI-svaret saknade textinnehåll.")

    try:
        data = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "AI-svaret kunde inte tolkas som projektmanifest."
        ) from exc

    return _normalize_manifest(data)

def _files_for_prompt(files: dict[str, str]) -> str:
    chunks: list[str] = []
    total = 0
    max_total = 180000

    preferred = sorted(
        files.items(),
        key=lambda pair: (
            0 if pair[0].lower() in {
                "index.html", "preview.html", "app.py", "main.py",
                "package.json", "styles.css", "app.js", "readme.md"
            } else 1,
            pair[0],
        ),
    )

    for path, content in preferred:
        piece = f"\n--- FIL: {path} ---\n{content}\n--- SLUT FIL: {path} ---\n"
        if total + len(piece) > max_total:
            chunks.append(
                "\n[Övriga filer utelämnades ur prompten på grund av storlek. "
                "Behåll dem oförändrade om de inte behöver ändras.]\n"
            )
            break
        chunks.append(piece)
        total += len(piece)

    return "".join(chunks)

async def generate_project(
    name: str,
    project_type: str,
    stack: str,
    brief: str,
) -> dict[str, Any]:
    prompt = f"""
SKAPA ETT NYTT PROJEKT.

Projektnamn:
{name}

Typ:
{project_type}

Önskad teknik:
{stack}

Användarens fullständiga önskemål:
{brief}

Leverera ett komplett körbart projekt för den valda stacken.
Skapa preview.html om projektet kräver byggsteg eller backend.
"""
    return await _call_openai(prompt)

async def refine_project(
    name: str,
    project_type: str,
    stack: str,
    original_brief: str,
    instruction: str,
    current_files: dict[str, str],
) -> dict[str, Any]:
    prompt = f"""
FINSLIPA ETT BEFINTLIGT PROJEKT.

Projektnamn:
{name}

Typ:
{project_type}

Teknik:
{stack}

Ursprungligt önskemål:
{original_brief}

NY ÄNDRINGSINSTRUKTION:
{instruction}

VIKTIGT:
- Returnera hela projektets aktuella filuppsättning efter ändringen.
- Skriv kompletta filer, inte diffar.
- Behåll funktioner och filer som fortfarande behövs.
- Förbättra inte bort befintlig funktionalitet.
- Uppdatera preview.html om UI:t ändras.

NUVARANDE FILER:
{_files_for_prompt(current_files)}
"""
    result = await _call_openai(prompt)
    merged = dict(current_files)
    merged.update(result["files"])
    result["files"] = merged
    return result

def build_preview_html(files: dict[str, str]) -> str:
    base = files.get("preview.html") or files.get("index.html")

    if not base:
        names = "\n".join(
            f"<li><code>{html.escape(path)}</code></li>"
            for path in sorted(files)
        )
        return f"""
        <!doctype html>
        <html lang="sv">
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Ingen preview</title>
        <style>
          body{{font-family:system-ui;padding:40px;background:#f6f7fb;color:#18202a}}
          .box{{max-width:760px;margin:auto;background:#fff;padding:28px;border-radius:20px}}
          code{{background:#eef1f5;padding:2px 5px;border-radius:6px}}
        </style>
        <div class="box">
          <h1>Ingen HTML-preview hittades</h1>
          <p>Projektet innehåller följande filer:</p>
          <ul>{names}</ul>
          <p>Be AI:n skapa <code>preview.html</code>.</p>
        </div>
        </html>
        """

    css_parts = []
    js_parts = []

    for path, content in files.items():
        low = path.lower()
        if low.endswith(".css") and len(content) < 250000:
            css_parts.append(f"\n/* Inlined preview: {path} */\n{content}\n")
        if (
            low.endswith(".js")
            and not low.endswith(".min.js")
            and len(content) < 200000
            and "/" not in low.replace("src/", "", 1)
        ):
            js_parts.append(f"\n/* Inlined preview: {path} */\n{content}\n")

    preview = base

    if css_parts:
        block = "<style>" + "\n".join(css_parts) + "</style>"
        low = preview.lower()
        if "</head>" in low:
            idx = low.rfind("</head>")
            preview = preview[:idx] + block + preview[idx:]
        else:
            preview = block + preview

    if js_parts:
        block = "<script>" + "\n".join(js_parts) + "</script>"
        low = preview.lower()
        if "</body>" in low:
            idx = low.rfind("</body>")
            preview = preview[:idx] + block + preview[idx:]
        else:
            preview += block

    return preview

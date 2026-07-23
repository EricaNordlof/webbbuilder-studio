from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
from typing import Any

import httpx


# ============================================================
# ENVIRONMENT / CONFIGURATION
# ============================================================


def _env_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """
    Läs ett heltal från miljövariabel utan att appen kraschar
    vid ett felaktigt värde.
    """
    raw = os.getenv(name, str(default)).strip()

    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default

    return max(
        minimum,
        min(value, maximum),
    )


OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    "",
).strip()

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5-mini",
).strip() or "gpt-5-mini"

OPENAI_REQUEST_TIMEOUT = _env_int(
    "OPENAI_REQUEST_TIMEOUT",
    180,
    30,
    600,
)

OPENAI_MAX_OUTPUT_TOKENS = _env_int(
    "OPENAI_MAX_OUTPUT_TOKENS",
    20000,
    4000,
    60000,
)

OPENAI_REASONING_EFFORT = os.getenv(
    "OPENAI_REASONING_EFFORT",
    "low",
).strip().lower()

MAX_PROJECT_FILES = _env_int(
    "MAX_PROJECT_FILES",
    80,
    1,
    250,
)

MAX_FILE_CHARS = _env_int(
    "MAX_FILE_CHARS",
    120000,
    1000,
    500000,
)

MAX_REFINEMENT_CONTEXT_CHARS = _env_int(
    "MAX_REFINEMENT_CONTEXT_CHARS",
    180000,
    20000,
    500000,
)


# ============================================================
# STRUCTURED OUTPUT SCHEMA
# ============================================================

#
# Filantalet valideras också i Python efter svaret.
# På så sätt kan vi ge ett tydligare felmeddelande och hålla
# JSON-schemat enkelt.
#

PROJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
        },
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {
                        "type": "string",
                    },
                    "content": {
                        "type": "string",
                    },
                },
                "required": [
                    "path",
                    "content",
                ],
            },
        },
        "notes": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
    },
    "required": [
        "summary",
        "files",
        "notes",
    ],
}


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
Du är en senior fullstackutvecklare, UX-designer och teknisk leveransansvarig.

Du bygger kompletta webbprojekt utifrån användarens specifikation.

MÅL:
Leverera faktiska, kompletta projektfiler som användaren kan ladda ner,
förhandsgranska, versionshantera, publicera och fortsätta utveckla.

Leverera inte bara mockups, kodfragment eller pseudokod.

REGLER:

1. Följ användarens önskemål så exakt som möjligt.

2. Skriv kompletta filer.
   Använd aldrig:
   - "... resten oförändrat"
   - "lägg till resten själv"
   - pseudokod i stället för faktisk implementation.

3. Skapa en responsiv, mobilvänlig och tillgänglig grundstruktur.

4. Lägg aldrig:
   - API-nycklar
   - lösenord
   - access tokens
   - andra hemligheter
   i klientkod eller hårdkodat i projektet.

5. Skapa README.md med tydliga instruktioner när projektet kräver det.

6. För en ren HTML/CSS/JS-webbplats:
   - index.html ska kunna öppnas direkt.
   - skapa komplett CSS och JavaScript.
   - externa byggsteg ska undvikas om de inte behövs.

7. För React, Next.js, FastAPI, Node, Django eller annan stack som kräver
   server eller byggsteg:
   - skapa projektets riktiga filer.
   - skapa dessutom preview.html.
   - preview.html ska vara en självständig statisk förhandsvisning av UI:t.

8. preview.html:
   - får använda inline CSS/JS.
   - får inte kräva npm install.
   - får inte kräva backend.
   - ska ge en representativ förhandsvisning av projektets UI.

9. Använd inga binära filer.

10. För fullstackprojekt ska projektet när det är relevant innehålla:
    - rimlig mappstruktur
    - validering
    - felhantering
    - .env.example
    - .gitignore
    - säker grundkonfiguration
    - README.md

11. Gör projektet deploybart.

12. Lägg till Dockerfile när projektet har backend/server och Docker är
    lämpligt.

13. För statiska eller Vite-baserade projekt:
    - dokumentera build command.
    - dokumentera output directory.
    - håll deployinstruktionerna tydliga.

14. För backendprojekt:
    skapa /health eller motsvarande health endpoint när det passar stacken.

15. Vid refinering:
    - behåll fungerande delar.
    - ändra bara det användaren bett om och sådant som tekniskt krävs.
    - ta inte bort fungerande funktionalitet utan god anledning.

16. Ge korta notes om sådant som kräver:
    - externa konton
    - API-nycklar
    - databas
    - betalningsleverantör
    - e-postleverantör
    - extern juridisk granskning.

17. Lämna inte kvar:
    - lorem ipsum
    - example.com
    - TODO-platshållare
    - falska kontaktuppgifter
    i en leveransklar version, om användaren inte uttryckligen bett om dem.

18. README.md ska vara tillräckligt tydlig för att en annan utvecklare
    ska kunna starta projektet utan att fråga.

19. Skapa inte fler filer än projektet faktiskt behöver.

20. Prioritera:
    - fungerande kod
    - enkel struktur
    - tydlig UX
    - säkerhet
    - mobilanpassning
    - enkel vidareutveckling.

SVAR:

Returnera endast data enligt det angivna JSON-schemat.

Returnera inga markdown-kodblock runt JSON-svaret.
"""


# ============================================================
# FILE / MANIFEST HELPERS
# ============================================================


def _safe_path(
    path: str,
) -> str:
    """
    Gör AI-genererade sökvägar säkra så att projektet inte kan
    skriva utanför sin projektkatalog.
    """

    path = (
        path
        .replace("\\", "/")
        .strip()
        .lstrip("/")
    )

    path = re.sub(
        r"/+",
        "/",
        path,
    )

    parts = [
        part
        for part in path.split("/")
        if part not in {
            "",
            ".",
            "..",
        }
    ]

    safe = "/".join(parts)

    if not safe:
        raise ValueError(
            "Tom eller ogiltig filsökväg."
        )

    if len(safe) > 300:
        raise ValueError(
            "En genererad filsökväg blev för lång."
        )

    return safe


def _extract_output_text(
    payload: dict[str, Any],
) -> str:
    """
    Hämta texten både från output_text och från Responses API:s
    output/content-struktur.
    """

    direct = payload.get(
        "output_text"
    )

    if (
        isinstance(direct, str)
        and direct.strip()
    ):
        return direct

    texts: list[str] = []

    output_items = payload.get(
        "output",
        [],
    )

    if not isinstance(
        output_items,
        list,
    ):
        return ""

    for item in output_items:
        if not isinstance(
            item,
            dict,
        ):
            continue

        content_items = item.get(
            "content",
            [],
        )

        if not isinstance(
            content_items,
            list,
        ):
            continue

        for content in content_items:
            if not isinstance(
                content,
                dict,
            ):
                continue

            content_type = str(
                content.get(
                    "type",
                    "",
                )
            )

            if content_type not in {
                "output_text",
                "text",
            }:
                continue

            text = content.get(
                "text",
                "",
            )

            if isinstance(
                text,
                str,
            ):
                texts.append(
                    text
                )

    return "".join(
        texts
    )


def _normalize_manifest(
    data: dict[str, Any],
) -> dict[str, Any]:
    """
    Kontrollera och normalisera projektmanifestet innan det sparas.
    """

    raw_files = data.get(
        "files",
        [],
    )

    if not isinstance(
        raw_files,
        list,
    ):
        raise ValueError(
            "AI-svaret hade ett ogiltigt files-fält."
        )

    if len(raw_files) > MAX_PROJECT_FILES:
        raise ValueError(
            f"AI:n skapade {len(raw_files)} filer, "
            f"men gränsen är {MAX_PROJECT_FILES}."
        )

    files: dict[str, str] = {}

    for item in raw_files:
        if not isinstance(
            item,
            dict,
        ):
            continue

        path = _safe_path(
            str(
                item.get(
                    "path",
                    "",
                )
            )
        )

        content = str(
            item.get(
                "content",
                "",
            )
        )

        if len(content) > MAX_FILE_CHARS:
            raise ValueError(
                f"Filen {path} blev för stor "
                f"({len(content)} tecken)."
            )

        files[path] = content

    if not files:
        raise ValueError(
            "AI-svaret innehöll inga projektfiler."
        )

    summary = str(
        data.get(
            "summary",
            "",
        )
    ).strip()

    raw_notes = data.get(
        "notes",
        [],
    )

    notes: list[str] = []

    if isinstance(
        raw_notes,
        list,
    ):
        notes = [
            str(note).strip()
            for note in raw_notes
            if str(note).strip()
        ]

    return {
        "summary":
            summary
            or "Projektet genererades.",
        "files":
            files,
        "notes":
            notes,
    }


# ============================================================
# OPENAI ERROR HANDLING
# ============================================================


def _openai_error_message(
    response: httpx.Response,
) -> str:
    request_id = response.headers.get(
        "x-request-id",
        "",
    ).strip()

    try:
        payload = response.json()
    except Exception:
        payload = {}

    error = (
        payload.get("error")
        if isinstance(
            payload,
            dict,
        )
        else None
    )

    code = ""
    message = ""

    if isinstance(
        error,
        dict,
    ):
        code = str(
            error.get("code")
            or error.get("type")
            or ""
        ).strip()

        message = str(
            error.get("message")
            or ""
        ).strip()

    suffix = (
        f" OpenAI request-id: {request_id}"
        if request_id
        else ""
    )

    if response.status_code == 400:
        clean = (
            message
            or response.text[:900]
        )

        return (
            "OpenAI avvisade AI-anropets konfiguration. "
            f"{clean}"
            + suffix
        )

    if response.status_code == 401:
        return (
            "OpenAI API-nyckeln nekades. "
            "Kontrollera OPENAI_API_KEY i Render."
            + suffix
        )

    if response.status_code == 403:
        return (
            "OpenAI nekade åtkomst till API-resursen. "
            "Kontrollera projekt, API-nyckel och modellbehörighet."
            + suffix
        )

    if response.status_code == 404:
        lowered = (
            code
            + " "
            + message
        ).lower()

        if (
            "model" in lowered
            or "not found" in lowered
        ):
            return (
                f"Modellen {OPENAI_MODEL!r} är inte tillgänglig "
                "för den här API-nyckeln eller projektet. "
                "Kontrollera OPENAI_MODEL i Render."
                + suffix
            )

    if response.status_code == 429:
        lowered = (
            code
            + " "
            + message
        ).lower()

        if (
            "insufficient_quota" in lowered
            or "billing" in lowered
            or "quota" in lowered
            or "credit" in lowered
        ):
            return (
                "OpenAI API saknar tillgänglig kredit eller betalningskvot. "
                "Kontrollera Billing/Credits i OpenAI Platform."
                + suffix
            )

        return (
            "OpenAI rate limit nåddes. "
            "Vänta en stund och försök igen."
            + suffix
        )

    if response.status_code >= 500:
        return (
            "OpenAI API hade ett tillfälligt serverfel "
            f"({response.status_code}). Försök igen."
            + suffix
        )

    clean = (
        message
        or (
            str(payload)[:900]
            if payload
            else response.text[:900]
        )
    )

    return (
        f"AI-anropet misslyckades "
        f"({response.status_code}): "
        f"{clean}"
        + suffix
    )


# ============================================================
# OPENAI API CALL
# ============================================================


async def _call_openai(
    prompt: str,
) -> dict[str, Any]:
    """
    Gör ett enda kontrollerat anrop till OpenAI Responses API.
    """

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY saknas. "
            "Lägg nyckeln som servermiljövariabel i Render."
        )

    if not prompt.strip():
        raise RuntimeError(
            "AI-prompten var tom."
        )

    payload: dict[str, Any] = {
        "model":
            OPENAI_MODEL,

        "instructions":
            SYSTEM_PROMPT,

        "input":
            prompt,

        "store":
            False,

        "max_output_tokens":
            OPENAI_MAX_OUTPUT_TOKENS,

        "text": {
            "format": {
                "type":
                    "json_schema",

                "name":
                    "web_project_manifest",

                "strict":
                    True,

                "schema":
                    PROJECT_SCHEMA,
            }
        },
    }

    supported_reasoning_values = {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    }

    if (
        OPENAI_REASONING_EFFORT
        in supported_reasoning_values
        and (
            OPENAI_MODEL.startswith(
                "gpt-5"
            )
            or OPENAI_MODEL.startswith(
                "o"
            )
        )
    ):
        payload["reasoning"] = {
            "effort":
                OPENAI_REASONING_EFFORT
        }

    headers = {
        "Authorization":
            f"Bearer {OPENAI_API_KEY}",

        "Content-Type":
            "application/json",
    }

    timeout = httpx.Timeout(
        connect=20.0,
        read=float(
            OPENAI_REQUEST_TIMEOUT
        ),
        write=30.0,
        pool=20.0,
    )

    started = time.monotonic()

    print(
        "[webbbuilder] ========================================",
        flush=True,
    )

    print(
        "[webbbuilder] OpenAI generation start",
        flush=True,
    )

    print(
        f"[webbbuilder] model={OPENAI_MODEL}",
        flush=True,
    )

    print(
        f"[webbbuilder] reasoning={OPENAI_REASONING_EFFORT}",
        flush=True,
    )

    print(
        f"[webbbuilder] max_output_tokens="
        f"{OPENAI_MAX_OUTPUT_TOKENS}",
        flush=True,
    )

    print(
        f"[webbbuilder] timeout="
        f"{OPENAI_REQUEST_TIMEOUT}s",
        flush=True,
    )

    print(
        f"[webbbuilder] prompt_chars="
        f"{len(prompt)}",
        flush=True,
    )

    try:
        async with asyncio.timeout(
            OPENAI_REQUEST_TIMEOUT
        ):
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:

                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers=headers,
                    json=payload,
                )

    except TimeoutError as exc:
        elapsed = int(
            time.monotonic()
            - started
        )

        print(
            "[webbbuilder] "
            f"OpenAI generation hard-timeout "
            f"after {elapsed}s",
            flush=True,
        )

        raise RuntimeError(
            f"AI-genereringen tog längre än "
            f"{OPENAI_REQUEST_TIMEOUT} sekunder och avbröts. "
            "Försök igen med ett mindre första projekt."
        ) from exc

    except httpx.TimeoutException as exc:
        elapsed = int(
            time.monotonic()
            - started
        )

        print(
            "[webbbuilder] "
            f"httpx timeout after {elapsed}s: "
            f"{type(exc).__name__}",
            flush=True,
        )

        raise RuntimeError(
            "Tidsgränsen mot OpenAI API överskreds. "
            "Försök igen."
        ) from exc

    except httpx.RequestError as exc:
        print(
            "[webbbuilder] "
            f"OpenAI network error: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        raise RuntimeError(
            "Kunde inte ansluta stabilt till OpenAI API: "
            f"{exc}"
        ) from exc

    elapsed = int(
        time.monotonic()
        - started
    )

    request_id = response.headers.get(
        "x-request-id",
        "",
    )

    print(
        "[webbbuilder] "
        f"OpenAI response received: "
        f"status={response.status_code}, "
        f"elapsed={elapsed}s, "
        f"request_id={request_id or 'saknas'}",
        flush=True,
    )

    if response.status_code >= 400:
        error_message = _openai_error_message(
            response
        )

        print(
            "[webbbuilder] "
            f"OpenAI error: {error_message}",
            flush=True,
        )

        raise RuntimeError(
            error_message
        )

    try:
        response_payload = response.json()

    except Exception as exc:
        print(
            "[webbbuilder] "
            "OpenAI returnerade ett svar som inte var JSON.",
            flush=True,
        )

        raise RuntimeError(
            "OpenAI returnerade ett ogiltigt API-svar."
        ) from exc

    if not isinstance(
        response_payload,
        dict,
    ):
        raise RuntimeError(
            "OpenAI returnerade ett oväntat API-format."
        )

    status = str(
        response_payload.get(
            "status"
        )
        or ""
    ).lower()

    print(
        "[webbbuilder] "
        f"OpenAI response status field="
        f"{status or 'saknas'}",
        flush=True,
    )

    if status in {
        "failed",
        "cancelled",
    }:
        error_obj = response_payload.get(
            "error"
        )

        details = ""

        if isinstance(
            error_obj,
            dict,
        ):
            details = str(
                error_obj.get(
                    "message"
                )
                or ""
            ).strip()

        message = (
            f"OpenAI avslutade genereringen "
            f"med status: {status}."
        )

        if details:
            message += (
                f" {details}"
            )

        raise RuntimeError(
            message
        )

    if status == "incomplete":
        incomplete_details = (
            response_payload.get(
                "incomplete_details"
            )
            or {}
        )

        reason = ""

        if isinstance(
            incomplete_details,
            dict,
        ):
            reason = str(
                incomplete_details.get(
                    "reason"
                )
                or ""
            )

        reason = (
            reason
            or "okänd orsak"
        )

        print(
            "[webbbuilder] "
            f"OpenAI incomplete response: "
            f"{reason}",
            flush=True,
        )

        raise RuntimeError(
            "AI-svaret blev ofullständigt "
            f"({reason}). "
            "Höj OPENAI_MAX_OUTPUT_TOKENS eller "
            "be om ett mindre projekt."
        )

    output_text = _extract_output_text(
        response_payload
    )

    print(
        "[webbbuilder] "
        f"OpenAI output chars="
        f"{len(output_text)}",
        flush=True,
    )

    if not output_text.strip():
        output_types: list[str] = []

        for item in response_payload.get(
            "output",
            [],
        ):
            if isinstance(
                item,
                dict,
            ):
                output_types.append(
                    str(
                        item.get(
                            "type",
                            "unknown",
                        )
                    )
                )

        print(
            "[webbbuilder] "
            f"No output text. "
            f"Output types={output_types}",
            flush=True,
        )

        raise RuntimeError(
            "AI-svaret saknade projektmanifest/textinnehåll."
        )

    try:
        data = json.loads(
            output_text
        )

    except json.JSONDecodeError as exc:
        preview = output_text[
            :1000
        ]

        print(
            "[webbbuilder] "
            "JSON parse failed. "
            f"Output start={preview!r}",
            flush=True,
        )

        raise RuntimeError(
            "AI-svaret kunde inte tolkas som projektmanifest."
        ) from exc

    if not isinstance(
        data,
        dict,
    ):
        raise RuntimeError(
            "AI-svaret hade fel projektmanifestformat."
        )

    try:
        manifest = _normalize_manifest(
            data
        )

    except Exception as exc:
        print(
            "[webbbuilder] "
            f"Manifest validation failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        raise

    print(
        "[webbbuilder] "
        f"Manifest OK: "
        f"{len(manifest['files'])} files",
        flush=True,
    )

    print(
        "[webbbuilder] "
        f"Generated files: "
        f"{', '.join(list(manifest['files'].keys())[:20])}",
        flush=True,
    )

    print(
        "[webbbuilder] OpenAI generation finished successfully",
        flush=True,
    )

    print(
        "[webbbuilder] ========================================",
        flush=True,
    )

    return manifest


# ============================================================
# REFINEMENT CONTEXT
# ============================================================


def _files_for_prompt(
    files: dict[str, str],
) -> str:
    """
    Skicka relevanta projektfiler till AI:n vid refinering utan
    att prompten växer obegränsat.
    """

    chunks: list[str] = []

    total = 0

    priority_names = {
        "index.html",
        "preview.html",
        "app.py",
        "main.py",
        "package.json",
        "requirements.txt",
        "styles.css",
        "style.css",
        "app.js",
        "main.js",
        "readme.md",
        "dockerfile",
    }

    preferred = sorted(
        files.items(),
        key=lambda pair: (
            0
            if pair[0].lower()
            in priority_names
            else 1,
            pair[0].lower(),
        ),
    )

    for path, content in preferred:
        piece = (
            f"\n"
            f"--- FIL: {path} ---\n"
            f"{content}\n"
            f"--- SLUT FIL: {path} ---\n"
        )

        if (
            total
            + len(piece)
            > MAX_REFINEMENT_CONTEXT_CHARS
        ):
            chunks.append(
                "\n"
                "[Övriga filer utelämnades ur prompten "
                "på grund av storlek. Behåll dem oförändrade "
                "om de inte behöver ändras.]\n"
            )

            break

        chunks.append(
            piece
        )

        total += len(
            piece
        )

    return "".join(
        chunks
    )


# ============================================================
# GENERATE PROJECT
# ============================================================


async def generate_project(
    name: str,
    project_type: str,
    stack: str,
    brief: str,
) -> dict[str, Any]:

    print(
        "[webbbuilder] "
        f"generate_project() called: "
        f"name={name!r}, "
        f"type={project_type!r}, "
        f"stack={stack!r}, "
        f"brief_chars={len(brief)}",
        flush=True,
    )

    prompt = f"""
SKAPA ETT NYTT KOMPLETT WEBBPROJEKT.

PROJEKTNAMN:
{name}

PROJEKTTYP:
{project_type}

ÖNSKAD TEKNIK:
{stack}

ANVÄNDARENS ÖNSKEMÅL:
{brief}

LEVERANSKRAV:

- Leverera ett komplett körbart projekt.
- Skapa alla filer som faktiskt krävs.
- Projektet ska vara mobilvänligt.
- Projektet ska vara användbart direkt.
- Undvik onödig komplexitet.
- Använd rimlig och säker kodstruktur.
- Skriv kompletta filer.
- Skapa README.md om projektet kräver installationssteg.
- Skapa preview.html om projektet kräver byggsteg eller backend.
- preview.html ska fungera fristående utan backend.
- Lämna inte TODO-text eller ofärdiga platshållare.
"""

    result = await _call_openai(
        prompt
    )

    print(
        "[webbbuilder] "
        f"generate_project() complete: "
        f"{len(result['files'])} files",
        flush=True,
    )

    return result


# ============================================================
# REFINE PROJECT
# ============================================================


async def refine_project(
    name: str,
    project_type: str,
    stack: str,
    original_brief: str,
    instruction: str,
    current_files: dict[str, str],
) -> dict[str, Any]:

    print(
        "[webbbuilder] "
        f"refine_project() called: "
        f"name={name!r}, "
        f"instruction_chars={len(instruction)}, "
        f"current_files={len(current_files)}",
        flush=True,
    )

    if not current_files:
        raise RuntimeError(
            "Projektet saknar en tidigare revision att finslipa."
        )

    if not instruction.strip():
        raise RuntimeError(
            "Ändringsinstruktionen är tom."
        )

    prompt = f"""
FINSLIPA ETT BEFINTLIGT WEBBPROJEKT.

PROJEKTNAMN:
{name}

PROJEKTTYP:
{project_type}

TEKNIK:
{stack}

URSPRUNGLIGT ÖNSKEMÅL:
{original_brief}

NY ÄNDRINGSINSTRUKTION:
{instruction}

VIKTIGA REGLER:

- Returnera kompletta filer.
- Returnera hela projektets aktuella filuppsättning efter ändringen.
- Skriv inte diffar.
- Skriv inte "... resten oförändrat".
- Behåll all fungerande funktionalitet som fortfarande behövs.
- Ta inte bort filer eller funktioner utan anledning.
- Ändra främst det användaren uttryckligen begärt.
- Uppdatera preview.html om UI eller design ändras.
- Kontrollera att projektet fortfarande är körbart efter ändringen.

NUVARANDE PROJEKTFILER:

{_files_for_prompt(current_files)}
"""

    result = await _call_openai(
        prompt
    )

    #
    # Säkerhetsnät:
    # Om AI:n trots instruktionen bara returnerar ändrade filer
    # behåller vi de befintliga filer som inte ersattes.
    #

    merged = dict(
        current_files
    )

    merged.update(
        result["files"]
    )

    if len(merged) > MAX_PROJECT_FILES:
        raise RuntimeError(
            f"Projektet skulle innehålla "
            f"{len(merged)} filer efter refinering, "
            f"men gränsen är {MAX_PROJECT_FILES}."
        )

    result["files"] = merged

    print(
        "[webbbuilder] "
        f"refine_project() complete: "
        f"{len(result['files'])} total files",
        flush=True,
    )

    return result


# ============================================================
# PREVIEW BUILDER
# ============================================================


def build_preview_html(
    files: dict[str, str],
) -> str:
    """
    Bygg en fristående HTML-preview.

    Prioritet:
    1. preview.html
    2. index.html
    3. fallback med fillista
    """

    base = (
        files.get(
            "preview.html"
        )
        or files.get(
            "index.html"
        )
    )

    if not base:
        names = "\n".join(
            (
                "<li>"
                f"<code>{html.escape(path)}</code>"
                "</li>"
            )
            for path in sorted(
                files
            )
        )

        return f"""
<!doctype html>
<html lang="sv">
<head>
  <meta charset="utf-8">
  <meta
    name="viewport"
    content="width=device-width,initial-scale=1"
  >
  <title>Ingen preview</title>

  <style>
    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
      padding: 40px 20px;
      background: #f6f7fb;
      color: #18202a;
    }}

    .box {{
      max-width: 760px;
      margin: auto;
      background: #ffffff;
      padding: 28px;
      border-radius: 20px;
      box-shadow:
        0 12px 40px
        rgba(0, 0, 0, 0.08);
    }}

    code {{
      background: #eef1f5;
      padding: 2px 5px;
      border-radius: 6px;
    }}

    li {{
      margin: 8px 0;
    }}
  </style>
</head>

<body>

  <div class="box">

    <h1>
      Ingen HTML-preview hittades
    </h1>

    <p>
      Projektet innehåller följande filer:
    </p>

    <ul>
      {names}
    </ul>

    <p>
      Be AI:n skapa
      <code>preview.html</code>
      för en fristående förhandsvisning.
    </p>

  </div>

</body>
</html>
"""

    css_parts: list[str] = []

    js_parts: list[str] = []

    for path, content in files.items():
        low = path.lower()

        if (
            low.endswith(".css")
            and len(content) < 250000
        ):
            css_parts.append(
                "\n"
                f"/* Inlined preview: {path} */\n"
                f"{content}\n"
            )

        #
        # Inkludera enklare JS-filer i previewn.
        # Undvik minifierade filer och alltför stora script.
        #

        if (
            low.endswith(".js")
            and not low.endswith(
                ".min.js"
            )
            and len(content) < 200000
        ):
            js_parts.append(
                "\n"
                f"/* Inlined preview: {path} */\n"
                f"{content}\n"
            )

    preview = base

    if css_parts:
        css_block = (
            "<style>\n"
            + "\n".join(
                css_parts
            )
            + "\n</style>"
        )

        lower_preview = (
            preview.lower()
        )

        if "</head>" in lower_preview:
            index = lower_preview.rfind(
                "</head>"
            )

            preview = (
                preview[:index]
                + css_block
                + "\n"
                + preview[index:]
            )

        else:
            preview = (
                css_block
                + "\n"
                + preview
            )

    if js_parts:
        js_block = (
            "<script>\n"
            + "\n".join(
                js_parts
            )
            + "\n</script>"
        )

        lower_preview = (
            preview.lower()
        )

        if "</body>" in lower_preview:
            index = lower_preview.rfind(
                "</body>"
            )

            preview = (
                preview[:index]
                + js_block
                + "\n"
                + preview[index:]
            )

        else:
            preview += (
                "\n"
                + js_block
            )

    return preview

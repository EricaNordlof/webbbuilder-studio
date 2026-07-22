from __future__ import annotations

import re
from typing import Any


SECRET_PATTERNS = [
    (
        "OpenAI-liknande API-nyckel",
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    ),
    (
        "Privat nyckel",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "GitHub-token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ),
]

PLACEHOLDER_PATTERNS = [
    "example.com",
    "your-api-key",
    "your_api_key",
    "change-me",
    "changeme",
    "todo:",
    "lorem ipsum",
]


def analyze_project(
    files: dict[str, str],
    stack: str,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    names = {path.lower() for path in files}

    def add(
        severity: str,
        title: str,
        detail: str,
        path: str = "",
    ) -> None:
        issues.append(
            {
                "severity": severity,
                "title": title,
                "detail": detail,
                "path": path,
            }
        )

    if not files:
        add("critical", "Projektet saknar filer", "Generera eller importera projektet först.")

    if "readme.md" not in names:
        add(
            "warning",
            "README saknas",
            "Lägg till start-, miljö- och deployinstruktioner.",
        )

    has_backend = any(
        key in stack.lower()
        for key in ("fastapi", "node", "express", "next", "backend", "python")
    ) or any(
        path in names
        for path in ("requirements.txt", "pyproject.toml", "package.json", "dockerfile")
    )

    if has_backend and ".env.example" not in names:
        add(
            "warning",
            ".env.example saknas",
            "Fullstackprojekt bör dokumentera nödvändiga miljövariabler utan riktiga hemligheter.",
        )

    if "preview.html" not in names and "index.html" not in names:
        add(
            "info",
            "Direkt preview saknas",
            "Lägg till preview.html för enklare visuell kontroll.",
        )

    for path, content in files.items():
        lower = content.lower()

        for label, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                add(
                    "critical",
                    f"Möjlig hemlighet hittad: {label}",
                    "Ta bort hemligheten och använd servermiljövariabler innan publicering.",
                    path,
                )

        if path.lower() != ".env.example":
            for marker in PLACEHOLDER_PATTERNS:
                if marker in lower:
                    add(
                        "warning",
                        "Möjlig platshållare kvar",
                        f"Hittade '{marker}'. Kontrollera innan leverans.",
                        path,
                    )
                    break

        if path.lower().endswith(".html"):
            if "<meta name=\"viewport\"" not in lower and "<meta name='viewport'" not in lower:
                add(
                    "warning",
                    "Viewport-meta saknas",
                    "Mobilanpassning kan fungera sämre utan viewport-meta.",
                    path,
                )

    critical = sum(1 for issue in issues if issue["severity"] == "critical")
    warning = sum(1 for issue in issues if issue["severity"] == "warning")
    info = sum(1 for issue in issues if issue["severity"] == "info")

    score = max(0, 100 - critical * 40 - warning * 7 - info * 2)

    return {
        "score": score,
        "critical": critical,
        "warning": warning,
        "info": info,
        "deploy_blocked": critical > 0,
        "issues": issues,
    }

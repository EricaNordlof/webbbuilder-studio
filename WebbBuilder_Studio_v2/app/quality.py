from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Iterable
from urllib.parse import unquote, urljoin, urlparse

import httpx

QUALITY_HTTP_TIMEOUT = max(
    3,
    min(int(os.getenv("QUALITY_HTTP_TIMEOUT", "10")), 30),
)
QUALITY_MAX_EXTERNAL_IMAGES = max(
    1,
    min(int(os.getenv("QUALITY_MAX_EXTERNAL_IMAGES", "30")), 100),
)

_IMAGE_EXTENSIONS = {
    ".avif",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}

_NUMBER_WORDS = {
    "en": 1,
    "ett": 1,
    "två": 2,
    "tre": 3,
    "fyra": 4,
    "fem": 5,
    "sex": 6,
    "sju": 7,
    "åtta": 8,
    "nio": 9,
    "tio": 10,
}


@dataclass(slots=True)
class HtmlReference:
    file_path: str
    tag: str
    attribute: str
    value: str
    alt: str = ""
    classes: str = ""
    element_id: str = ""


@dataclass(slots=True)
class QualityReport:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    image_count: int = 0
    external_image_count: int = 0

    def blocking_text(self) -> str:
        if not self.errors:
            return "Inga blockerande kvalitetsfel hittades."
        return "\n".join(f"- {item}" for item in self.errors)

    def notes(self) -> list[str]:
        result = [
            "Automatisk kvalitetskontroll: godkänd."
            if self.passed
            else "Automatisk kvalitetskontroll: underkänd."
        ]
        result.extend(self.checks)
        result.extend(f"Varning: {warning}" for warning in self.warnings)
        return result


class _ReferenceParser(HTMLParser):
    def __init__(self, file_path: str) -> None:
        super().__init__(convert_charrefs=True)
        self.file_path = file_path
        self.references: list[HtmlReference] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        tag = tag.lower()

        if tag == "img":
            self.references.append(
                HtmlReference(
                    file_path=self.file_path,
                    tag="img",
                    attribute="src",
                    value=values.get("src", "").strip(),
                    alt=values.get("alt", "").strip(),
                    classes=values.get("class", "").strip(),
                    element_id=values.get("id", "").strip(),
                )
            )

        if tag == "script" and values.get("src"):
            self.references.append(
                HtmlReference(
                    file_path=self.file_path,
                    tag="script",
                    attribute="src",
                    value=values["src"].strip(),
                )
            )

        if tag == "link" and values.get("href"):
            rel = values.get("rel", "").lower()
            if "stylesheet" in rel:
                self.references.append(
                    HtmlReference(
                        file_path=self.file_path,
                        tag="link",
                        attribute="href",
                        value=values["href"].strip(),
                    )
                )


def _html_files(files: dict[str, str]) -> list[tuple[str, str]]:
    return [
        (path, content)
        for path, content in files.items()
        if path.lower().endswith((".html", ".htm"))
    ]


def _parse_references(files: dict[str, str]) -> list[HtmlReference]:
    references: list[HtmlReference] = []
    for path, content in _html_files(files):
        parser = _ReferenceParser(path)
        try:
            parser.feed(content)
        except Exception:
            # HTMLParser is forgiving, but malformed input should not crash QA.
            pass
        references.extend(parser.references)
    return references


def _css_image_references(files: dict[str, str]) -> list[HtmlReference]:
    references: list[HtmlReference] = []
    pattern = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)

    for path, content in files.items():
        if not path.lower().endswith((".css", ".html", ".htm")):
            continue

        for match in pattern.finditer(content):
            value = match.group(2).strip()
            if not value or value.lower().startswith("data:"):
                continue
            references.append(
                HtmlReference(
                    file_path=path,
                    tag="css-image",
                    attribute="url",
                    value=value,
                )
            )

    return references


def _is_external(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"}


def _is_ignored_reference(value: str) -> bool:
    lowered = value.lower().strip()
    return (
        not lowered
        or lowered.startswith("data:")
        or lowered.startswith("mailto:")
        or lowered.startswith("tel:")
        or lowered.startswith("javascript:")
        or lowered.startswith("#")
    )


def _resolve_local_path(source_file: str, value: str) -> str | None:
    clean = unquote(value.split("#", 1)[0].split("?", 1)[0]).strip()
    if not clean or clean.startswith("/"):
        clean = clean.lstrip("/")
        return str(PurePosixPath(clean)) if clean else None

    base = PurePosixPath(source_file).parent
    parts: list[str] = []
    for part in (base / clean).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts) if parts else None


def _expected_image_count(requirements: str) -> int:
    text = requirements.lower()
    expected = 0

    # Explicit expressions such as "4 bilder" or "fyra bilder".
    for match in re.finditer(
        r"\b(\d{1,2}|en|ett|två|tre|fyra|fem|sex|sju|åtta|nio|tio)\s+"
        r"(?:st(?:ycken)?\s+)?(?:stock)?bilder?\b",
        text,
        flags=re.IGNORECASE,
    ):
        raw = match.group(1).lower()
        number = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw, 0)
        expected = max(expected, number)

    # Numbered requirements such as "1. hero, 2. Kaffe, 3. Fika, 4. Lunch".
    numbered = [
        int(value)
        for value in re.findall(r"(?:^|\n)\s*(\d{1,2})[.)]\s+", text)
    ]
    if numbered:
        expected = max(expected, max(numbered))

    # Repeated "en bild ..." bullets are common in refinement prompts.
    single_requests = len(
        re.findall(
            r"(?:^|\n|[-•])\s*(?:en|1)\s+(?:stor\s+)?(?:hero[- ]?)?bild\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    expected = max(expected, single_requests)

    return expected


def _hero_requested(requirements: str) -> bool:
    lowered = requirements.lower()
    return bool(
        re.search(r"\bhero[- ]?(?:bild|image)\b", lowered)
        or "stor hero" in lowered
    )


def _has_hero_image(files: dict[str, str], references: Iterable[HtmlReference]) -> bool:
    for ref in references:
        if ref.tag != "img":
            continue
        marker = " ".join(
            [ref.classes, ref.element_id, ref.alt, ref.value]
        ).lower()
        if "hero" in marker:
            return True

    for path, content in _html_files(files):
        lowered = content.lower()
        if "hero" in lowered and (
            "background-image" in lowered
            or "<picture" in lowered
            or "<img" in lowered
        ):
            return True

    for path, content in files.items():
        if path.lower().endswith(".css"):
            lowered = content.lower()
            if "hero" in lowered and "background-image" in lowered:
                return True

    return False


async def _check_external_image(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[str, str | None]:
    try:
        async with client.stream(
            "GET",
            url,
            headers={
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                "User-Agent": "WebbBuilder-Studio-v2-QualityCheck/1.0",
                "Range": "bytes=0-2047",
            },
        ) as response:
            if response.status_code >= 400:
                return url, f"HTTP {response.status_code}"

            content_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )

            if not content_type.startswith("image/"):
                return url, (
                    "fel Content-Type "
                    + (content_type or "saknas")
                )

            return url, None

    except httpx.TimeoutException:
        return url, "timeout"
    except httpx.RequestError as exc:
        return url, f"nätverksfel: {type(exc).__name__}"


async def validate_project_files(
    files: dict[str, str],
    requirements: str = "",
) -> QualityReport:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []

    html_entries = _html_files(files)
    if not html_entries:
        errors.append("Projektet saknar index.html eller annan HTML-fil för preview.")
        return QualityReport(
            passed=False,
            errors=errors,
            warnings=warnings,
            checks=checks,
        )

    if not any(
        path.lower().endswith(("index.html", "preview.html"))
        for path, _ in html_entries
    ):
        warnings.append(
            "Ingen index.html eller preview.html hittades; preview kan bli svårare att visa."
        )

    references = _parse_references(files)
    css_image_refs = _css_image_references(files)
    all_references = references + css_image_refs
    image_refs = [ref for ref in references if ref.tag == "img"]
    visual_refs = image_refs + css_image_refs
    external_images = [
        ref for ref in visual_refs if _is_external(ref.value)
    ]

    # HTML sanity and obvious placeholders.
    for path, content in html_entries:
        lowered = content.lower()
        if "<html" not in lowered or "<body" not in lowered:
            warnings.append(f"{path} saknar komplett <html>/<body>-struktur.")

        if "lorem ipsum" in lowered:
            errors.append(f"{path} innehåller Lorem ipsum-platshållartext.")

        if "example.com" in lowered:
            errors.append(f"{path} innehåller example.com som platshållare.")

    # Local references must point to generated files.
    for ref in all_references:
        value = ref.value.strip()
        if not value:
            if ref.tag == "img":
                errors.append(f"{ref.file_path} innehåller en bild med tom src.")
            continue

        if _is_external(value) or _is_ignored_reference(value):
            continue

        local_path = _resolve_local_path(ref.file_path, value)
        if local_path and local_path not in files:
            errors.append(
                f"{ref.file_path}: {ref.tag} refererar till saknad fil {local_path}."
            )
            continue

        if ref.tag in {"img", "css-image"} and local_path in files:
            suffix = PurePosixPath(local_path).suffix.lower()
            if suffix in _IMAGE_EXTENSIONS and suffix != ".svg":
                errors.append(
                    f"{ref.file_path}: {local_path} är en binär bildtyp ({suffix}), "
                    "men WebbBuilder-manifestet lagrar bara textfiler. Använd en fungerande "
                    "extern HTTPS-bild eller SVG tills binär asset-lagring stöds."
                )
            elif suffix == ".svg" and "<svg" not in files[local_path].lower():
                errors.append(
                    f"{ref.file_path}: {local_path} har .svg men innehåller inte giltig SVG-markup."
                )

    # Image accessibility basics.
    for ref in image_refs:
        if not ref.alt:
            warnings.append(
                f"{ref.file_path}: bild {ref.value or '(tom src)'} saknar alt-text."
            )

    expected_images = _expected_image_count(requirements)
    if expected_images and len(visual_refs) < expected_images:
        errors.append(
            f"Kravet verkar efterfråga minst {expected_images} bilder, "
            f"men projektet innehåller bara {len(visual_refs)} bildreferens(er)."
        )

    if _hero_requested(requirements) and not _has_hero_image(files, references):
        errors.append(
            "Kravet efterfrågar en hero-bild, men ingen tydlig hero-bild hittades i HTML/CSS."
        )

    # Validate external image URLs instead of trusting the AI's claim.
    unique_urls = list(dict.fromkeys(ref.value for ref in external_images if ref.value))
    if len(unique_urls) > QUALITY_MAX_EXTERNAL_IMAGES:
        errors.append(
            f"Projektet använder {len(unique_urls)} externa bilder; "
            f"max {QUALITY_MAX_EXTERNAL_IMAGES} kan kvalitetskontrolleras automatiskt."
        )
        unique_urls = unique_urls[:QUALITY_MAX_EXTERNAL_IMAGES]

    if unique_urls:
        timeout = httpx.Timeout(
            connect=float(QUALITY_HTTP_TIMEOUT),
            read=float(QUALITY_HTTP_TIMEOUT),
            write=float(QUALITY_HTTP_TIMEOUT),
            pool=float(QUALITY_HTTP_TIMEOUT),
        )
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            limits=limits,
        ) as client:
            results = await asyncio.gather(
                *(_check_external_image(client, url) for url in unique_urls)
            )

        for url, problem in results:
            if problem:
                short_url = url if len(url) <= 150 else url[:147] + "..."
                errors.append(
                    f"Extern bild fungerar inte: {short_url} ({problem})."
                )

    checks.append(
        f"Kontrollerade {len(files)} filer och {len(html_entries)} HTML-fil(er)."
    )
    checks.append(
        f"Kontrollerade {len(visual_refs)} bildreferens(er), "
        f"varav {len(unique_urls)} externa URL:er."
    )
    if expected_images:
        checks.append(
            f"Bildkrav: minst {expected_images}; hittade {len(visual_refs)}."
        )

    return QualityReport(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        checks=checks,
        image_count=len(visual_refs),
        external_image_count=len(unique_urls),
    )

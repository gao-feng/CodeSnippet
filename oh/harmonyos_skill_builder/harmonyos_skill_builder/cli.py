from __future__ import annotations

import argparse
import html
import json
import os
import posixpath
import re
import shutil
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen


DEFAULT_START_URL = "https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/arkts"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; harmonyos-skill-builder/0.1; "
    "+https://developer.huawei.com/consumer/cn/doc/harmonyos-guides)"
)
DEFAULT_LANGUAGE = "cn"
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_MAX_INPUT_CHARS = 60000
BLOCKED_EXTENSIONS = {
    ".7z",
    ".avi",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".rar",
    ".svg",
    ".webp",
    ".zip",
}


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    markdown: str
    links: tuple[str, ...]


@dataclass(frozen=True)
class SkillSummary:
    description: str
    title: str
    overview: str
    workflow: tuple[str, ...]
    focus_topics: tuple[str, ...]


@dataclass(frozen=True)
class LlmConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float
    max_input_chars: int


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {name.lower(): value for name, value in attrs}
        href = attr_map.get("href")
        if href:
            self.links.append(urljoin(self.base_url, href))


class MarkdownExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.skip_stack: list[str] = []
        self.href_stack: list[str | None] = []
        self.in_title = False
        self.in_pre = False
        self.list_depth = 0
        self.pending_heading: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_stack.append(tag)
            return
        if self.skip_stack:
            return

        attr_map = {name.lower(): value for name, value in attrs}
        if tag == "title":
            self.in_title = True
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._blank_line()
            self.pending_heading = int(tag[1])
            self.parts.append("#" * min(self.pending_heading, 6) + " ")
        elif tag in {"p", "section", "article", "main", "div"}:
            self._blank_line()
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "pre":
            self._blank_line()
            self.parts.append("```\n")
            self.in_pre = True
        elif tag == "code" and not self.in_pre:
            self.parts.append("`")
        elif tag in {"ul", "ol"}:
            self.list_depth += 1
            self._blank_line()
        elif tag == "li":
            self._blank_line()
            indent = "  " * max(self.list_depth - 1, 0)
            self.parts.append(f"{indent}- ")
        elif tag == "a":
            href = attr_map.get("href")
            self.href_stack.append(urljoin(self.base_url, href) if href else None)
            if href:
                self.parts.append("[")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("_")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_stack:
            if self.skip_stack[-1] == tag:
                self.skip_stack.pop()
            return

        if tag == "title":
            self.in_title = False
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.pending_heading = None
            self._blank_line()
        elif tag in {"p", "section", "article", "main", "div", "li"}:
            self._blank_line()
        elif tag == "pre":
            if not self.parts or not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            self.parts.append("```\n")
            self.in_pre = False
            self._blank_line()
        elif tag == "code" and not self.in_pre:
            self.parts.append("`")
        elif tag in {"ul", "ol"}:
            self.list_depth = max(self.list_depth - 1, 0)
            self._blank_line()
        elif tag == "a":
            href = self.href_stack.pop() if self.href_stack else None
            if href:
                self.parts.append(f"]({href})")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("_")

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.in_pre:
            self.parts.append(html.unescape(data))
            return

        text = re.sub(r"\s+", " ", html.unescape(data))
        if text.strip():
            self.parts.append(text)

    def title(self) -> str:
        return clean_inline_text(" ".join(self.title_parts))

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n".join(line.rstrip() for line in text.splitlines())
        return text.strip() + "\n"

    def _blank_line(self) -> None:
        if not self.parts:
            return
        current = "".join(self.parts[-3:])
        if current.endswith("\n\n"):
            return
        if current.endswith("\n"):
            self.parts.append("\n")
        else:
            self.parts.append("\n\n")


def info(message: str) -> None:
    print(f"[info] {message}")


def warn(message: str) -> None:
    print(f"[warn] {message}", file=sys.stderr)


def clean_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url: str) -> str:
    url = urldefrag(url)[0]
    parsed = urlparse(url)
    path = posixpath.normpath(parsed.path or "/")
    if parsed.path.endswith("/") and not path.endswith("/"):
        path += "/"
    return parsed._replace(path=path, query="").geturl()


def path_has_blocked_extension(url: str) -> bool:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix in BLOCKED_EXTENSIONS


def is_in_scope(url: str, start_url: str, scope_prefix: str) -> bool:
    parsed = urlparse(url)
    start = urlparse(start_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc != start.netloc:
        return False
    if path_has_blocked_extension(url):
        return False
    return parsed.path.startswith(scope_prefix)


def fetch_text(url: str, timeout: float, user_agent: str) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_charset() or "utf-8"
            data = response.read()
    except HTTPError as exc:
        raise BuildError(f"HTTP {exc.code} while fetching {url}") from exc
    except URLError as exc:
        raise BuildError(f"Unable to fetch {url}: {exc.reason}") from exc
    return data.decode(content_type, errors="replace")


def post_json(url: str, payload: dict, timeout: float, user_agent: str) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"User-Agent": user_agent, "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            text = response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise BuildError(f"HTTP {exc.code} while posting {url}") from exc
    except URLError as exc:
        raise BuildError(f"Unable to post {url}: {exc.reason}") from exc
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BuildError(f"Invalid JSON from {url}: {text[:200]}") from exc
    if str(result.get("code")) not in {"0", "00000"}:
        raise BuildError(f"API error from {url}: {result.get('code')} {result.get('message')}")
    return result


def post_openai_chat_completion(config: LlmConfig, messages: list[dict[str, str]]) -> str:
    endpoint = openai_chat_completions_url(config.base_url)
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.2,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=config.timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            text = response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise BuildError(f"LLM API HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise BuildError(f"Unable to call LLM API: {exc.reason}") from exc

    try:
        result = json.loads(text)
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise BuildError(f"Unexpected LLM API response: {text[:500]}") from exc
    if not isinstance(content, str) or not content.strip():
        raise BuildError("LLM API returned an empty summary.")
    return content


def openai_chat_completions_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def parse_page(url: str, html_text: str) -> Page:
    markdown_parser = MarkdownExtractor(url)
    markdown_parser.feed(html_text)
    markdown_parser.close()

    link_parser = LinkExtractor(url)
    link_parser.feed(html_text)
    link_parser.close()

    title = markdown_parser.title() or infer_title_from_markdown(markdown_parser.markdown()) or url
    links = tuple(dict.fromkeys(normalize_url(link) for link in link_parser.links))
    return Page(url=url, title=title, markdown=markdown_parser.markdown(), links=links)


def infer_title_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("#"):
            return clean_inline_text(line.lstrip("# "))
    return ""


def crawl(
    start_url: str,
    scope_prefix: str,
    max_pages: int | None,
    delay: float,
    timeout: float,
    user_agent: str,
) -> list[Page]:
    start_url = normalize_url(start_url)
    queue: deque[str] = deque([start_url])
    seen: set[str] = set()
    pages: list[Page] = []

    while queue and (max_pages is None or len(pages) < max_pages):
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)

        if not is_in_scope(url, start_url, scope_prefix):
            continue

        info(f"download {progress_count(len(pages) + 1, max_pages)}: {url}")
        try:
            page = parse_page(url, fetch_text(url, timeout=timeout, user_agent=user_agent))
        except BuildError as exc:
            warn(str(exc))
            continue

        if page.markdown.strip():
            pages.append(page)
        for link in page.links:
            if link not in seen and is_in_scope(link, start_url, scope_prefix):
                queue.append(link)

        if delay > 0:
            time.sleep(delay)

    return pages


def crawl_document_api(
    start_url: str,
    max_pages: int | None,
    delay: float,
    timeout: float,
    user_agent: str,
    language: str,
    show_hide: int,
) -> list[Page]:
    catalog_name, root_slug = catalog_and_root_slug(start_url)
    api_base = document_api_base(start_url)
    tree_url = f"{api_base}/getCatalogTree"
    doc_url = f"{api_base}/getDocumentById"
    tree = post_json(
        tree_url,
        {
            "language": language,
            "catalogName": catalog_name,
            "objectId": None,
            "showHide": show_hide,
        },
        timeout=timeout,
        user_agent=user_agent,
    )

    nodes = tree.get("value", {}).get("catalogTreeList") or []
    root = find_node_by_slug(nodes, root_slug)
    if root is None:
        raise BuildError(f"Could not find document node '{root_slug}' in catalog '{catalog_name}'.")

    pages: list[Page] = []
    failures: list[str] = []
    for node in flatten_nodes([root]):
        slug = node.get("relateDocument")
        if not slug:
            continue
        if max_pages is not None and len(pages) >= max_pages:
            break

        info(f"download {progress_count(len(pages) + 1, max_pages)}: {slug}")
        try:
            payload = post_json(
                doc_url,
                {
                    "language": language,
                    "objectId": slug,
                    "version": "",
                    "showHide": show_hide,
                    "catalogName": catalog_name,
                },
                timeout=timeout,
                user_agent=user_agent,
            )
        except BuildError as exc:
            failures.append(f"{slug}: {exc}")
            continue

        value = payload.get("value") or {}
        content = value.get("content") or {}
        html_text = content.get("content") or ""
        title = clean_inline_text(value.get("title") or node.get("nodeName") or slug)
        if not html_text.strip():
            failures.append(f"{slug}: empty document content")
            continue

        source_url = doc_url_for_slug(start_url, catalog_name, slug)
        page = parse_page(source_url, html_text)
        pages.append(Page(url=source_url, title=title or page.title, markdown=page.markdown, links=page.links))
        if delay > 0:
            time.sleep(delay)

    if failures:
        formatted = "\n".join(f"- {failure}" for failure in failures)
        raise BuildError(f"Failed to download all documents:\n{formatted}")

    return pages


def progress_count(current: int, max_pages: int | None) -> str:
    if max_pages is None:
        return str(current)
    return f"{current}/{max_pages}"


def catalog_and_root_slug(start_url: str) -> tuple[str, str]:
    parts = [part for part in urlparse(start_url).path.split("/") if part]
    try:
        doc_index = parts.index("doc")
    except ValueError as exc:
        raise BuildError(f"Start URL path must contain '/doc/<catalog>/<document>': {start_url}") from exc
    if len(parts) <= doc_index + 2:
        raise BuildError(f"Start URL path must contain both catalog and document slug: {start_url}")
    return parts[doc_index + 1], parts[doc_index + 2]


def document_api_base(start_url: str) -> str:
    parsed = urlparse(start_url)
    return f"{parsed.scheme}://{parsed.netloc}/consumer/cn/documentPortal"


def doc_url_for_slug(start_url: str, catalog_name: str, slug: str) -> str:
    parsed = urlparse(start_url)
    return f"{parsed.scheme}://{parsed.netloc}/consumer/cn/doc/{catalog_name}/{slug}"


def find_node_by_slug(nodes: list[dict], slug: str) -> dict | None:
    for node in nodes:
        if node.get("relateDocument") == slug:
            return node
        match = find_node_by_slug(node.get("children") or [], slug)
        if match is not None:
            return match
    return None


def flatten_nodes(nodes: list[dict]) -> Iterable[dict]:
    for node in nodes:
        yield node
        yield from flatten_nodes(node.get("children") or [])


def slugify(value: str, fallback: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", value)
    value = value.strip("-")
    return value[:80] or fallback


def skill_name_from_url(start_url: str) -> str:
    try:
        _, root_slug = catalog_and_root_slug(start_url)
    except BuildError:
        root_slug = Path(urlparse(start_url).path).stem or "harmonyos-docs"
    name = re.sub(r"[^0-9a-zA-Z]+", "-", root_slug.strip().lower()).strip("-")
    return name[:63] or "harmonyos-docs"


def default_scope_prefix(start_url: str) -> str:
    path = urlparse(start_url).path
    if not path:
        return "/"
    return path.rstrip("/") or "/"


def page_filename(page: Page, index: int) -> str:
    slug = slugify(page.title, fallback=f"page-{index:03d}")
    return f"{index:03d}-{slug}.md"


def summarize_skill(skill_name: str, pages: list[Page], start_url: str, config: LlmConfig) -> SkillSummary:
    corpus = build_summary_corpus(pages, max_chars=config.max_input_chars)
    messages = [
        {
            "role": "system",
            "content": (
                "You create concise Codex SKILL.md instructions from downloaded technical documentation. "
                "Use only the supplied source text. Return only one JSON object and no thinking, markdown, "
                "or explanatory text. The JSON object must have keys: description, title, "
                "overview, workflow, focus_topics. description must be one sentence under 220 characters. "
                "workflow must be 4 to 7 short imperative strings. focus_topics must be 3 to 8 short strings."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Skill name: {skill_name}\n"
                f"Source URL: {start_url}\n\n"
                "Summarize what this HarmonyOS documentation set is for and how Codex should use the "
                "downloaded reference files. Prefer English for SKILL.md content, but keep official API "
                "or product names unchanged.\n\n"
                f"{corpus}"
            ),
        },
    ]
    info(f"summarize SKILL.md with {config.model}")
    content = post_openai_chat_completion(config, messages)
    data = parse_json_object(content)
    return normalize_skill_summary(data, skill_name=skill_name, fallback_title=pages[0].title if pages else skill_name)


def build_summary_corpus(pages: list[Page], max_chars: int) -> str:
    if max_chars <= 0:
        raise BuildError("--llm-max-input-chars must be greater than 0.")
    if not pages:
        return ""

    per_page = max(1200, max_chars // max(len(pages), 1))
    parts: list[str] = []
    used = 0
    for index, page in enumerate(pages, start=1):
        header = f"## Page {index}: {page.title}\nURL: {page.url}\n\n"
        body_budget = max(400, min(per_page, max_chars - used - len(header)))
        if body_budget <= 0:
            break
        body = trim_text(page.markdown, body_budget)
        part = f"{header}{body}".strip()
        parts.append(part)
        used += len(part) + 2
        if used >= max_chars:
            break
    return "\n\n".join(parts)


def trim_text(value: str, max_chars: int) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    if max_chars <= 200:
        return value[:max_chars].rstrip()
    head = max_chars * 2 // 3
    tail = max_chars - head - 40
    return f"{value[:head].rstrip()}\n\n[... omitted ...]\n\n{value[-tail:].lstrip()}"


def parse_json_object(value: str) -> dict:
    text = extract_json_object_text(value)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        for candidate in iter_json_object_candidates(strip_llm_sideband_text(value)):
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
        raise BuildError(f"LLM summary was not valid JSON: {value[:500]}") from exc
    if not isinstance(data, dict):
        raise BuildError("LLM summary JSON must be an object.")
    return data


def extract_json_object_text(value: str) -> str:
    text = strip_llm_sideband_text(value).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    if start < 0:
        return text

    in_string = False
    escape = False
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def iter_json_object_candidates(value: str) -> Iterable[str]:
    text = value.strip()
    for start, char in enumerate(text):
        if char != "{":
            continue

        in_string = False
        escape = False
        depth = 0
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escape:
                    escape = False
                elif current == "\\":
                    escape = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : index + 1]
                    break


def strip_llm_sideband_text(value: str) -> str:
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", value, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"^\s*<think\b[^>]*>.*?(?=\{)", "", text, flags=re.IGNORECASE | re.DOTALL)
    return text


def normalize_skill_summary(data: dict, skill_name: str, fallback_title: str) -> SkillSummary:
    description = clean_inline_text(str(data.get("description") or ""))
    title = clean_inline_text(str(data.get("title") or fallback_title or skill_name))
    overview = str(data.get("overview") or "").strip()
    workflow = tuple(clean_inline_text(str(item)) for item in data.get("workflow") or [] if clean_inline_text(str(item)))
    focus_topics = tuple(
        clean_inline_text(str(item)) for item in data.get("focus_topics") or [] if clean_inline_text(str(item))
    )

    if not description:
        description = (
            f"Use when working with HarmonyOS documentation related to {title}; consult the bundled references "
            "before answering or editing code."
        )
    if not overview:
        overview = (
            "Use this skill for HarmonyOS work related to the generated documentation set. Prefer the bundled "
            "references before relying on memory."
        )
    if not workflow:
        workflow = default_workflow()

    return SkillSummary(
        description=description,
        title=title,
        overview=overview,
        workflow=workflow[:7],
        focus_topics=focus_topics[:8],
    )


def default_workflow() -> tuple[str, ...]:
    return (
        "Identify the HarmonyOS topic in the user request.",
        "Open `references/index.json` to find matching downloaded pages by title or URL.",
        "Read only the relevant files under `references/pages/`.",
        "Apply the documented HarmonyOS pattern, preserving the user's project conventions.",
        "Mention any uncertainty if the downloaded docs do not cover the requested API or behavior.",
    )


def yaml_double_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_skill_md(
    skill_name: str,
    pages: list[dict[str, str]],
    start_url: str,
    summary: SkillSummary | None = None,
) -> str:
    toc = "\n".join(f"- `{item['file']}`: {item['title']}" for item in pages)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    title = summary.title if summary else pages[0]["title"] if pages else skill_name
    description = (
        summary.description
        if summary
        else (
            f"Use when working with HarmonyOS documentation related to {title}. This skill provides local "
            "references generated from Huawei Developer HarmonyOS docs and tells Agent how to consult them "
            "before answering questions, editing code, or explaining APIs and development patterns."
        )
    )
    overview = (
        summary.overview
        if summary
        else (
            "Use this skill for HarmonyOS work related to the generated documentation set. Prefer the bundled "
            "references before relying on memory, especially for API behavior, version constraints, examples, "
            "configuration, lifecycle, permissions, and development patterns."
        )
    )
    workflow = summary.workflow if summary else default_workflow()
    workflow_lines = "\n".join(f"{index}. {step}" for index, step in enumerate(workflow, start=1))
    focus_section = ""
    if summary and summary.focus_topics:
        focus_items = "\n".join(f"- {item}" for item in summary.focus_topics)
        focus_section = f"\n## Focus Topics\n\n{focus_items}\n"
    return f"""---
name: {skill_name}
description: {yaml_double_quote(description)}
---

# HarmonyOS {title}

{overview}

## Workflow

{workflow_lines}
{focus_section}

## Local References

Generated from: {start_url}
Generated at: {generated_at}

{toc}
"""


def write_skill(
    output_dir: Path,
    skill_name: str,
    pages: list[Page],
    start_url: str,
    overwrite: bool,
    llm_config: LlmConfig | None = None,
) -> None:
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise BuildError(f"Output directory is not empty: {output_dir}. Use --overwrite to replace generated files.")
    if overwrite:
        skill_file = output_dir / "SKILL.md"
        references_dir = output_dir / "references"
        if skill_file.exists():
            skill_file.unlink()
        if references_dir.exists():
            shutil.rmtree(references_dir)

    references_dir = output_dir / "references"
    pages_dir = references_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    index_items: list[dict[str, str]] = []
    for index, page in enumerate(pages, start=1):
        filename = page_filename(page, index)
        rel_path = f"pages/{filename}"
        (pages_dir / filename).write_text(render_reference_page(page), encoding="utf-8")
        index_items.append({"title": page.title, "url": page.url, "file": rel_path})

    (references_dir / "index.json").write_text(
        json.dumps({"source": start_url, "pages": index_items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = summarize_skill(skill_name, pages, start_url, llm_config) if llm_config else None
    (output_dir / "SKILL.md").write_text(render_skill_md(skill_name, index_items, start_url, summary), encoding="utf-8")


def render_reference_page(page: Page) -> str:
    return f"""---
title: {page.title}
source: {page.url}
---

{page.markdown}
"""


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download Huawei HarmonyOS documentation and generate a skill folder.",
    )
    parser.add_argument("--start-url", default=DEFAULT_START_URL, help="HarmonyOS documentation entry URL.")
    parser.add_argument(
        "--source",
        choices=("api", "html"),
        default="api",
        help="Use Huawei document APIs or plain HTML crawling.",
    )
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="Document language for API crawling.")
    parser.add_argument("--show-hide", type=int, default=0, help="showHide value for Huawei document APIs.")
    parser.add_argument(
        "--scope-prefix",
        default=None,
        help="Only used by --source html. Crawl pages whose URL path starts with this prefix. Defaults to the start URL path.",
    )
    parser.add_argument("--output-dir", default=None, help="Generated skill directory. Defaults to build/<skill-name>.")
    parser.add_argument("--skill-name", default=None, help="Skill name written to SKILL.md. Defaults to the document slug.")
    parser.add_argument(
        "--max-pages",
        type=non_negative_int,
        default=0,
        help="Maximum number of pages to download. Use 0 to download every document under the start URL.",
    )
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between requests in seconds.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Request timeout in seconds.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent header.")
    parser.add_argument("--overwrite", action="store_true", help="Allow writing into a non-empty output directory.")
    parser.add_argument(
        "--summarize-skill",
        action="store_true",
        help="Use an OpenAI-compatible chat completions API to summarize downloaded pages into SKILL.md.",
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.environ.get("OPENAI_API_KEY"),
        help="API key for --summarize-skill. Defaults to OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.environ.get("OPENAI_BASE_URL", DEFAULT_LLM_BASE_URL),
        help=f"OpenAI-compatible API base URL. Defaults to OPENAI_BASE_URL or {DEFAULT_LLM_BASE_URL}.",
    )
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("OPENAI_MODEL", DEFAULT_LLM_MODEL),
        help=f"Model used by --summarize-skill. Defaults to OPENAI_MODEL or {DEFAULT_LLM_MODEL}.",
    )
    parser.add_argument(
        "--llm-max-input-chars",
        type=positive_int,
        default=DEFAULT_LLM_MAX_INPUT_CHARS,
        help="Maximum downloaded-document characters sent to the LLM summary request.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    skill_name = args.skill_name or skill_name_from_url(args.start_url)
    output_dir = Path(args.output_dir or f"build/{skill_name}")
    max_pages = None if args.max_pages == 0 else args.max_pages
    scope_prefix = args.scope_prefix or default_scope_prefix(args.start_url)

    try:
        if args.source == "api":
            pages = crawl_document_api(
                start_url=args.start_url,
                max_pages=max_pages,
                delay=args.delay,
                timeout=args.timeout,
                user_agent=args.user_agent,
                language=args.language,
                show_hide=args.show_hide,
            )
        else:
            pages = crawl(
                start_url=args.start_url,
                scope_prefix=scope_prefix,
                max_pages=max_pages,
                delay=args.delay,
                timeout=args.timeout,
                user_agent=args.user_agent,
            )
        if not pages:
            raise BuildError("No pages were downloaded. Check network access, URL scope, or site markup.")
        llm_config = None
        if args.summarize_skill:
            if not args.llm_api_key:
                raise BuildError("--summarize-skill requires --llm-api-key or OPENAI_API_KEY.")
            llm_config = LlmConfig(
                api_key=args.llm_api_key,
                base_url=args.llm_base_url,
                model=args.llm_model,
                timeout=args.timeout,
                max_input_chars=args.llm_max_input_chars,
            )
        write_skill(output_dir, skill_name, pages, args.start_url, overwrite=args.overwrite, llm_config=llm_config)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    info(f"generated {len(pages)} pages into {output_dir}")
    return 0

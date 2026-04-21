from __future__ import annotations

import argparse
import html
import json
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


def render_skill_md(skill_name: str, pages: list[dict[str, str]], start_url: str) -> str:
    toc = "\n".join(f"- `{item['file']}`: {item['title']}" for item in pages)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    title = pages[0]["title"] if pages else skill_name
    return f"""---
name: {skill_name}
description: Use when working with HarmonyOS documentation related to {title}. This skill provides local references generated from Huawei Developer HarmonyOS docs and tells Agent how to consult them before answering questions, editing code, or explaining APIs and development patterns.
---

# HarmonyOS {title}

Use this skill for HarmonyOS work related to the generated documentation set. Prefer the bundled references before relying on memory, especially for API behavior, version constraints, examples, configuration, lifecycle, permissions, and development patterns.

## Workflow

1. Identify the HarmonyOS topic in the user request.
2. Open `references/index.json` to find matching downloaded pages by title or URL.
3. Read only the relevant files under `references/pages/`.
4. Apply the documented HarmonyOS pattern, preserving the user's project conventions.
5. Mention any uncertainty if the downloaded docs do not cover the requested API or behavior.

## Local References

Generated from: {start_url}
Generated at: {generated_at}

{toc}
"""


def write_skill(output_dir: Path, skill_name: str, pages: list[Page], start_url: str, overwrite: bool) -> None:
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
    (output_dir / "SKILL.md").write_text(render_skill_md(skill_name, index_items, start_url), encoding="utf-8")


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
        write_skill(output_dir, skill_name, pages, args.start_url, overwrite=args.overwrite)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    info(f"generated {len(pages)} pages into {output_dir}")
    return 0

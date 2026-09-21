"""
Python port of the n8n "Code in JavaScript" node from the RAG - Sbot workflow.
Converts markdown/plain-text LLM output to sanitized HTML for webchat rendering.
"""
from __future__ import annotations

import re


def markdown_to_html(text: str) -> str:
    """Convert markdown or plain text to sanitized HTML."""
    html = str(text).strip()

    # Normalize literal escaped newlines
    html = html.replace("\\n", "\n")

    # If already HTML, just sanitize and return
    if re.search(r"<[a-z][\s\S]*>", html, re.IGNORECASE):
        return _sanitize(html)

    # --- Link conversion with placeholders to avoid double-processing ---
    links: list[str] = []

    def placeholder(i: int) -> str:
        return f"%%LINK{i}%%"

    # 1. Markdown links [text](url)
    def replace_md_link(m: re.Match) -> str:
        text_part, url = m.group(1), m.group(2)
        url = re.sub(r"[*_`]", "", url)  # strip markdown artifacts the LLM may embed in URLs
        links.append(f'<a href="{url}" target="_blank">{text_part}</a>')
        return placeholder(len(links) - 1)

    html = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", replace_md_link, html)

    # 2. Bare URLs with protocol
    def replace_bare_url(m: re.Match) -> str:
        url = m.group(1)
        links.append(f'<a href="{url}" target="_blank">{url}</a>')
        return placeholder(len(links) - 1)

    html = re.sub(r"(https?://[^\s<,;)]+)", replace_bare_url, html)

    # 3. Bare domain URLs without protocol (domain.tld/path)
    # Mirrors JS: (?<=\s|^) — only match after whitespace or start of line
    def replace_domain_url(m: re.Match) -> str:
        match_str = m.group(1)
        cleaned = re.sub(r"[.,;:!?]+$", "", match_str)
        links.append(f'<a href="https://{cleaned}" target="_blank">{cleaned}</a>')
        return placeholder(len(links) - 1) + match_str[len(cleaned):]

    html = re.sub(
        r"(?:^|(?<=\s))([a-z0-9][\w-]*\.(?:com|org|net|io|gov|edu)(?:\.br)?(?:/[^\s<,;)]*)?)",
        replace_domain_url,
        html,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    # 4. Bold **text**
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)

    # 5. Italic *text* (not adjacent to another *)
    html = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", html)

    # 6. List items (- or * at line start)
    html = re.sub(r"^[-*]\s+(.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
    # Wrap consecutive <li> blocks in <ul>
    html = re.sub(r"((?:<li>.*</li>\n?)+)", r"<ul>\1</ul>", html)

    # 7. Paragraphs — split on double newlines
    paragraphs = [p.strip() for p in re.split(r"\n\n+", html) if p.strip()]
    html_parts: list[str] = []
    for p in paragraphs:
        if re.match(r"^<(ul|ol|h[1-6]|blockquote)", p, re.IGNORECASE):
            html_parts.append(p)
        else:
            inner = p.replace("\n", "<br>")
            html_parts.append(f"<p>{inner}</p>")
    html = "\n".join(html_parts)

    # 8. Restore link placeholders
    for i, link_tag in enumerate(links):
        html = html.replace(placeholder(i), link_tag)

    return _sanitize(html)


def _sanitize(html: str) -> str:
    """Remove potentially dangerous HTML."""
    # Remove executable tags and their content
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.IGNORECASE)
    # Remove dangerous embed/frame tags entirely
    html = re.sub(r"<(?:iframe|object|embed|form)[^>]*>[\s\S]*?</(?:iframe|object|embed|form)>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<(?:iframe|object|embed|form)[^>]*/?>", "", html, flags=re.IGNORECASE)
    # Remove inline event handlers (both quote styles)
    html = re.sub(r'\bon\w+\s*=\s*(?:"[^"]*"|\'[^\']*\')', "", html, flags=re.IGNORECASE)
    # Strip javascript: and data: from href/src attributes
    html = re.sub(r'(href|src)\s*=\s*"(?:javascript|data):[^"]*"', r'\1="#"', html, flags=re.IGNORECASE)
    html = re.sub(r"(href|src)\s*=\s*'(?:javascript|data):[^']*'", r"\1='#'", html, flags=re.IGNORECASE)
    # Remove structural tags
    html = re.sub(r"</?(?:html|head|body)[^>]*>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()

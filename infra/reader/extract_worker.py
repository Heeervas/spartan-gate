"""Fixed stdin/stdout converter. No URLs are opened and no scripts executed."""

import json
import re
import resource
import socket
import sys
from uuid import uuid4
from urllib.parse import urljoin, urlsplit

MAX_TEXT = 100_000


def deny_network(*_args, **_kwargs):
    raise RuntimeError("Converter networking disabled")


def contain():
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    # Virtual address ceiling is distinct from Compose's 128-MiB RSS/cgroup
    # limit; Python 3.14 reserves arenas that alone exceed 192 MiB.
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network
    def audit(event, _args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto"}:
            deny_network()
    sys.addaudithook(audit)


def fence(text):
    runs = re.findall(r"`+", text)
    marker = "`" * max(3, max(map(len, runs), default=0) + 1)
    return f"{marker}\n{text}\n{marker}"


def github_content(tree, url):
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.hostname != "github.com" or len(parts) < 2:
        return None
    if len(parts) > 3 and parts[2] == "blob":
        return github_source(tree)
    if len(parts) == 2 or (len(parts) > 3 and parts[2] == "tree"):
        return github_listing(tree, url, "/" + "/".join(parts[:2]) + "/")
    return None


def github_source(tree):
    for script in tree.xpath('//script[@type="application/json"]'):
        try:
            payload = json.loads(script.text or "").get("payload", {})
        except (ValueError, AttributeError):
            continue
        if not isinstance(payload, dict):
            continue
        # Current advertised route key (the dot is literal), then legacy data.
        for key in ("codeViewBlobLayoutRoute.StyledBlob", "codeViewBlob"):
            blob = payload.get(key)
            lines = blob.get("rawLines") if isinstance(blob, dict) else None
            if isinstance(lines, list) and all(isinstance(line, str) for line in lines):
                return "\n".join(lines), "github-source"
    lines = tree.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " blob-code ")]')
    if lines:
        return "\n".join("".join(line.itertext()) for line in lines), "github-source"
    return None


def page_link(url, href):
    try:
        target = urljoin(url, href)
        parsed = urlsplit(target)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return target
    except ValueError:
        pass
    return ""


def github_listing(tree, url, prefix):
    links = {}
    for anchor in tree.xpath('//a[@href]'):
        href = page_link(url, anchor.get("href"))
        target = urlsplit(href)
        if target.hostname == "github.com" and target.path.startswith((prefix + "tree/", prefix + "blob/")):
            label = " ".join("".join(anchor.itertext()).split()) or target.path
            # Angle-bracket destinations retain spaces/parentheses safely.
            links[href] = label.replace("[", "\\[").replace("]", "\\]")
    if links:
        return "# Repository files\n\n" + "\n".join(f"- [{label}](<{href}>)" for href, label in links.items()), "github-listing"
    return None


def convert(content, url):
    from lxml import etree, html
    import trafilatura

    class DenyResources(etree.Resolver):
        def resolve(self, _url, _public_id, _context):
            raise ValueError("External resources disabled")

    parser = html.HTMLParser(no_network=True, recover=True, huge_tree=False)
    parser.resolvers.add(DenyResources())
    # Transport already decoded this text. Remove only its leading XML
    # declaration; re-encoding under an obsolete declaration corrupts Unicode.
    content = re.sub(r"\A\ufeff?\s*<\?xml\s[^?]*\?>", "", content, count=1)
    tree = html.fromstring(content, parser=parser)
    shells = tree.xpath('//*[@id="root" or @id="app" or @id="__next"]')
    if shells and tree.xpath('//script') and not tree.xpath('//article|//main//p|//pre|//table'):
        visible = " ".join(tree.xpath('//body//text()[not(ancestor::script) and not(ancestor::style) and not(ancestor::noscript)]'))
        if len(visible.strip()) < 200:
            return "", "needs_render"
    special = github_content(tree, url)
    if special:
        return special
    # Ignore page-controlled <base>; resolve links only against the fetched URL.
    for anchor in tree.xpath('//a[@href]'):
        target = page_link(url, anchor.get("href"))
        if target:
            anchor.set("href", target)
        else:
            anchor.attrib.pop("href", None)
    # Trafilatura normalizes code whitespace. Preserve exact preformatted text
    # through main-content selection, then restore bounded fenced source.
    code = {}
    nonce = uuid4().hex
    for index, pre in enumerate(tree.xpath('//pre')):
        token = f"SPARTANCODE{nonce}BLOCK{index}END"
        code[token] = fence("".join(pre.itertext()))
        pre.tag = "p"
        pre.clear(keep_tail=True)
        pre.text = token
    text = trafilatura.extract(tree, url=url, output_format="markdown",
                               include_links=True, include_tables=True,
                               include_comments=False, include_formatting=True,
                               with_metadata=False, fast=True)
    if not text:
        # A tiny static heading is useful; a script-only app shell is not.
        headings = tree.xpath('//h1|//h2')
        text = "\n\n".join("# " + "".join(h.itertext()).strip() for h in headings)
    if not text or not text.strip():
        return "", "needs_render"
    for token, source in code.items():
        text = text.replace(token, source)
    return text, "trafilatura-markdown"


def main():
    contain()
    try:
        data = json.loads(sys.stdin.buffer.read(16 * 1024 * 1024 + 1))
        text, method = convert(data["html"], data["url"])
        if method == "needs_render":
            result = {"error": "needs_render"}
        else:
            result = {"text": text[:MAX_TEXT], "method": method, "truncated": len(text) > MAX_TEXT}
    except Exception:
        result = {"error": "extraction_failed"}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()

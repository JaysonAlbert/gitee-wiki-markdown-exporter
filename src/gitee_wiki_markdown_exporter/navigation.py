"""Map observed Wiki page URLs and managed Markdown paths to stable page identities."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

PageIdentity = tuple[str, int]


def page_source_url(base_url: str, tenant: str, identity: PageIdentity) -> str | None:
    if not tenant:
        return None
    parsed = urlsplit(base_url)
    origin = urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], "", "", ""))
    space, page_id = identity
    return f"{origin}/wiki/{quote(tenant, safe='')}/space/{quote(space, safe='')}/doc/{page_id}"


def remote_page_identity(destination: str, base_url: str, tenant: str) -> PageIdentity | None:
    if not tenant:
        return None
    try:
        parsed = urlsplit(urljoin(base_url + "/", destination))
        base = urlsplit(base_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        base_port = base.port or (443 if base.scheme == "https" else 80)
        if (parsed.scheme, parsed.hostname, port) != (base.scheme, base.hostname, base_port):
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        match = re.fullmatch(r"/wiki/([^/]+)/space/([^/]+)/doc/([0-9]+)", parsed.path)
        if match is None or unquote(match[1]) != tenant:
            return None
        return unquote(match[2]), int(match[3])
    except ValueError:
        return None


def local_page_identity(
    destination: str, source: Path, paths: Mapping[Path, PageIdentity]
) -> PageIdentity | None:
    try:
        parsed = urlsplit(destination)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("/"):
        return None
    relative = unquote(parsed.path)
    if "\\" in relative:
        return None
    path = Path(posixpath.normpath(posixpath.join(source.parent.as_posix(), relative)))
    return paths.get(path)

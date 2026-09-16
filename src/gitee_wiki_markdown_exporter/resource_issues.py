"""Credential-free resource failure facts for automation and human recovery."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceIssue:
    page_id: int
    resource_kind: str
    reference: str
    code: str
    retryable: bool = False
    resource_id: int | None = None
    resource_key: str | None = None
    http_status: int | None = None
    limit_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "pageId": self.page_id,
            "resourceKind": self.resource_kind,
            "reference": self.reference,
            "code": self.code,
            "retryable": self.retryable,
        }
        for key, value in (
            ("resourceId", self.resource_id),
            ("resourceKey", self.resource_key),
            ("httpStatus", self.http_status),
            ("limitBytes", self.limit_bytes),
        ):
            if value is not None:
                result[key] = value
        return result


def resource_issue(
    error: Exception | str,
    *,
    page_id: int,
    resource_kind: str,
    reference: str,
    resource_id: int | None = None,
    resource_key: str | None = None,
    limit_bytes: int | None = None,
) -> ResourceIssue:
    """Classify known sanitized errors without copying messages or remote values."""
    message = str(error)
    status = re.search(r"\bHTTP (\d{3})\b", message)
    http_status = int(status[1]) if status else None
    retryable = False
    if http_status is not None:
        code = "http_error"
        retryable = http_status in {408, 429, 500, 502, 503, 504}
    elif "attachment_too_large" in message:
        code = "size_limit"
    elif "invalid_attachment_url" in message:
        code = "invalid_url"
    elif "outside the configured Gitee host" in message:
        code = "outside_origin"
    elif "invalid_image_payload" in message:
        code = next(
            (
                c
                for c in (
                    "html_response",
                    "image_type_mismatch",
                    "unsafe_svg",
                    "invalid_image_container",
                )
                if c in message
            ),
            "invalid_image_container",
        )
    elif any(
        c in message
        for c in ("Timeout", "ConnectError", "ReadError", "WriteError", "RemoteProtocolError")
    ):
        code, retryable = "network_error", True
    elif resource_kind == "diagram":
        code = "diagram_error"
    else:
        code = "resource_error"
    return ResourceIssue(
        page_id,
        resource_kind,
        reference,
        code,
        retryable,
        resource_id,
        resource_key,
        http_status,
        limit_bytes if code == "size_limit" else None,
    )

"""Forge client — GitHub and GitLab pull/merge-request creation (#7).

Pure payload builders are split out so they're unit-testable without network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from daedalus.core.settings import get_settings


class ForgeError(RuntimeError):
    """Forge is unconfigured or the API call failed."""


@dataclass
class PullRequest:
    number: int | None
    url: str
    raw: dict[str, Any]


def forge_enabled() -> bool:
    s = get_settings()
    return bool(s.forge_provider and s.forge_provider != "none" and s.forge_token and s.forge_repo)


def build_pr_payload(
    provider: str, *, head: str, base: str, title: str, body: str
) -> dict[str, Any]:
    """Provider-shaped request body for opening a PR/MR."""
    if provider == "github":
        return {"title": title, "head": head, "base": base, "body": body}
    if provider == "gitlab":
        return {
            "source_branch": head,
            "target_branch": base,
            "title": title,
            "description": body,
        }
    raise ForgeError(f"unsupported forge provider: {provider}")


def _endpoint(provider: str, api_base: str, repo: str) -> str:
    if provider == "github":
        return f"{api_base.rstrip('/')}/repos/{repo}/pulls"
    if provider == "gitlab":
        # repo must be the URL-encoded project path or numeric id.
        return f"{api_base.rstrip('/')}/projects/{repo}/merge_requests"
    raise ForgeError(f"unsupported forge provider: {provider}")


def _headers(provider: str, token: str) -> dict[str, str]:
    if provider == "github":
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    if provider == "gitlab":
        return {"PRIVATE-TOKEN": token}
    raise ForgeError(f"unsupported forge provider: {provider}")


def _parse_pr(provider: str, data: dict[str, Any]) -> PullRequest:
    if provider == "github":
        return PullRequest(number=data.get("number"), url=data.get("html_url", ""), raw=data)
    return PullRequest(
        number=data.get("iid"), url=(data.get("web_url") or ""), raw=data
    )


async def open_pull_request(*, head: str, base: str, title: str, body: str) -> PullRequest:
    """Open a PR/MR for `head` into `base`. Raises ForgeError if disabled/failed."""
    s = get_settings()
    if not forge_enabled():
        raise ForgeError("forge integration is not configured")
    provider = s.forge_provider
    payload = build_pr_payload(provider, head=head, base=base, title=title, body=body)
    url = _endpoint(provider, s.forge_api_base, s.forge_repo)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload, headers=_headers(provider, s.forge_token))
    except httpx.HTTPError as exc:
        raise ForgeError(f"forge request failed: {exc}") from exc
    if resp.status_code >= 300:
        raise ForgeError(f"forge returned {resp.status_code}: {resp.text[:300]}")
    return _parse_pr(provider, resp.json())

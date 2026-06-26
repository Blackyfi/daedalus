"""Optional VCS-forge integration (GitHub/GitLab) — IMPROVEMENTS #7.

Off by default to preserve the air-gapped posture. When ``FORGE_PROVIDER`` and
a token are configured, Daedalus can open a pull/merge request for a merge
batch's integration branch instead of (or in addition to) shipping locally.
"""
from daedalus.forge.client import (
    ForgeError,
    PullRequest,
    build_pr_payload,
    forge_enabled,
    open_pull_request,
)

__all__ = [
    "ForgeError",
    "PullRequest",
    "build_pr_payload",
    "forge_enabled",
    "open_pull_request",
]

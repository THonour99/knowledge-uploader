# ruff: noqa: PTH108, PTH110, PTH112, PTH113, PTH114, PTH116, PTH117, PTH118, PTH123
"""Consume one-time claims issued by the acceptance launcher."""

from __future__ import annotations

import os
import sys
from typing import Final

CLAIM_TOKEN_ENV: Final = "KNOWLEDGE_UPLOADER_ACCEPTANCE_TOKEN"
CLAIM_MARKER_ENV: Final = "KNOWLEDGE_UPLOADER_ACCEPTANCE_MARKER"
CLAIM_RUNTIME_ENV: Final = "KNOWLEDGE_UPLOADER_ACCEPTANCE_RUNTIME"
CLAIM_FILENAME: Final = ".knowledge-uploader-acceptance-launch"
CHILD_PYCACHE_NAME: Final = "pycache"


class AcceptanceEntryError(RuntimeError):
    """Raised when an acceptance child lacks a valid launcher claim."""


def _is_within(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath((parent, child)) == parent
    except ValueError:
        return False


def runtime_isolation_error(repo_root: str) -> str | None:
    """Return a fail-closed error when the current child is not isolated."""
    if sys.flags.isolated != 1:
        return "Python isolated mode (-I) is required"
    if sys.flags.no_site != 1:
        return "Python no-site mode (-S) is required before site initialization"
    if sys.flags.utf8_mode != 1:
        return "Python UTF-8 mode (-X utf8) is required"
    raw_prefix = sys.pycache_prefix
    if raw_prefix is None or not os.path.isabs(raw_prefix):
        return "an absolute launcher-controlled -X pycache_prefix is required"
    repository = os.path.realpath(repo_root)
    prefix = os.path.realpath(raw_prefix)
    if _is_within(repository, prefix):
        return "pycache_prefix must be outside the repository"
    if os.path.islink(raw_prefix) or (os.path.exists(raw_prefix) and not os.path.isdir(raw_prefix)):
        return "pycache_prefix must be a non-symlink directory"
    return None


def consume_launcher_claim(repo_root: str) -> None:
    """Validate and immediately destroy a one-time launcher claim."""
    token = os.environ.pop(CLAIM_TOKEN_ENV, None)
    raw_marker = os.environ.pop(CLAIM_MARKER_ENV, None)
    raw_runtime = os.environ.pop(CLAIM_RUNTIME_ENV, None)

    isolation_error = runtime_isolation_error(repo_root)
    if isolation_error is not None:
        raise AcceptanceEntryError(isolation_error)
    if token is None or raw_marker is None or raw_runtime is None:
        raise AcceptanceEntryError("a trusted acceptance launcher is required")
    if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
        raise AcceptanceEntryError("launcher claim is invalid")
    if not os.path.isabs(raw_marker) or not os.path.isabs(raw_runtime):
        raise AcceptanceEntryError("launcher claim paths must be absolute")

    repository = os.path.realpath(repo_root)
    runtime = os.path.realpath(raw_runtime)
    marker = os.path.realpath(raw_marker)
    prefix = os.path.realpath(sys.pycache_prefix or "")
    expected_marker = os.path.join(runtime, CLAIM_FILENAME)
    expected_prefix = os.path.join(runtime, CHILD_PYCACHE_NAME)
    if _is_within(repository, runtime):
        raise AcceptanceEntryError("launcher runtime must be outside the repository")
    if (
        os.path.normcase(marker) != os.path.normcase(expected_marker)
        or os.path.normcase(prefix) != os.path.normcase(expected_prefix)
    ):
        raise AcceptanceEntryError("launcher claim paths do not match the child runtime")
    if os.path.islink(raw_runtime) or not os.path.isdir(raw_runtime):
        raise AcceptanceEntryError("launcher runtime is invalid")
    if os.path.islink(raw_marker) or not os.path.isfile(raw_marker):
        raise AcceptanceEntryError("launcher claim marker is invalid")
    try:
        marker_stat = os.stat(raw_marker, follow_symlinks=False)
        with open(raw_marker, "rb") as claim_file:
            marker_payload = claim_file.read(65)
    except OSError as error:
        raise AcceptanceEntryError("launcher claim marker cannot be read") from error
    if marker_stat.st_nlink != 1 or marker_payload != token.encode("ascii"):
        raise AcceptanceEntryError("launcher claim does not match")
    try:
        os.unlink(raw_marker)
    except OSError as error:
        raise AcceptanceEntryError("launcher claim marker cannot be destroyed") from error
    token = ""
    marker_payload = b""

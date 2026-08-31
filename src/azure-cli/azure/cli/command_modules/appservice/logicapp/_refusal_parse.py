# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
"""Prefix-tolerant refusal-payload consumer contract.

The load-bearing property of the refusal wire contract is not the exact byte
prefix; it is **machine-readability** — a caller must be able to distinguish
"the platform cannot do this" from "your command was wrong" without heuristics.

This contract is tolerant by construction, so that whichever prefix convention the
Azure CLI team prefers, the fallback costs nothing at the consumer end: an
``ERROR: `` prefix can be accepted without giving up the machine-readable property.

Tolerated variations
--------------------
- Leading whitespace on the line.
- Leading ``ERROR: `` (knack's default log level prefix for ``logger.error``).
- ANSI colour escape sequences wrapping the whole line (knack's coloured
  console format when ``AZURE_CORE_NO_COLOR`` is unset).
- Multi-line stderr: any line satisfying the above may carry the marker; the
  helper returns the first matching payload and refuses if more than one is
  present.

What is NOT tolerated (and rightly so)
--------------------------------------
- Missing marker line entirely (rung 4 case). The parser returns ``None`` — the
  caller must record this as a §6 LOSS explicitly rather than silently
  degrade.
- Multiple distinct marker lines on the same stderr. Ambiguity beats
  guessing.

This module has no runtime dependencies. It is intentionally usable from
scripts, ``bash``, ``pwsh`` or downstream Python without importing the CLI.
"""

import json
import re

from ._exceptions import REFUSAL_PREFIX

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Accept optional leading whitespace, then optional ``ERROR: `` (with any case),
# then the marker prefix. This is broader than knack's exact ``%(levelname)s: ``
# format because we saw exactly one variant (``ERROR: ``) in evidence and want
# to be robust to a future one (``Error: ``, ``error: ``) without another
# consumer-contract change.
_ERROR_PREFIX = re.compile(r"^\s*(?:error\s*:\s*)?", flags=re.IGNORECASE)


def strip_decorations(line):
    """Return ``line`` with ANSI colours, leading whitespace and any ``ERROR:``
    prefix stripped. Returned string is safe to compare against ``REFUSAL_PREFIX``.
    """
    without_ansi = _ANSI_ESCAPE.sub("", line)
    without_prefix = _ERROR_PREFIX.sub("", without_ansi, count=1)
    return without_prefix


def find_refusal_line(stderr_text):
    """Return the first stderr line whose decorated form begins with
    ``AZ_LOGICAPP_REFUSAL: ``, or ``None`` if none is present.

    Raises :class:`AmbiguousRefusalError` if more than one candidate is found —
    ambiguity beats guessing.
    """
    if stderr_text is None:
        return None
    candidates = []
    for raw in stderr_text.splitlines():
        stripped = strip_decorations(raw)
        if stripped.startswith(REFUSAL_PREFIX):
            candidates.append(stripped)
    if not candidates:
        return None
    if len(candidates) > 1:
        raise AmbiguousRefusalError(
            "found {n} refusal marker lines on stderr; expected exactly one".format(
                n=len(candidates)))
    return candidates[0]


def parse_refusal_stderr(stderr_text):
    """Parse ``stderr_text`` and return the refusal payload dict, or ``None``
    if the stderr does not contain a marker line at all.

    A ``None`` return is the parser's way of surfacing a rung-4 emission (or
    a non-refusal exit). Callers that need machine-readability MUST NOT treat
    ``None`` as "no refusal happened" — that is only knowable by pairing with
    the process exit code.

    Raises :class:`AmbiguousRefusalError` if multiple marker lines are found.
    Raises :class:`MalformedRefusalError` if the marker is present but the
    trailing JSON payload does not parse or is missing required keys.
    """
    line = find_refusal_line(stderr_text)
    if line is None:
        return None
    json_text = line[len(REFUSAL_PREFIX):]
    try:
        payload = json.loads(json_text)
    except (ValueError, TypeError) as ex:
        raise MalformedRefusalError(
            "refusal marker present but payload did not parse: {err}".format(err=ex))
    if not isinstance(payload, dict):
        raise MalformedRefusalError(
            "refusal marker present but payload is not a JSON object")
    required = {"capabilityId", "feasibilityState", "gap", "remedy", "delegatedTo"}
    missing = required - payload.keys()
    if missing:
        raise MalformedRefusalError(
            "refusal payload missing required keys: {keys}".format(
                keys=sorted(missing)))
    if not (payload.get("remedy") or payload.get("delegatedTo")):
        raise MalformedRefusalError(
            "refusal payload provides neither remedy nor delegatedTo (D3)")
    return payload


class AmbiguousRefusalError(ValueError):
    """More than one refusal-marker line seen on stderr."""


class MalformedRefusalError(ValueError):
    """A marker line is present but the payload is invalid."""


__all__ = [
    "strip_decorations",
    "find_refusal_line",
    "parse_refusal_stderr",
    "AmbiguousRefusalError",
    "MalformedRefusalError",
]

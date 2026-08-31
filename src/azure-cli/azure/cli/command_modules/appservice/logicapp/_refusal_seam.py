# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=global-statement
"""Refusal emission seam.

A single helper — :func:`emit_refusal` — through which every command body emits a
refusal. Command bodies never construct :class:`DesignRefusalError`,
:class:`PlatformGapError`, or :class:`ModePreconditionError` directly, and never raise
them; that is this module's job.

Purpose of the seam
-------------------
The exact wire shape of a refusal (whether the stderr line begins with the bare
``AZ_LOGICAPP_REFUSAL:`` marker, with an ``ERROR:`` prefix, or with prose only) is a
convention the Azure CLI team owns. No other command module emits a structured
machine-readable marker on stderr today, so that shape is an open review question
rather than an established precedent.

Routing every refusal through this one helper means the answer changes **one
implementation** instead of ~40 call sites. The consumer contract is deliberately
prefix-tolerant, so the most likely outcome (an ``ERROR: `` prefix being required)
costs nothing.

The rung ladder
---------------
Four selectable emission strategies, in preference order. Selecting a rung is a
module-level state change; **no command body sees the choice**::

    RUNG_1_MARKER_OVERRIDE
        Refusal exception uses the ``_LogicAppRefusalMixin`` override. Stderr
        contains the bare ``AZ_LOGICAPP_REFUSAL: {json}`` line (plus one human
        summary line). This is today's behaviour.

    RUNG_2_MARKER_ERROR_PREFIX_ACCEPTED
        Refusal exception uses the override; consumer contract is defined to
        accept a leading ``ERROR: ``. Emission is identical to rung 1 today;
        the difference is only that we do not require the CLI team to accept
        the bare-marker precedent — we can degrade to rung 3 with no
        consumer-side breakage.

    RUNG_3_MARKER_NO_OVERRIDE
        Refusal exception is a plain ``AzCLIError`` subclass, no override. The
        ``error_msg`` still carries the ``AZ_LOGICAPP_REFUSAL: {json}`` line;
        knack's default logger renders it as ``ERROR: AZ_LOGICAPP_REFUSAL:
        {json}`` (color off) or ANSI-wrapped (color on). The prefix-tolerant
        consumer contract still parses the same payload out.

    RUNG_4_SET_RECOMMENDATION_PROSE
        No marker. Refusal is a plain ``AzCLIError`` with an ``error_msg`` of
        the gap prose and ``set_recommendation()`` for the alternative.
        Machine-readability is **lost**; documented as a §6 LOSS if we land
        here.

Rung selection is by ``AZURE_LOGICAPP_REFUSAL_RUNG`` env var (default rung 1) or by
:func:`set_active_rung` at process start. Tests use the latter to prove the rung
choice is localised.

Invariants preserved
--------------------
- Every refusal names a **cause and an alternative** (constructor-enforced in
  ``_exceptions.py`` for rungs 1–3; enforced here for rung 4). See D3.
- Payload keys are ``{capabilityId, feasibilityState, gap, remedy, delegatedTo}``
  (plus optional ``deploymentMode``) at every marker-carrying rung.
- Command bodies contain **zero** refusal-emission logic. Verified by
  ``tests/unit/test_refusal_seam.py::test_no_command_module_raises_refusal_directly``.
"""

import os
import sys

from azure.cli.core.azclierror import AzCLIError, ServiceError, UserFault

from ._exceptions import (
    REFUSAL_PREFIX,
    DesignRefusalError,
    ModePreconditionError,
    PlatformGapError,
    _build_error_msg,
    _has_value,
)

# --- rung identifiers ------------------------------------------------------

RUNG_1_MARKER_OVERRIDE = "1"
RUNG_2_MARKER_ERROR_PREFIX_ACCEPTED = "2"
RUNG_3_MARKER_NO_OVERRIDE = "3"
RUNG_4_SET_RECOMMENDATION_PROSE = "4"

_VALID_RUNGS = frozenset({
    RUNG_1_MARKER_OVERRIDE,
    RUNG_2_MARKER_ERROR_PREFIX_ACCEPTED,
    RUNG_3_MARKER_NO_OVERRIDE,
    RUNG_4_SET_RECOMMENDATION_PROSE,
})

_ACTIVE_RUNG = None

_RUNG_ENV_VAR = "AZURE_LOGICAPP_REFUSAL_RUNG"


def get_active_rung():
    """Return the active rung id, initialising from environment on first call."""
    global _ACTIVE_RUNG
    if _ACTIVE_RUNG is None:
        value = os.environ.get(_RUNG_ENV_VAR, RUNG_1_MARKER_OVERRIDE)
        if value not in _VALID_RUNGS:
            raise ValueError(
                "Invalid {env}={value!r}; expected one of {valid}".format(
                    env=_RUNG_ENV_VAR, value=value, valid=sorted(_VALID_RUNGS)))
        _ACTIVE_RUNG = value
    return _ACTIVE_RUNG


def set_active_rung(rung):
    """Set the active rung. Used by tests and by any deployment-time toggle.

    Command bodies must not call this. There is exactly one place where this is
    called in production: initialisation on module import (via the env var).
    """
    global _ACTIVE_RUNG
    if rung not in _VALID_RUNGS:
        raise ValueError(
            "Invalid rung {value!r}; expected one of {valid}".format(
                value=rung, valid=sorted(_VALID_RUNGS)))
    _ACTIVE_RUNG = rung


# --- refusal kinds ---------------------------------------------------------

REFUSAL_KIND_DESIGN = "design"
REFUSAL_KIND_PLATFORM_GAP = "platform-gap"
REFUSAL_KIND_MODE_PRECONDITION = "mode-precondition"

_MARKER_EXCEPTION_BY_KIND = {
    REFUSAL_KIND_DESIGN: DesignRefusalError,
    REFUSAL_KIND_PLATFORM_GAP: PlatformGapError,
    REFUSAL_KIND_MODE_PRECONDITION: ModePreconditionError,
}


# --- rung 3 & 4 exception classes ------------------------------------------

class _PlainRefusalUserFault(UserFault):
    """A refusal with the marker line but no ``print_error`` override.

    Used by rung 3. ``error_msg`` carries ``AZ_LOGICAPP_REFUSAL: {json}``; the
    default ``AzCLIError.print_error`` path adds the ``ERROR: `` prefix (or an
    ANSI-coloured equivalent). The prefix-tolerant consumer contract parses
    the same payload out.
    """


class _PlainRefusalServiceError(ServiceError):
    """Rung-3 counterpart to :class:`_PlainRefusalUserFault` for platform gaps."""


class _ProseRefusalUserFault(UserFault):
    """Rung-4 refusal: prose only, no marker."""


class _ProseRefusalServiceError(ServiceError):
    """Rung-4 counterpart for platform gaps."""


_PLAIN_MARKER_CLASS_BY_KIND = {
    REFUSAL_KIND_DESIGN: _PlainRefusalUserFault,
    REFUSAL_KIND_PLATFORM_GAP: _PlainRefusalServiceError,
    REFUSAL_KIND_MODE_PRECONDITION: _PlainRefusalServiceError,
}

_PROSE_CLASS_BY_KIND = {
    REFUSAL_KIND_DESIGN: _ProseRefusalUserFault,
    REFUSAL_KIND_PLATFORM_GAP: _ProseRefusalServiceError,
    REFUSAL_KIND_MODE_PRECONDITION: _ProseRefusalServiceError,
}


# --- the seam --------------------------------------------------------------

def emit_refusal(kind, capability_id, feasibility_state, gap,
                 remedy=None, delegated_to=None, deployment_mode=None,
                 cause=None):
    """Raise a refusal via the active rung.

    Command bodies call this and only this. There is no ``return`` path — the
    function always raises. Callers whose refusal branches sit inside a broader
    ``except`` should pass ``cause=exc`` to chain the underlying exception via
    ``raise ... from ...`` for tracebacks.

    Parameters mirror the refusal exception constructors so that switching
    between rungs preserves the payload semantics.

    Invariants (all rungs):
      - ``gap`` must be non-empty.
      - At least one of ``remedy`` or ``delegated_to`` must be non-empty.
        (D3: a refusal names a cause AND an alternative.)
      - ``kind`` must be one of the three refusal kinds.
    """
    if kind not in _MARKER_EXCEPTION_BY_KIND:
        raise ValueError(
            "Unknown refusal kind {value!r}; expected one of {valid}".format(
                value=kind, valid=sorted(_MARKER_EXCEPTION_BY_KIND)))

    rung = get_active_rung()
    exc = _build_refusal(
        rung=rung,
        kind=kind,
        capability_id=capability_id,
        feasibility_state=feasibility_state,
        gap=gap,
        remedy=remedy,
        delegated_to=delegated_to,
        deployment_mode=deployment_mode,
    )
    if cause is not None:
        raise exc from cause
    raise exc


def _build_refusal(rung, kind, capability_id, feasibility_state, gap,
                   remedy, delegated_to, deployment_mode):
    """Construct the refusal exception for a given rung.

    Kept factored out so tests can exercise the builder without raising.
    """
    if rung in (RUNG_1_MARKER_OVERRIDE, RUNG_2_MARKER_ERROR_PREFIX_ACCEPTED):
        return _build_marker_override_refusal(
            kind, capability_id, feasibility_state, gap,
            remedy, delegated_to, deployment_mode)

    if rung == RUNG_3_MARKER_NO_OVERRIDE:
        return _build_marker_plain_refusal(
            kind, capability_id, feasibility_state, gap,
            remedy, delegated_to, deployment_mode)

    if rung == RUNG_4_SET_RECOMMENDATION_PROSE:
        return _build_prose_refusal(
            kind, capability_id, feasibility_state, gap,
            remedy, delegated_to, deployment_mode)

    raise AssertionError("unreachable rung {!r}".format(rung))  # pragma: no cover


def _build_marker_override_refusal(kind, capability_id, feasibility_state, gap,
                                   remedy, delegated_to, deployment_mode):
    """Rungs 1 & 2: our subclass with the ``print_error`` override."""
    cls = _MARKER_EXCEPTION_BY_KIND[kind]
    if kind == REFUSAL_KIND_MODE_PRECONDITION:
        return cls(capability_id, feasibility_state, gap,
                   remedy=remedy, delegated_to=delegated_to,
                   deployment_mode=deployment_mode)
    if kind == REFUSAL_KIND_PLATFORM_GAP:
        return cls(capability_id, feasibility_state, gap,
                   remedy=remedy, delegated_to=delegated_to,
                   deployment_mode=deployment_mode)
    return cls(capability_id, feasibility_state, gap,
               remedy=remedy, delegated_to=delegated_to)


def _build_marker_plain_refusal(kind, capability_id, feasibility_state, gap,
                                remedy, delegated_to, deployment_mode):
    """Rung 3: plain ``AzCLIError`` subclass, same marker line in ``error_msg``."""
    if kind == REFUSAL_KIND_MODE_PRECONDITION and deployment_mode is None:
        raise ValueError("ModePreconditionError requires deploymentMode")
    error_msg = _build_error_msg(
        capability_id, feasibility_state, gap,
        remedy=remedy, delegated_to=delegated_to,
        deployment_mode=deployment_mode)
    cls = _PLAIN_MARKER_CLASS_BY_KIND[kind]
    return cls(error_msg)


def _build_prose_refusal(kind, capability_id, feasibility_state, gap,
                         remedy, delegated_to, deployment_mode):
    """Rung 4: prose ``error_msg`` + ``set_recommendation``. Marker is LOST."""
    if not _has_value(gap):
        raise ValueError("gap must be non-empty")
    if not (_has_value(remedy) or _has_value(delegated_to)):
        raise ValueError("at least one of remedy or delegatedTo must be non-empty")
    if kind == REFUSAL_KIND_MODE_PRECONDITION and deployment_mode is None:
        raise ValueError("ModePreconditionError requires deploymentMode")

    alternative = remedy if _has_value(remedy) else delegated_to
    cls = _PROSE_CLASS_BY_KIND[kind]
    exc = cls("Logic Apps CLI refusal ({cap}, {state}): {gap}".format(
        cap=capability_id, state=feasibility_state, gap=gap))
    exc.set_recommendation("Alternative: {alt}".format(alt=alternative))
    return exc


# --- rung-3 emission emulation --------------------------------------------

# Rung 3 relies on knack's default logger to prefix ``ERROR: `` on stderr.
# In-process test callers cannot easily route knack's logger to a captured
# stderr, so we provide a small emitter that mirrors what knack produces for
# the non-color, non-debug console format (verified from source: see P4 and
# ``knack/log.py CLILogging._get_console_log_formats``). The emitter is used
# by tests; production emission goes through ``AzCLI.exception_handler`` →
# ``AzCLIError.print_error`` → knack's logger, exactly as today.

_KNACK_NON_COLOR_ERROR_FORMAT = "ERROR: {msg}"


def emit_to_stderr_for_test(exc, stream=None):
    """Mirror the CLI-level rendering of a refusal for in-process tests.

    Not for production use. Production emission goes through
    ``AzCLI.exception_handler`` and ``AzCLIError.print_error``. This helper
    lets tests exercise each rung's emission without spinning up ``az`` and
    without configuring knack's logging.

    - Rung 1 / rung 2: delegates to the exception's own ``print_error``
      override (which prints the human summary line + the bare marker line).
    - Rung 3: prints ``ERROR: {error_msg}`` on stderr — this is knack's
      documented non-color, non-debug console format for ``logger.error``.
    - Rung 4: prints ``ERROR: {error_msg}`` on stderr followed by each
      recommendation on its own line — matching ``AzCLIError.print_error``'s
      default recommendation-rendering loop (``azclierror.py:79-81``).

    Returns the raw stderr text emitted.
    """
    from ._exceptions import _LogicAppRefusalMixin  # local import to avoid cycle in docs

    if stream is None:
        import io
        stream = io.StringIO()

    if isinstance(exc, _LogicAppRefusalMixin):
        # Rungs 1 & 2 — the extension's own override wins. Redirect stderr for
        # the duration of the call because print_error hardcodes file=sys.stderr.
        real_stderr = sys.stderr
        try:
            sys.stderr = stream
            exc.print_error()
        finally:
            sys.stderr = real_stderr
        return stream.getvalue()

    if isinstance(exc, (_PlainRefusalUserFault, _PlainRefusalServiceError)):
        # Rung 3 — plain refusal, marker in error_msg, no override.
        stream.write(_KNACK_NON_COLOR_ERROR_FORMAT.format(msg=exc.error_msg) + "\n")
        return stream.getvalue()

    if isinstance(exc, (_ProseRefusalUserFault, _ProseRefusalServiceError)):
        # Rung 4 — prose, no marker; recommendations rendered one per line.
        stream.write(_KNACK_NON_COLOR_ERROR_FORMAT.format(msg=exc.error_msg) + "\n")
        for rec in getattr(exc, "recommendations", []) or []:
            stream.write(rec + "\n")
        return stream.getvalue()

    raise AssertionError(
        "emit_to_stderr_for_test received a non-refusal exception: {!r}".format(exc))


__all__ = [
    "RUNG_1_MARKER_OVERRIDE",
    "RUNG_2_MARKER_ERROR_PREFIX_ACCEPTED",
    "RUNG_3_MARKER_NO_OVERRIDE",
    "RUNG_4_SET_RECOMMENDATION_PROSE",
    "REFUSAL_KIND_DESIGN",
    "REFUSAL_KIND_PLATFORM_GAP",
    "REFUSAL_KIND_MODE_PRECONDITION",
    "REFUSAL_PREFIX",
    "get_active_rung",
    "set_active_rung",
    "emit_refusal",
    "emit_to_stderr_for_test",
    # exception classes exported so isinstance checks and typing work; still
    # not intended for direct construction outside the seam.
    "AzCLIError",
]

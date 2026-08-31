# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Shared mechanism for the "vacuity" class of guard defects.

Context (do not delete this without reading the linked findings): the honesty
invariant (``clientSidePaging``) shipped in the extension as an AST selector
keyed on parameter names ``{"max_items", "continuation_token"}``. The port
renamed ``continuation_token`` -> ``next_token``. The selector then matched
ZERO functions and the guard reported PASS -- silently, forever, on every
run -- even after the marker it existed to check was deleted from production
code. See the guard-verification notes.

A second instance of the same class was found independently in this ported
suite's OWN new test code:
a hardcoded 3-tuple of workflow module filenames in the refusal-seam guard, which
would not see a 4th workflow module ever added.

The standing rule
widened the standing
rule: a guard must be RE-PROVEN, not merely proven once, whenever the thing
it guards is renamed or refactored. The single enforceable property named as
sufficient to have caught the honesty-invariant defect is:

    A guard that matches/derives ZERO things must FAIL, not silently pass.

This module is the reusable mechanism for that property. Wrap every
HIGH-RISK (AST-shaped, name-coupled, or hardcoded-name-list) derivation
function in ``@derived_scope(...)`` instead of hand-writing an ``assert
result, "..."`` floor check inline. Two reasons to centralise it rather than
rely on each guard's author remembering to write their own:

  1. A shared, load-bearing failure message that names the defect class by
     name and tells the reader what to do (RE-PROVE THIS GUARD), instead of
     an ad hoc message that may or may not mention the real risk.
  2. A single place to audit. ``test_logicapp_workflow_guard_vacuity_meta.py``
     enumerates every guard wrapped by this decorator and re-proves, in one
     file, that the mechanism itself is capable of firing (a meta-guard that
     cannot fire is the same defect one level up -- see that file for the
     mutation-based proof).

This is NOT a blanket "flag every list/tuple/set literal in test files"
linter. That alternative was considered and rejected in
``test-vacuity-standing-rule.md`` as over-broad (legitimate pinned constants
-- URLs, HTTP methods, schema versions, rung enums -- would be false
positives). This mechanism only wraps constructs an author has deliberately
identified as scope-derivations over a moving surface; it does not attempt
to auto-discover them.
"""

from __future__ import annotations

import functools


class VacuousGuardError(AssertionError):
    """Raised when a HIGH-RISK derivation-based guard computed an empty
    scope. Subclasses AssertionError so pytest reports it exactly like any
    other assertion failure (no special pytest plugin/hook required)."""


def derived_scope(guard_name, hint):
    """Decorator for a zero-argument derivation function that computes the
    live scope of a HIGH-RISK guard (e.g. "every module file backing a
    registered command", "every credential-class query-parameter name the
    production redactor targets").

    Wrapping the derivation in this decorator means the floor check --
    "did this derivation actually find anything?" -- is enforced by the
    mechanism itself, not by an inline assert the guard's author has to
    remember to write. If the wrapped function ever returns an empty
    collection, the guard fails LOUDLY with a message that names the defect
    class and instructs the reader to re-prove the guard, rather than
    silently reporting PASS on a scope of zero.

    :param guard_name: short identifier of the guard this scope feeds,
        used verbatim in the failure message so a failing CI run points
        straight at the guard to re-prove.
    :param hint: a short, guard-specific explanation of what an empty
        result would mean in practice (e.g. "the loader stopped
        registering `logicapp workflow *`, or the op_path prefix
        drifted") -- this is what turns a generic assertion into an
        actionable one.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            materialised = list(result)
            if not materialised:
                raise VacuousGuardError(
                    "VACUITY: guard '{guard}' derived an EMPTY scope from "
                    "{fn}(). {hint} A guard that matches/derives zero things "
                    "must FAIL, not silently report PASS on nothing -- this "
                    "is the exact defect class that let the honesty-invariant "
                    "guard (`clientSidePaging`/`synthesised`) go vacuous after "
                    "a parameter rename (see guard-verification.md §3b) and "
                    "that a hardcoded module tuple repeated in the "
                    "refusal-seam guard. RE-PROVE THIS GUARD before "
                    "merging: confirm whether the empty result reflects an "
                    "intentional rename/refactor of the guarded thing, then "
                    "fix the derivation to track the new shape. Do NOT "
                    "silence this by re-widening to a hardcoded literal -- "
                    "that reintroduces the exact vacuity this mechanism "
                    "exists to catch.".format(
                        guard=guard_name, fn=fn.__name__, hint=hint)
                )
            return materialised
        # Mark the wrapper so the meta-guard can enumerate every guard using
        # this mechanism without needing a hand-maintained registry to stay
        # in sync by convention alone.
        wrapper.is_vacuity_guarded_derivation = True
        wrapper.guard_name = guard_name
        return wrapper

    return decorator

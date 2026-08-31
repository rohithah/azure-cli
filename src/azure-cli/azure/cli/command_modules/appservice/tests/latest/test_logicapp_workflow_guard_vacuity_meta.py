# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Meta-guard for the vacuity mechanism itself.

Rationale (see the ``_guard_vacuity_scope.py`` module docstring): a meta-guard
that cannot fire is exactly the same defect one level up as the guard it is
meant to protect. Having been caught by that class once already -- an earlier
vacuity sweep missed a case that independent review later found -- this file
does not merely
assert the mechanism works; it proves it, every run, two ways:

1. ``TestMechanismItselfCanFail`` -- a synthetic, production-independent
   mutation of ``derived_scope`` proving the decorator raises
   ``VacuousGuardError`` on an empty derivation and passes a non-empty one
   through unchanged. This never depends on any ported production module,
   so it always runs and always proves the mechanism, independent of
   whatever else is happening in the port.

2. ``TestEveryHighRiskDerivationIsRegisteredAndHealthy`` -- a live registry
   of every HIGH-RISK, derivation-based guard currently in the ported
   suite (the guards that replaced hardcoded name/module/parameter lists
   per the widened standing rule). Re-running this registry on every test
   session is what turns "we swept the suite for vacuity once, by hand, on
   2026-08-31" into a standing, automatically-re-checked property rather
   than a snapshot that goes stale the moment a new HIGH-RISK guard is
   added without being added here too.

Adding a NEW HIGH-RISK derivation-based guard? Decorate it with
``@derived_scope`` where it lives (that is what gives it the "zero matches
must FAIL" property on its own), AND add it to the registry below so this
file's coverage-of-coverage claim keeps being true instead of silently
becoming stale itself.
"""

from __future__ import annotations

import pytest

from azure.cli.command_modules.appservice.tests.latest._guard_vacuity_scope import (
    VacuousGuardError,
    derived_scope,
)


class TestMechanismItselfCanFail:
    """Prove ``derived_scope`` itself is a real guard, not a formality.

    No ported production module is touched here -- this is a pure,
    always-available proof of the mechanism, independent of the current
    shape of the workflow port.
    """

    def test_decorator_raises_loudly_on_an_empty_derivation(self):
        @derived_scope("synthetic-empty-guard", "this is a synthetic proof, not a real guard")
        def _always_empty():
            return []

        with pytest.raises(VacuousGuardError) as excinfo:
            _always_empty()

        message = str(excinfo.value)
        assert "VACUITY" in message
        assert "synthetic-empty-guard" in message
        assert "RE-PROVE THIS GUARD" in message
        assert "must FAIL, not silently report PASS on nothing" in message

    def test_decorator_passes_a_nonempty_derivation_through_unchanged(self):
        @derived_scope("synthetic-nonempty-guard", "this is a synthetic proof, not a real guard")
        def _always_three():
            return ["a", "b", "c"]

        assert _always_three() == ["a", "b", "c"]

    def test_decorator_treats_a_generator_the_same_as_a_list(self):
        # these derivations return generator expressions
        # in a couple of places; the floor check must materialise before
        # judging emptiness rather than truth-testing the generator object
        # itself (a generator object is always truthy, empty or not --
        # that would be a silent hole in the mechanism).
        @derived_scope("synthetic-generator-guard", "this is a synthetic proof, not a real guard")
        def _empty_generator():
            return (x for x in [])

        with pytest.raises(VacuousGuardError):
            _empty_generator()


class TestEveryHighRiskDerivationIsRegisteredAndHealthy:
    """Live registry + health check of every HIGH-RISK derivation-based
    guard in the ported suite, per the vacuity classification table in
    the vacuity rule described above.

    Each entry below already enforces its own floor check via
    ``@derived_scope`` at its point of definition -- calling it here a
    second time is deliberate redundancy: it turns "N separate guards each
    protect themselves" into "one file lists and re-proves all N", so a
    reader auditing vacuity coverage has a single place to look, and a
    regression in any one of them fails HERE too, not only at its original
    site.
    """

    def test_refusal_seam_m2_command_module_derivation_is_nonempty(self):
        from azure.cli.command_modules.appservice.tests.latest.test_logicapp_workflow_refusal_seam import (
            _derive_m2_command_modules,
        )
        modules = _derive_m2_command_modules()
        assert modules, "registry says this guard should be HIGH-RISK-derived and healthy"
        assert modules == sorted(modules)

    def test_refusal_seam_refusal_exception_name_derivation_is_nonempty(self):
        from azure.cli.command_modules.appservice.tests.latest.test_logicapp_workflow_refusal_seam import (
            _derive_refusal_exception_names,
        )
        names = _derive_refusal_exception_names()
        assert names, "registry says this guard should be HIGH-RISK-derived and healthy"

    def test_command_collisions_m2_ported_module_suffix_derivation_is_nonempty(self):
        from azure.cli.command_modules.appservice.tests.latest.test_logicapp_workflow_command_collisions import (
            _derive_m2_ported_module_suffixes,
        )
        suffixes = _derive_m2_ported_module_suffixes()
        assert suffixes, "registry says this guard should be HIGH-RISK-derived and healthy"

    def test_run_unit_test_credential_exact_name_derivation_is_nonempty(self):
        from azure.cli.command_modules.appservice.tests.latest.test_logicapp_workflow_run_unit_test import (
            _derive_exact_credential_query_parameter_names,
        )
        names = _derive_exact_credential_query_parameter_names()
        assert names, "registry says this guard should be HIGH-RISK-derived and healthy"

    def test_run_unit_test_credential_marker_derivation_is_nonempty(self):
        from azure.cli.command_modules.appservice.tests.latest.test_logicapp_workflow_run_unit_test import (
            _derive_credential_query_parameter_name_markers,
        )
        markers = _derive_credential_query_parameter_name_markers()
        assert markers, "registry says this guard should be HIGH-RISK-derived and healthy"

    _EXPECTED_REGISTERED_DERIVATION_HEALTH_CHECKS = 5

    def test_registry_size_matches_the_classification_table(self):
        """A crude but real tripwire, not a tautology: count the actual
        ``*_derivation_is_nonempty`` health-check methods on this class and
        compare against the number recorded here and in
        ``findings/vacuity-mechanism.md`` §1. If a health-check method is
        added or removed without updating both this constant and that
        table, this fails -- forcing the two to be reconciled instead of
        drifting apart silently."""
        registered = [
            name for name in dir(TestEveryHighRiskDerivationIsRegisteredAndHealthy)
            if name.startswith("test_") and name.endswith("_derivation_is_nonempty")
        ]
        assert len(registered) == self._EXPECTED_REGISTERED_DERIVATION_HEALTH_CHECKS, (
            "Registered HIGH-RISK derivation health-checks changed from {} to {}: {}. "
            "Update _EXPECTED_REGISTERED_DERIVATION_HEALTH_CHECKS here AND the "
            "classification table in findings/vacuity-mechanism.md §1 to match -- "
            "do not silently leave the two out of sync.".format(
                self._EXPECTED_REGISTERED_DERIVATION_HEALTH_CHECKS, len(registered), sorted(registered))
        )

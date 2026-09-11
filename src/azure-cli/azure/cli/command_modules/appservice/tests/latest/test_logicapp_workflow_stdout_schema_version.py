# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Guards the ruling that command stdout carries no versioned schema identifier.

azure-cli has no convention for stamping command output with a schema id: the
output contract is the command name plus ``--output``/``--query``, versioned by
the CLI version itself. The ``schemaVersion`` fields that the ported CLI emitted
were removed rather than renamed, so nothing here should reintroduce them.

Two uses of the name are deliberately still allowed and are asserted below so a
future reader does not mistake them for oversights:

* capability-manifest rows, which describe commands and are attached to the
  command table -- no command surfaces them today; and
* the ``x-logicapp-cli-redaction`` marker written inside generated unit-test
  mock files, which is an on-disk artifact rather than stdout, and which
  ``unit-test create`` verifies is present before it will emit the zip.
"""

import os
import re
import unittest

from azure.cli.command_modules.appservice.logicapp import _run_unit_test as _run_unit_test_module
from azure.cli.command_modules.appservice.logicapp._run_unit_test import (
    _mockable_operations_response,
    _redaction_metadata,
)
from azure.cli.command_modules.appservice.logicapp._run import (
    _run_show_response,
    _run_summary_response,
)
from azure.cli.command_modules.appservice.logicapp._run_action import (
    _action_response,
)
from azure.cli.command_modules.appservice.logicapp._trigger import (
    TRIGGER_LIST_MANIFEST,
    _trigger_response,
)
from azure.cli.command_modules.appservice.logicapp._trigger_history import (
    _history_response,
    _resubmit_result,
)
from azure.cli.command_modules.appservice.logicapp._version import _version_response
from azure.cli.command_modules.appservice.logicapp._workflow import (
    _workflow_list_response,
    _workflow_response,
)


# One invocation per shipped stdout-response builder. New builders are forced
# into this table by ``test_every_shipped_stdout_response_builder_is_covered``
# below, which discovers the shipped population from disk and fails if any of
# them are missing from here. That coupling is the point: hardcoding the list
# of tested builders let a new builder ship without ever being checked for a
# stdout ``schemaVersion``, which is what this file exists to forbid.
_STDOUT_BUILDER_INVOCATIONS = {
    "_trigger_response": lambda: _trigger_response(
        {"name": "manual", "properties": {"type": "Request"}}, "wf"),
    "_history_response": lambda: _history_response(
        {"name": "08585", "properties": {"fired": True}}, "wf", "manual"),
    "_resubmit_result": lambda: _resubmit_result(
        [{"historyId": "08585", "status": "Accepted"}]),
    "_mockable_operations_response": lambda: _mockable_operations_response(
        {"value": ["Http"]}, http_only=True),
    "_version_response": lambda: _version_response(
        {"name": "08585", "properties": {"state": "Enabled"}}, "wf"),
    "_run_summary_response": lambda: _run_summary_response(
        {"name": "run1", "properties": {"status": "Succeeded"}}, "wf", False),
    "_run_show_response": lambda: _run_show_response(
        {"name": "run1", "properties": {"status": "Succeeded"}}, "wf", "run1", False),
    "_action_response": lambda: _action_response(
        {"name": "Compose_greeting", "properties": {"status": "Succeeded"}}, "wf", "run1", False),
    "_workflow_response": lambda: _workflow_response(
        {"name": "wf", "health": {"state": "Healthy"}}),
    "_workflow_list_response": lambda: _workflow_list_response(
        [{"name": "wf", "health": {"state": "Healthy"}}]),
}


class LogicappWorkflowStdoutSchemaVersionTest(unittest.TestCase):

    def test_stdout_response_builders_emit_no_schema_version(self):
        for builder, invoke in _STDOUT_BUILDER_INVOCATIONS.items():
            payload = invoke()
            self.assertNotIn(
                "schemaVersion", payload,
                "{} put a schema identifier back on stdout".format(builder))

    def test_synthesised_provenance_does_not_claim_schema_version(self):
        # The provenance block lists CLI-generated fields. Leaving schemaVersion
        # listed after removing the field would assert output that is not there.
        synthesised = _mockable_operations_response({"value": []}, http_only=False)["synthesised"]
        self.assertNotIn("schemaVersion", synthesised["fields"])

    def test_capability_manifest_rows_still_carry_schema_version(self):
        # Manifest rows describe commands rather than being command output.
        self.assertIn("schemaVersion", TRIGGER_LIST_MANIFEST)

    def test_on_disk_redaction_marker_still_carries_schema_version(self):
        # Written into generated mock files, not stdout, and load-bearing for the
        # marker check that gates zip emission.
        self.assertIn("schemaVersion", _redaction_metadata(["Authorization"], ["sig"]))


# The scan deliberately accepts more separators than the codebase uses. A scanner
# that can only see the shape it approves of cannot report a violation: it would
# report a clean single-shape population that had silently absorbed an offender.
_ANY_SHAPE = re.compile(r"logicapp\.[A-Za-z][A-Za-z-]*[-/:._]20\d\d-\d\d-\d\d")
_ACCEPTED_SHAPE = re.compile(r"logicapp\.[A-Za-z][A-Za-z-]*-20\d\d-\d\d-\d\d\Z")

# Discovery pattern for stdout-response builders. Any module-level ``def`` whose
# name ends in ``_response`` or ``_result`` is treated as one, mirroring the
# actual naming convention the shipped package uses. The coverage guard below
# rejects a shipped name that is not paired with an invocation in the table
# above, so a new builder cannot ship without being run through the absence
# check for a stdout ``schemaVersion``.
_STDOUT_BUILDER_DEF = re.compile(r"^def (_[a-z][a-z_]*_(?:response|result))\(", re.MULTILINE)


def _scan_shipped_stdout_response_builders():
    package_dir = os.path.dirname(os.path.abspath(_run_unit_test_module.__file__))
    found = {}
    for entry in sorted(os.listdir(package_dir)):
        if not entry.endswith(".py"):
            continue
        path = os.path.join(package_dir, entry)
        with open(path, "r", encoding="utf-8") as handle:
            for name in _STDOUT_BUILDER_DEF.findall(handle.read()):
                found.setdefault(name, entry)
    return found


def _scan_shipped_schema_identifiers():
    package_dir = os.path.dirname(os.path.abspath(_run_unit_test_module.__file__))
    found = {}
    for entry in sorted(os.listdir(package_dir)):
        if not entry.endswith(".py"):
            continue
        path = os.path.join(package_dir, entry)
        with open(path, "r", encoding="utf-8") as handle:
            for identifier in _ANY_SHAPE.findall(handle.read()):
                found.setdefault(identifier, entry)
    return found


class LogicappWorkflowSchemaIdentifierShapeTest(unittest.TestCase):
    """One shape, derived from the shipped package rather than a frozen list.

    The prototype under ``tools/logicapps/cli-ext`` used the slash form for all
    21 of its identifiers, so the slash never distinguished anything there. When
    the surface was ported into core, the 11 identifiers that were declared as
    named constants were normalised to the hyphen form, and one that was written
    as an inline literal several hundred lines from that block was not. Both
    entered core in the same commit. It was an oversight in a single pass, not a
    shape reserved to mean anything, so it is normalised rather than described.

    This asserts no total: a thirteenth hyphen identifier is healthy growth and
    must not require an edit here. Only a new *shape* is a finding.
    """

    def test_every_shipped_schema_identifier_uses_the_hyphen_shape(self):
        offenders = {
            identifier: source
            for identifier, source in _scan_shipped_schema_identifiers().items()
            if not _ACCEPTED_SHAPE.match(identifier)
        }
        self.assertEqual(
            {}, offenders,
            "schema identifiers must use the hyphen shape; a second shape means a "
            "reader cannot tell shape from meaning: {}".format(offenders))

    def test_the_scan_can_see_a_shape_it_does_not_accept(self):
        # Without this, the guard above passes trivially if the scan pattern is
        # ever narrowed to the accepted shape.
        sample = "logicapp.generated-unit-test-redaction/2026-08-30"
        self.assertTrue(_ANY_SHAPE.match(sample))
        self.assertFalse(_ACCEPTED_SHAPE.match(sample))

    def test_the_scan_finds_the_shipped_population(self):
        # Guards against the scan silently matching nothing, which would make
        # the conformance assertion vacuous.
        found = _scan_shipped_schema_identifiers()
        self.assertGreater(len(found), 1)


class LogicappWorkflowStdoutBuilderDiscoveryTest(unittest.TestCase):
    """Bind the absence check to the shipped population rather than a frozen list.

    The first version of this file hardcoded four builders and asserted the
    absence of a stdout ``schemaVersion`` on each. That let a new builder ship
    without ever being iterated, so the ruling ``no stdout schema identifier``
    was enforced only on the population known at the time the test was written.
    A new hyphen-form identifier like ``logicapp.run-summary-2026-08-29`` would
    pass the sibling shape scan above -- because the shape is accepted -- while
    the absence check never looked at it.

    Discovery closes that: the invocation table becomes required rather than
    exemplary, and a new builder cannot land without being wired through it.
    """

    def test_every_shipped_stdout_response_builder_is_covered_by_the_absence_check(self):
        discovered = set(_scan_shipped_stdout_response_builders())
        registered = set(_STDOUT_BUILDER_INVOCATIONS)
        missing = discovered - registered
        self.assertEqual(
            set(), missing,
            "new stdout response builder(s) shipped without an invocation in "
            "_STDOUT_BUILDER_INVOCATIONS; add one so the absence check runs "
            "against them: {}".format(sorted(missing)))

    def test_the_scan_can_see_a_builder_it_does_not_accept(self):
        # Without this, the guard above passes trivially if the discovery
        # pattern is ever narrowed to only the names already registered.
        sample = "def _hypothetical_new_shape_response(raw, workflow):\n"
        self.assertIsNotNone(_STDOUT_BUILDER_DEF.match(sample))
        self.assertNotIn("_hypothetical_new_shape_response", _STDOUT_BUILDER_INVOCATIONS)

    def test_the_scan_finds_the_shipped_population(self):
        # Guards against the scan silently matching nothing, which would make
        # the coverage assertion vacuous.
        found = _scan_shipped_stdout_response_builders()
        self.assertGreater(len(found), 1)


if __name__ == "__main__":
    unittest.main()

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

import unittest

from azure.cli.command_modules.appservice.logicapp._run_unit_test import (
    _mockable_operations_response,
    _redaction_metadata,
)
from azure.cli.command_modules.appservice.logicapp._trigger import (
    TRIGGER_LIST_MANIFEST,
    _trigger_response,
)
from azure.cli.command_modules.appservice.logicapp._trigger_history import (
    _history_response,
    _resubmit_result,
)


class LogicappWorkflowStdoutSchemaVersionTest(unittest.TestCase):

    def test_stdout_response_builders_emit_no_schema_version(self):
        responses = {
            "_trigger_response": _trigger_response(
                {"name": "manual", "properties": {"type": "Request"}}, "wf"),
            "_history_response": _history_response(
                {"name": "08585", "properties": {"fired": True}}, "wf", "manual"),
            "_resubmit_result": _resubmit_result([{"historyId": "08585", "status": "Accepted"}]),
            "_mockable_operations_response": _mockable_operations_response(
                {"value": ["Http"]}, http_only=True),
        }
        for builder, payload in responses.items():
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


if __name__ == "__main__":
    unittest.main()

# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Manifest-collection tests for the workflow workflow surface.

Reduced port of the extension prototype's manifest tests: the shared
``_manifest.py`` module (validate_manifest_row, collect_manifest_rows, ROW_KEYS,
FEASIBILITY_STATES) is ported to core intact, so those tests survive.  Tests
that depended on _manifest_sync.py (NOT ported for workflow), capabilities-map I/O,
and the baseline ``capabilities list`` command are dropped — see
{TEAM_ROOT}\\findings\\test-execution.md for the coverage lost.
"""

import pytest

from azure.cli.core.mock import DummyCli
from azure.cli.command_modules.appservice import AppserviceCommandsLoader
from azure.cli.command_modules.appservice.logicapp._manifest import (
    FEASIBILITY_STATES,
    ROW_KEYS,
    collect_manifest_rows,
    custom_command_with_manifest,
    validate_manifest_row,
)


_VALID_ROW = {
    "capabilityId": "CM-013-list",
    "command": "logicapp workflow trigger list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": "logicapp.trigger-2026-08-29",
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}


def _loaded_command_table():
    loader = AppserviceCommandsLoader(cli_ctx=DummyCli())
    return loader.load_command_table(args=[])


def test_row_shape_includes_content_only_and_mode_condition():
    assert ROW_KEYS == (
        "capabilityId", "command", "plane", "feasibilityState", "schemaVersion",
        "credentialBearing", "delegatedTo", "gap", "modeCondition", "contentOnly")
    assert validate_manifest_row(dict(_VALID_ROW)) == _VALID_ROW


def test_feasibility_states_are_locked():
    assert FEASIBILITY_STATES == (
        "supported", "supported-conditional", "degraded", "gap-client-logic",
        "gap-platform", "refused", "delegated")


def test_mode_condition_is_required_even_when_null():
    row = dict(_VALID_ROW)
    del row["modeCondition"]
    with pytest.raises(ValueError):
        validate_manifest_row(row)


def test_supported_conditional_requires_well_formed_mode_condition():
    row = dict(_VALID_ROW, feasibilityState="supported-conditional")
    with pytest.raises(ValueError):
        validate_manifest_row(row)
    row["modeCondition"] = {
        "axis": "runFromStorage",
        "requiredState": "on",
        "determinationMethod": "determined",
    }
    assert validate_manifest_row(row)["modeCondition"] == row["modeCondition"]


def test_collector_walks_command_table_metadata():
    class Command:
        pass

    command = Command()
    command.logicapp_capability_manifest = dict(_VALID_ROW)
    assert collect_manifest_rows({"logicapp workflow trigger list": command}) == [_VALID_ROW]


def test_registration_helper_attaches_inline_row():
    class Command:
        pass

    class Group:
        def __init__(self, table):
            self.table = table

        def custom_command(self, name, method_name, **kwargs):
            self.table["logicapp workflow trigger list"] = Command()
            return "logicapp workflow trigger list"

    class Loader:
        def __init__(self):
            self.command_table = {}

    loader = Loader()
    custom_command_with_manifest(
        loader, Group(loader.command_table),
        "list", "trigger_list", dict(_VALID_ROW))
    assert collect_manifest_rows(loader.command_table) == [_VALID_ROW]


def test_all_twelve_m2_commands_carry_valid_manifest_rows_and_expected_capability_ids():
    """Real content check on the shipped workflow surface.

    Any manifest row that fails to validate, any missing command, or any
    unexpected capability id lights this up.  The 12 (capability id, command)
    pairs below match the port's declared surface — see
    ``appservice/commands.py::_register_logicapp_workflow_commands`` and each
    module's ``_MANIFEST`` dict.
    """
    table = _loaded_command_table()
    rows = collect_manifest_rows(table)

    m2_pairs = {(row["capabilityId"], row["command"]) for row in rows
                if row["command"].startswith("logicapp workflow ")}

    expected = {
        ("CM-013-list", "logicapp workflow trigger list"),
        ("CM-013-show", "logicapp workflow trigger show"),
        ("CM-014", "logicapp workflow trigger show-schema"),
        ("CM-015", "logicapp workflow trigger show-callback-url"),
        ("CM-016", "logicapp workflow trigger run"),
        ("CM-011", "logicapp workflow trigger history list"),
        ("CM-012-show", "logicapp workflow trigger history show"),
        ("CM-012-show-inputs", "logicapp workflow trigger history show-inputs"),
        ("CM-012-show-outputs", "logicapp workflow trigger history show-outputs"),
        ("CM-017", "logicapp workflow trigger history resubmit"),
        ("CM-018", "logicapp workflow mock list"),
        ("CM-020", "logicapp workflow unit-test create"),
    }
    assert m2_pairs == expected, "shipped workflow manifest surface diverged from expected"

    # Every workflow row round-trips validate_manifest_row cleanly.
    for row in rows:
        if row["command"].startswith("logicapp workflow "):
            assert validate_manifest_row(dict(row), command_name=row["command"]) == row


def test_credential_bearing_matches_command_body_behaviour_for_content_endpoints():
    """CM-011 (history list) and CM-012-show (history show) return the raw
    inline entry including inputsLink/outputsLink URIs, which carry secrets —
    they MUST be credential-bearing. show-inputs/show-outputs strip those
    (they follow the URI and return only content), so contentOnly is true and
    credentialBearing is false. This is a load-bearing distinction — a lie
    here would let content-shape lies pass unnoticed.
    """
    table = _loaded_command_table()
    rows = {row["command"]: row for row in collect_manifest_rows(table)}

    assert rows["logicapp workflow trigger history list"]["credentialBearing"] is True
    assert rows["logicapp workflow trigger history show"]["credentialBearing"] is True
    assert rows["logicapp workflow trigger history show-inputs"]["credentialBearing"] is False
    assert rows["logicapp workflow trigger history show-inputs"]["contentOnly"] is True
    assert rows["logicapp workflow trigger history show-outputs"]["credentialBearing"] is False
    assert rows["logicapp workflow trigger history show-outputs"]["contentOnly"] is True
    # Callback-URL is credential-bearing by design; live evidence trails aid
    # exactly-that failure mode.
    assert rows["logicapp workflow trigger show-callback-url"]["credentialBearing"] is True

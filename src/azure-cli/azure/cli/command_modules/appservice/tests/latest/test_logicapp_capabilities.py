# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Tests for ``az logicapp capabilities list``.

The command is intentionally local metadata: it reads the loaded command-table
manifest rows and must not require a site-runtime client or ARM resource.
"""

from pathlib import Path

import pytest

from azure.cli.core.mock import DummyCli
from azure.cli.command_modules.appservice import AppserviceCommandsLoader
from azure.cli.command_modules.appservice.logicapp._capabilities import (
    CAPABILITIES_LIST_MANIFEST,
    capabilities_list,
    capabilities_table_format,
)
from azure.cli.command_modules.appservice.logicapp._manifest import (
    FEASIBILITY_STATES,
    PLANES,
    ROW_KEYS,
    collect_manifest_rows,
)


def _loaded_command_table():
    loader = AppserviceCommandsLoader(cli_ctx=DummyCli())
    return loader.load_command_table(args=[])


class _Loader:
    def __init__(self, command_table):
        self.command_table = command_table


class _Cmd:
    def __init__(self, command_table):
        self.loader = _Loader(command_table)

    @property
    def cli_ctx(self):
        raise AssertionError("capabilities list must not require cli_ctx, subscription, or HTTP state")


class _Command:
    def __init__(self, row):
        self.logicapp_capability_manifest = row


def _row(command, capability_id, plane="site-runtime", feasibility="supported"):
    return {
        "capabilityId": capability_id,
        "command": command,
        "plane": plane,
        "feasibilityState": feasibility,
        "schemaVersion": "logicapp.sample-2026-09-11",
        "credentialBearing": False,
        "delegatedTo": None,
        "gap": "sample platform gap" if feasibility.startswith("gap-") else None,
        "modeCondition": None,
        "contentOnly": False,
    }


def _fake_command_table():
    supported = _row("logicapp workflow trigger list", "CM-013-list")
    gap = _row("logicapp workflow future list", "CM-099", plane="diagnostic", feasibility="gap-platform")
    capabilities = dict(CAPABILITIES_LIST_MANIFEST)
    return {row["command"]: _Command(row) for row in (supported, gap, capabilities)}


def test_capabilities_list_returns_value_envelope_and_synthesised_disclosure_without_root_schema_version():
    result = capabilities_list(_Cmd(_fake_command_table()))

    assert set(result) == {"value", "synthesised"}
    assert len(result["value"]) == 3
    assert "schemaVersion" not in result
    assert result["synthesised"]["fields"] == ["value"]
    assert "makes no HTTP request" in result["synthesised"]["reason"]


def test_capabilities_list_rows_have_the_closed_field_set_and_per_row_schema_version():
    rows = capabilities_list(_Cmd(_fake_command_table()))["value"]
    for row in rows:
        assert tuple(row) == ROW_KEYS
        assert row["schemaVersion"].startswith("logicapp.")


def test_capabilities_list_includes_its_own_manifest_row_from_the_loaded_table():
    rows = collect_manifest_rows(_loaded_command_table())
    own = [row for row in rows if row["command"] == "logicapp capabilities list"]

    assert own == [CAPABILITIES_LIST_MANIFEST]
    assert own[0]["capabilityId"] == "CM-025"
    assert own[0]["plane"] == "none"


def test_plane_filter_excludes_rows_from_other_planes():
    result = capabilities_list(_Cmd(_fake_command_table()), plane="none")

    assert [row["command"] for row in result["value"]] == ["logicapp capabilities list"]
    assert all(row["plane"] == "none" for row in result["value"])


def test_feasibility_filter_excludes_rows_from_other_states():
    result = capabilities_list(_Cmd(_fake_command_table()), feasibility="gap-platform")

    assert [row["command"] for row in result["value"]] == ["logicapp workflow future list"]
    assert all(row["feasibilityState"] == "gap-platform" for row in result["value"])


def test_capabilities_list_does_not_require_http_or_resource_context():
    result = capabilities_list(_Cmd(_fake_command_table()))

    assert result["value"]
    assert "cli_ctx" not in result


def test_capabilities_table_format_projects_the_value_rows():
    result = capabilities_list(_Cmd(_fake_command_table()))

    assert capabilities_table_format(result) == result["value"]


def test_capabilities_argument_choice_lists_are_locked():
    assert PLANES == ("arm", "site-runtime", "file", "diagnostic", "none")
    assert "bogus" not in PLANES
    assert FEASIBILITY_STATES == (
        "supported", "supported-conditional", "degraded", "gap-client-logic",
        "gap-platform", "refused", "delegated")
    assert "gap-platform" in FEASIBILITY_STATES
    assert "bogus" not in FEASIBILITY_STATES


def test_invalid_filter_values_are_rejected_by_manifest_enum_guards():
    bad_plane = _row("logicapp workflow invalid plane", "CM-098", plane="bogus")
    with pytest.raises(ValueError, match="plane is not recognized"):
        collect_manifest_rows({bad_plane["command"]: _Command(bad_plane)})

    bad_feasibility = _row("logicapp workflow invalid feasibility", "CM-097", feasibility="bogus")
    with pytest.raises(ValueError, match="feasibilityState is not recognized"):
        collect_manifest_rows({bad_feasibility["command"]: _Command(bad_feasibility)})


def test_cli_parser_rejects_invalid_choice_values(caplog):
    from azure.cli.core import get_default_cli

    generated_cert_files = [Path(name) for name in ("testcert-chain.pem", "testcert.cer", "testkey.pvk")]
    try:
        with pytest.raises(SystemExit) as exc:
            get_default_cli().invoke(["logicapp", "capabilities", "list", "--plane", "bogus"])
    finally:
        for path in generated_cert_files:
            path.unlink(missing_ok=True)

    assert exc.value.code == 2
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "not a valid value for '--plane'" in logged
    assert "Allowed values: arm, site-runtime, file, diagnostic, none" in logged

# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long

"""Capability-manifest command bodies for Logic Apps Standard.

``az logicapp capabilities list`` is a CLI metadata command, not a site-runtime
request. It reads the command-table manifest rows that were attached during
``appservice`` command registration and returns them in a ``value`` envelope.
No resource group, site name, token, or HTTP client is required.

The row ``schemaVersion`` values are capability metadata and remain present on
each row. Command stdout deliberately has no root ``schemaVersion``; that
repository-wide Logic Apps invariant is guarded by
``test_logicapp_workflow_stdout_schema_version``.
"""

from ._manifest import collect_manifest_rows

CAPABILITIES_LIST_SCHEMA_DOCUMENT_VERSION = "logicapp.capabilities-list-2026-09-11"


CAPABILITIES_LIST_MANIFEST = {
    "capabilityId": "CM-025",
    "command": "logicapp capabilities list",
    "plane": "none",
    "feasibilityState": "supported",
    "schemaVersion": CAPABILITIES_LIST_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}


def capabilities_list(cmd, plane=None, feasibility=None):
    """List loaded Logic Apps command capability rows."""
    rows = collect_manifest_rows(_get_command_table(cmd))
    if plane is not None:
        rows = [row for row in rows if row["plane"] == plane]
    if feasibility is not None:
        rows = [row for row in rows if row["feasibilityState"] == feasibility]
    return _capabilities_response(rows)


def capabilities_table_format(result):
    if not result:
        return []
    return result.get("value", [])


def _capabilities_response(rows):
    return {
        "value": rows,
        "synthesised": {
            "fields": ["value"],
            "reason": (
                "The command reads capability manifest rows attached to the loaded Azure CLI "
                "command table. It makes no HTTP request; every value[] entry is CLI metadata."
            ),
        },
    }


def _get_command_table(cmd):
    loader = getattr(cmd, "loader", None)
    if loader is not None and getattr(loader, "command_table", None) is not None:
        return loader.command_table

    cli_ctx = getattr(cmd, "cli_ctx", None)
    invocation = getattr(cli_ctx, "invocation", None)
    commands_loader = getattr(invocation, "commands_loader", None)
    if commands_loader is not None and getattr(commands_loader, "command_table", None) is not None:
        return commands_loader.command_table

    raise ValueError("Unable to locate the loaded Azure CLI command table for manifest collection")

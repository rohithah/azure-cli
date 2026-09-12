# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Unit tests for workflow metadata command bodies (list, show).

Assertions target real content from live workflow captures: route paths,
``api-version`` handling, the seven observed platform keys, absence of root
``state`` and stdout ``schemaVersion``, list envelope shape, and refusal to mint
``connectors`` or ``nextContinuationToken`` for a route that does not support ``$top``.
"""

import pytest

from azure.cli.core.azclierror import ResourceNotFoundError
from azure.cli.core.mock import DummyCli

from azure.cli.command_modules.appservice.logicapp._runtime_client import (
    SiteRuntimeClient,
    workflow_path,
    workflows_path,
)
from azure.cli.command_modules.appservice.logicapp._workflow import (
    workflow_list,
    workflow_list_table_format,
    workflow_show,
    workflow_show_table_format,
)


_SITE_ID = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Web/sites/site"


class _Cmd:
    cli_ctx = DummyCli()


class _Response:
    content = b"x"

    def __init__(self, payload):
        self._payload = payload
        self.headers = {}
        self.content = b"x" if payload is not None else b""

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _workflow(name="specDemo"):
    return {
        "definition_href": "https://la-cli-pilot-ri2bg8.azurewebsites.net/admin/vfs/site/wwwroot/{}/workflow.json".format(name),
        "health": {"state": "Healthy"},
        "href": "https://la-cli-pilot-ri2bg8.azurewebsites.net/runtime/webhooks/workflow/api/management/workflows/{}".format(name),
        "isDisabled": False,
        "kind": "Stateful",
        "name": name,
        "triggers": {
            "manual": {
                "kind": "Http",
                "type": "Request",
            },
        },
    }


# ---- workflow show -----------------------------------------------------


def test_workflow_show_calls_singleton_route_and_url_encodes_name():
    client = _Client(_workflow())
    workflow_show(_Cmd(), "rg", "site", "wf/one", client=client)
    assert client.calls == [("get", "workflows/wf%2Fone", None)]


def test_workflow_show_passes_platform_payload_through_verbatim():
    payload = _workflow()
    client = _Client(payload)
    result = workflow_show(_Cmd(), "rg", "site", "specDemo", client=client)

    assert set(result) == {"definition_href", "health", "href", "isDisabled", "kind", "name", "triggers"}
    assert result == payload
    assert result["health"]["state"] == "Healthy"
    assert "state" not in result
    assert "schemaVersion" not in result


def test_workflow_show_missing_workflow_uses_runtime_not_found_translation():
    client = _Client(ResourceNotFoundError("workflow not found"))
    with pytest.raises(ResourceNotFoundError) as exc_info:
        workflow_show(_Cmd(), "rg", "site", "missing", client=client)
    assert "AZ_LOGICAPP_REFUSAL" not in str(exc_info.value)


# ---- workflow list -----------------------------------------------------


def test_workflow_list_calls_workflows_route():
    client = _Client([_workflow()])
    workflow_list(_Cmd(), "rg", "site", client=client)
    assert client.calls == [("get", "workflows", None)]


def test_workflow_list_wraps_platform_array_in_value_and_does_not_page_or_mint_connectors():
    entries = [_workflow("retryReps"), _workflow("specDemo")]
    client = _Client(entries)
    result = workflow_list(_Cmd(), "rg", "site", client=client)

    assert set(result) == {"value", "synthesised"}
    assert result["value"] == entries
    assert "nextContinuationToken" not in result
    assert result["synthesised"]["fields"] == ["value"]
    assert "top-level array" in result["synthesised"]["reason"]
    assert "nextContinuationToken" in result["synthesised"]["reason"]
    for item in result["value"]:
        assert set(item) == {"definition_href", "health", "href", "isDisabled", "kind", "name", "triggers"}
        assert item["health"]["state"] == "Healthy"
        assert "state" not in item
        assert "schemaVersion" not in item
        assert "connectors" not in item


def test_workflow_list_accepts_object_value_payload_without_minting_paging():
    entry = _workflow()
    client = _Client({"value": [entry]})
    result = workflow_list(_Cmd(), "rg", "site", client=client)
    assert result["value"] == [entry]
    assert "nextContinuationToken" not in result


def test_workflow_list_and_show_table_formats_surface_health_state():
    entries = [_workflow("retryReps"), _workflow("specDemo")]
    list_rows = workflow_list_table_format({"value": entries})
    show_rows = workflow_show_table_format(entries[0])

    assert list_rows[0]["name"] == "retryReps"
    assert list_rows[0]["state"] == "Healthy"
    assert list_rows[1]["isDisabled"] is False
    assert show_rows == [{
        "name": "retryReps",
        "kind": "Stateful",
        "state": "Healthy",
        "isDisabled": False,
        "href": entries[0]["href"],
        "definition_href": entries[0]["definition_href"],
    }]


def test_workflow_paths_and_runtime_client_preserve_api_version_handling():
    calls = []

    def sender(cli_ctx, method, url, headers=None, body=None):
        calls.append((cli_ctx, method, url, headers, body))
        return _Response(_workflow())

    client = SiteRuntimeClient(_Cmd(), _SITE_ID, sender=sender)
    client.get(workflow_path("wf one"))
    client.get(workflows_path())

    assert calls[0][1] == "GET"
    assert calls[0][2].startswith(_SITE_ID + "/hostruntime/runtime/webhooks/workflow/api/management/workflows/wf%20one?")
    assert "api-version=2018-11-01" in calls[0][2]
    assert calls[1][2].startswith(_SITE_ID + "/hostruntime/runtime/webhooks/workflow/api/management/workflows?")
    assert "api-version=2018-11-01" in calls[1][2]
    assert calls[0][3] == ["Content-Type=application/json"]
    assert calls[0][4] is None

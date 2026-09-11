# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Unit tests for the connector catalog command bodies.

Assertions target real content: platform route paths, verbatim pass-through (no minted
fields), the class-conditional ``type`` field, the proven absence of ``kind`` and
``manifest`` on the operation payload, ``synthesised`` disclosure shape, and CLI-side
paging over a route that does not page.

Fixture payloads below are trimmed copies of live captures taken against
``la-cli-pilot-ri2bg8`` / ``rg-lacli-ri2bg8`` and stored in the spec corpus at
``.squad/findings/scratch/connector/``. Key sets are reproduced exactly as observed.
"""

from azure.cli.core.mock import DummyCli

from azure.cli.command_modules.appservice.logicapp._connector import (
    connector_list,
    connector_list_table_format,
    connector_operation_list,
    connector_operation_list_table_format,
    connector_operation_show,
    connector_operation_show_table_format,
    connector_show,
    connector_show_table_format,
)


class _Cmd:
    cli_ctx = DummyCli()


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        return self.payload

    def list(self, path, params=None, continuation_token=None, max_items=None):
        from azure.cli.command_modules.appservice.logicapp._runtime_client import _slice_items
        self.calls.append(("list", path, params, continuation_token, max_items))
        payload = self.payload
        items = payload if isinstance(payload, list) else payload.get("value", [])
        page, next_token = _slice_items(items, continuation_token=continuation_token, max_items=max_items)
        return {"value": page, "nextContinuationToken": next_token}


# Live shape: a serviceProviders entry carries ``type``; properties carry all seven keys.
def _service_provider_connector():
    return {
        "id": "/serviceProviders/acasession",
        "name": "acasession",
        "type": "ServiceProvider",
        "properties": {
            "brandColor": "#8c6cff",
            "capabilities": ["actions"],
            "connectionParameterSets": {"uiDefinition": {}, "values": []},
            "description": "The Python Container Apps Code Interpreter session.",
            "displayName": "Code Interpreter (Python Container Apps session)",
            "iconUri": "https://logicapps.azureedge.net/icons/acasession/icon.svg",
            "isSecureByDefault": False,
        },
    }


# Live shape: a connectionProviders entry has NO ``type`` key at all, and no
# ``capabilities`` / ``connectionParameterSets``.
def _connection_provider_connector():
    return {
        "id": "connectionProviders/dataOperationNew",
        "name": "dataOperationNew",
        "properties": {
            "brandColor": "#8c6cff",
            "description": "Data operations.",
            "displayName": "Data Operations",
            "iconUri": "https://logicapps.azureedge.net/icons/dataoperation/icon.svg",
            "isSecureByDefault": False,
        },
    }


# Live shape: exactly id, name, properties, type -- no kind, no manifest.
def _operation(name="executeCode"):
    return {
        "id": name,
        "name": name,
        "type": name,
        "properties": {
            "annotation": {"family": "/serviceProviders/acasession", "status": "Preview"},
            "api": {
                "brandColor": "#8c6cff",
                "id": "/serviceProviders/acasession",
                "name": "acasession",
                "type": "ServiceProvider",
            },
            "brandColor": "#8c6cff",
            "description": "Executes code in a Python code interpreter session.",
            "iconUri": "https://logicapps.azureedge.net/icons/acasession/executecode/icon.svg",
            "operationType": "ServiceProvider",
            "summary": "Execute Python code",
            "visibility": "Important",
        },
    }


# ---- connector list ----------------------------------------------------


def test_connector_list_calls_operation_groups_route():
    client = _Client({"value": [_service_provider_connector()]})
    connector_list(_Cmd(), "rg", "site", client=client)
    assert client.calls[0][0] == "list"
    assert client.calls[0][1] == "operationGroups"


def test_connector_list_passes_entries_through_verbatim_without_minting_fields():
    """No source classification, no schemaVersion, no backfilled type."""
    entry = _service_provider_connector()
    client = _Client({"value": [entry]})
    result = connector_list(_Cmd(), "rg", "site", client=client)
    assert result["value"][0] == entry
    assert "source" not in result["value"][0]
    assert "schemaVersion" not in result["value"][0]


def test_connector_list_does_not_backfill_type_for_connection_providers():
    """Measured live: 19 of 50 entries (all connectionProviders) carry no type."""
    entry = _connection_provider_connector()
    client = _Client({"value": [entry]})
    result = connector_list(_Cmd(), "rg", "site", client=client)
    assert "type" not in result["value"][0]


def test_connector_list_paging_is_cli_computed_over_a_route_that_does_not_page():
    items = [_service_provider_connector() for _ in range(5)]
    client = _Client({"value": items})
    first = connector_list(_Cmd(), "rg", "site", max_items=2, client=client)
    assert len(first["value"]) == 2
    assert first["nextContinuationToken"] is not None
    second = connector_list(_Cmd(), "rg", "site", max_items=2,
                            next_token=first["nextContinuationToken"], client=client)
    assert len(second["value"]) == 2
    assert second["value"] == items[2:4]


def test_connector_list_discloses_client_side_paging():
    client = _Client({"value": [_service_provider_connector()]})
    result = connector_list(_Cmd(), "rg", "site", client=client)
    synthesised = result["synthesised"]
    assert synthesised["clientSidePaging"] is True
    assert synthesised["fields"] == ["nextContinuationToken"]
    assert "ignores $top" in synthesised["reason"]


def test_connector_list_table_format_includes_the_id_that_distinguishes_the_two_classes():
    """The transformer emits id and type; note that azure-cli core suppresses both
    at table render time, so the class distinction is visible in the default JSON
    output rather than in ``-o table``."""
    client = _Client({"value": [_service_provider_connector(), _connection_provider_connector()]})
    result = connector_list(_Cmd(), "rg", "site", client=client)
    rows = connector_list_table_format(result)
    assert rows[0]["id"] == "/serviceProviders/acasession"
    assert rows[0]["type"] == "ServiceProvider"
    assert rows[1]["id"] == "connectionProviders/dataOperationNew"
    assert rows[1]["type"] is None


# ---- connector show ----------------------------------------------------


def test_connector_show_calls_singleton_route_and_url_encodes_the_name():
    client = _Client(_service_provider_connector())
    connector_show(_Cmd(), "rg", "site", "aca/session", client=client)
    assert client.calls[0][0] == "get"
    assert client.calls[0][1] == "operationGroups/aca%2Fsession"


def test_connector_show_passes_platform_payload_through_verbatim():
    payload = _service_provider_connector()
    client = _Client(payload)
    result = connector_show(_Cmd(), "rg", "site", "acasession", client=client)
    for key, value in payload.items():
        assert result[key] == value
    assert set(result) == set(payload) | {"synthesised"}


def test_connector_show_does_not_backfill_type_for_a_connection_provider():
    client = _Client(_connection_provider_connector())
    result = connector_show(_Cmd(), "rg", "site", "dataOperationNew", client=client)
    assert "type" not in result
    assert result["synthesised"]["fields"] == []


# ---- connector operation list ------------------------------------------


def test_connector_operation_list_calls_operations_route():
    client = _Client({"value": [_operation()]})
    connector_operation_list(_Cmd(), "rg", "site", "acasession", client=client)
    assert client.calls[0][1] == "operationGroups/acasession/operations"


def test_connector_operation_list_echoes_connector_and_discloses_it():
    client = _Client({"value": [_operation()]})
    result = connector_operation_list(_Cmd(), "rg", "site", "acasession", client=client)
    assert result["connector"] == "acasession"
    assert "connector" in result["synthesised"]["fields"]
    assert result["synthesised"]["clientSidePaging"] is True


def test_connector_operation_list_passes_operations_through_verbatim():
    operation = _operation()
    client = _Client({"value": [operation]})
    result = connector_operation_list(_Cmd(), "rg", "site", "acasession", client=client)
    assert result["value"][0] == operation


def test_connector_operation_list_table_format_carries_connector_context():
    client = _Client({"value": [_operation()]})
    result = connector_operation_list(_Cmd(), "rg", "site", "acasession", client=client)
    rows = connector_operation_list_table_format(result)
    assert rows[0]["name"] == "executeCode"
    assert rows[0]["connector"] == "acasession"
    assert rows[0]["summary"] == "Execute Python code"


# ---- connector operation show ------------------------------------------


def test_connector_operation_show_calls_operation_singleton_route():
    client = _Client(_operation())
    connector_operation_show(_Cmd(), "rg", "site", "acasession", "executeCode", client=client)
    assert client.calls[0][1] == "operationGroups/acasession/operations/executeCode"


def test_connector_operation_show_emits_no_kind_and_no_manifest():
    """Both were design-proposed; live capture proves neither is emitted, and
    $expand=manifest returns a byte-identical response. Minting them is refused."""
    client = _Client(_operation())
    result = connector_operation_show(_Cmd(), "rg", "site", "acasession", "executeCode", client=client)
    assert "kind" not in result
    assert "manifest" not in result


def test_connector_operation_show_flattens_platform_object_to_the_root():
    operation = _operation()
    client = _Client(operation)
    result = connector_operation_show(_Cmd(), "rg", "site", "acasession", "executeCode", client=client)
    for key, value in operation.items():
        assert result[key] == value
    assert set(result) == set(operation) | {"connector", "operation", "synthesised"}


def test_connector_operation_show_discloses_both_echoed_inputs():
    client = _Client(_operation())
    result = connector_operation_show(_Cmd(), "rg", "site", "acasession", "executeCode", client=client)
    assert result["connector"] == "acasession"
    assert result["operation"] == "executeCode"
    assert result["synthesised"]["fields"] == ["connector", "operation"]
    assert "manifest" in result["synthesised"]["reason"]


def test_connector_operation_show_table_format():
    client = _Client(_operation())
    result = connector_operation_show(_Cmd(), "rg", "site", "acasession", "executeCode", client=client)
    rows = connector_operation_show_table_format(result)
    assert rows[0]["name"] == "executeCode"
    assert rows[0]["connector"] == "acasession"
    assert rows[0]["visibility"] == "Important"

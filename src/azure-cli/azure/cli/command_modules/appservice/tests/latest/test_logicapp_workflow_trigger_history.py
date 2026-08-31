# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Unit tests for workflow workflow trigger-history command bodies.

Adapted from the extension prototype's trigger-history tests.  The
port split ``show-content --content-name`` into two commands: ``show-inputs``
and ``show-outputs`` (see {TEAM_ROOT}\\findings\\port-execution.md).  So the
extension's parametrised ``trigger_history_show_content`` tests are replaced
here by per-direction tests (one inputs, one outputs, one refusal per link
absence) that hit the ported ``trigger_history_show_inputs`` /
``trigger_history_show_outputs`` bodies directly.

Every assertion targets real content: platform route paths (URL-encoded),
schema-version stamps, ``synthesised`` marker shape, resubmit outcome table,
and the refusal payload's exact cause + alternative.
"""

import json

import pytest

from azure.cli.core.azclierror import AzureResponseError
from azure.cli.core.mock import DummyCli

from azure.cli.command_modules.appservice.logicapp._exceptions import DesignRefusalError
from azure.cli.command_modules.appservice.logicapp._trigger_history import (
    TRIGGER_HISTORY_CONTENT_SCHEMA_VERSION,
    TRIGGER_HISTORY_ENTRY_SCHEMA_VERSION,
    TRIGGER_HISTORY_RESUBMIT_SCHEMA_VERSION,
    TRIGGER_HISTORY_SCHEMA_VERSION,
    trigger_history_list,
    trigger_history_resubmit,
    trigger_history_show,
    trigger_history_show_inputs,
    trigger_history_show_outputs,
)


class _Cmd:
    cli_ctx = DummyCli()


class _Client:
    def __init__(self, payload, post_results=None, raw=None):
        self.payload = payload
        self.post_results = list(post_results or [])
        self.raw = raw or {}
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
        result = {"value": page, "nextContinuationToken": next_token}
        if isinstance(payload, dict):
            result.update({k: v for k, v in payload.items() if k != "value"})
            result["value"] = page
            result["nextContinuationToken"] = next_token
        return result

    def post_with_headers(self, path, body=None, params=None):
        self.calls.append(("post", path, body, params))
        result = self.post_results.pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, tuple):
            return {"payload": result[0], "headers": result[1]}
        return {"payload": result, "headers": {}}

    def get_raw_url(self, url, max_bytes=None):
        self.calls.append(("raw", url, max_bytes))
        return self.raw[url]


def test_trigger_history_list_calls_site_runtime_histories_route_and_maps_contract():
    client = _Client({
        "value": [{
            "name": "hist1",
            "properties": {
                "status": "Succeeded",
                "code": "OK",
                "startTime": "2026-08-29T10:00:00Z",
                "endTime": "2026-08-29T10:00:01Z",
                "scheduledTime": "2026-08-29T09:59:59Z",
                "fired": True,
                "trackingId": "tracking",
                "correlation": {"clientTrackingId": "client"},
                "run": {"name": "run1"},
                "inputsLink": {"uri": "https://example.invalid/inputs"},
                "outputsLink": {"uri": "https://example.invalid/outputs"},
            },
        }],
        "nextLink": "https://example.invalid/next",
    })

    result = trigger_history_list(_Cmd(), "rg", "site", "wf", "manual", client=client)

    assert client.calls == [("list", "workflows/wf/triggers/manual/histories", {"$expand": "run/properties"}, None, None)]
    assert result["schemaVersion"] == TRIGGER_HISTORY_SCHEMA_VERSION
    assert result["nextLink"] == "https://example.invalid/next"
    assert result["nextContinuationToken"] is None
    assert result["synthesised"]["fields"] == [
        "value[].workflow", "value[].trigger", "value[].historyId",
        "value[].runId", "nextContinuationToken"]
    assert result["synthesised"]["clientSidePaging"] is True
    assert "applied by the CLI" in result["synthesised"]["reason"]
    assert result["value"] == [{
        "schemaVersion": TRIGGER_HISTORY_SCHEMA_VERSION,
        "historyId": "hist1",
        "workflow": "wf",
        "trigger": "manual",
        "status": "Succeeded",
        "code": "OK",
        "error": None,
        "startTime": "2026-08-29T10:00:00Z",
        "endTime": "2026-08-29T10:00:01Z",
        "scheduledTime": "2026-08-29T09:59:59Z",
        "fired": True,
        "sourceTriggerHistoryName": None,
        "trackingId": "tracking",
        "correlation": {"clientTrackingId": "client"},
        "run": {"name": "run1"},
        "runId": "run1",
        "runReferenceState": "returned-inline",
        "inputsLink": {"uri": "https://example.invalid/inputs"},
        "outputsLink": {"uri": "https://example.invalid/outputs"},
    }]


def test_trigger_history_list_url_encodes_workflow_and_trigger_segments():
    client = _Client({"value": []})
    trigger_history_list(_Cmd(), "rg", "site", "wf/name", "manual trigger", client=client)
    assert client.calls[0][1] == "workflows/wf%2Fname/triggers/manual%20trigger/histories"


def test_trigger_history_does_not_label_null_run_id_as_synthesised():
    client = _Client({
        "value": [{
            "name": "hist1",
            "properties": {"status": "Skipped", "fired": False},
        }]
    })

    result = trigger_history_list(_Cmd(), "rg", "site", "wf", "manual", client=client)

    assert result["value"][0]["runId"] is None
    assert result["value"][0]["runReferenceState"] == "no-run-reference"
    # ``value[].runId`` is NOT labelled synthesised — the CLI is passing the
    # platform's own absence of a run reference through, not synthesising it.
    # A lie here (marking ``runId`` as synthesised when it's just null) would
    # cost the honesty invariant.
    assert "value[].runId" not in result["synthesised"]["fields"]


def test_trigger_history_synthesises_run_id_from_run_reference_id():
    client = _Client({
        "value": [{
            "name": "hist1",
            "properties": {
                "status": "Succeeded",
                "fired": True,
                "run": {"id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Logic/workflows/wf/runs/run-from-id"},
            },
        }]
    })

    result = trigger_history_list(_Cmd(), "rg", "site", "wf", "manual", client=client)

    assert result["value"][0]["runId"] == "run-from-id"
    assert result["value"][0]["runReferenceState"] == "returned-inline"
    # Projection from the platform-returned run id string IS a synthesis — the
    # CLI extracted the tail segment. That claim MUST appear in synthesised.
    assert "value[].runId" in result["synthesised"]["fields"]


def test_trigger_history_show_calls_single_entry_route_and_uses_entry_schema():
    client = _Client({"name": "hist1", "properties": {"status": "Succeeded", "run": {"name": "run1"}}})

    result = trigger_history_show(_Cmd(), "rg", "site", "wf", "manual", "hist1", client=client)

    assert client.calls == [("get", "workflows/wf/triggers/manual/histories/hist1", {"$expand": "run/properties"})]
    assert result["schemaVersion"] == TRIGGER_HISTORY_ENTRY_SCHEMA_VERSION
    assert result["historyId"] == "hist1"
    assert result["runId"] == "run1"
    assert result["synthesised"]["fields"] == ["workflow", "trigger", "historyId", "runId"]
    assert "_runIdSynthesised" not in result


def test_trigger_history_show_inputs_follows_platform_link_uri_exactly():
    uri = "https://host/runtime/webhooks/workflow/scaleUnits/unit%201/workflows/flow%2Fid/triggers/manual/histories/hist1/contents/TriggerInputs?api-version=2018-11-01&code=secret&sig=signature"
    client = _Client(
        {
            "name": "hist1",
            "properties": {
                "status": "Succeeded",
                "inputsLink": {"uri": uri, "contentSize": 17},
            },
        },
        raw={uri: {"content": b'{"hello":"world"}', "headers": {"content-type": "application/json"}}},
    )

    result = trigger_history_show_inputs(_Cmd(), "rg", "site", "wf", "manual", "hist1", client=client)

    assert client.calls[0] == ("get", "workflows/wf/triggers/manual/histories/hist1", {"$expand": "run/properties"})
    assert client.calls[1] == ("raw", uri, None)
    assert result == {
        "schemaVersion": TRIGGER_HISTORY_CONTENT_SCHEMA_VERSION,
        "workflow": "wf",
        "trigger": "manual",
        "historyId": "hist1",
        "contentName": "inputs",
        "content": {"hello": "world"},
        "contentSizeBytes": 17,
        "contentType": "application/json",
        "preview": '{"hello":"world"}',
        "truncated": False,
        "synthesised": (
            "workflow, trigger, historyId, and contentName are CLI inputs; contentSizeBytes, "
            "preview, and truncated are CLI-computed from the returned content; content was "
            "read by following the platform-provided content link URI exactly."
        ),
    }


def test_trigger_history_show_outputs_follows_outputs_link_uri_exactly():
    """The port split ``show-content`` in two.  A regression that swapped the
    two link kinds (following ``inputsLink`` for ``show-outputs``) would look
    perfectly happy in unit tests that only exercise ``show-inputs`` — this
    one blocks exactly that.
    """
    uri = "https://host/runtime/webhooks/workflow/scaleUnits/unit/workflows/flow/triggers/manual/histories/hist1/contents/TriggerOutputs?api-version=2018-11-01&code=secret&sig=signature"
    client = _Client(
        {
            "name": "hist1",
            "properties": {
                "status": "Succeeded",
                "outputsLink": {"uri": uri, "contentSize": 11},
            },
        },
        raw={uri: {"content": b'{"out":true}', "headers": {"content-type": "application/json"}}},
    )

    result = trigger_history_show_outputs(_Cmd(), "rg", "site", "wf", "manual", "hist1", client=client)

    assert client.calls[1] == ("raw", uri, None)
    assert result["contentName"] == "outputs"
    assert result["content"] == {"out": True}


def test_trigger_history_show_inputs_refuses_when_inputs_link_uri_absent():
    client = _Client({"name": "hist1", "properties": {"status": "Succeeded", "inputsLink": {}}})

    with pytest.raises(DesignRefusalError) as exc_info:
        trigger_history_show_inputs(_Cmd(), "rg", "site", "wf", "manual", "hist1", client=client)

    message = str(exc_info.value)
    assert "AZ_LOGICAPP_REFUSAL" in message
    # Cause names the exact link kind so the operator can distinguish inputs
    # from outputs — this is the D3 "name the cause" invariant on a real refusal.
    assert "does not include a inputsLink.uri content pointer" in message
    # Alternative names the concrete follow-up action.
    assert "retry with show-inputs" in message
    # Only one HTTP call was made — the history entry lookup.  The refusal
    # avoided a fetch of an unknown URI.
    assert len(client.calls) == 1
    payload = json.loads(message[message.index("{"):])
    assert payload["capabilityId"] == "CM-012-show-inputs"


def test_trigger_history_show_outputs_refuses_when_outputs_link_uri_absent():
    client = _Client({"name": "hist1", "properties": {"status": "Succeeded", "outputsLink": {}}})

    with pytest.raises(DesignRefusalError) as exc_info:
        trigger_history_show_outputs(_Cmd(), "rg", "site", "wf", "manual", "hist1", client=client)

    message = str(exc_info.value)
    assert "outputsLink.uri" in message
    payload = json.loads(message[message.index("{"):])
    assert payload["capabilityId"] == "CM-012-show-outputs"


def test_trigger_history_resubmit_loops_and_returns_aggregated_success():
    client = _Client(
        None,
        post_results=[
            (None, {"x-ms-workflow-run-id": "run2"}),
            ({"properties": {"runId": "run3", "status": "Accepted"}}, {}),
        ])

    result = trigger_history_resubmit(_Cmd(), "rg", "site", "wf", "manual", ["hist1", "hist2"], client=client)

    assert client.calls == [
        ("post", "workflows/wf/triggers/manual/histories/hist1/resubmit", None, None),
        ("post", "workflows/wf/triggers/manual/histories/hist2/resubmit", None, None),
    ]
    assert result["schemaVersion"] == TRIGGER_HISTORY_RESUBMIT_SCHEMA_VERSION
    assert result["value"] == [
        {"historyId": "hist1", "runId": "run2", "status": "ResubmitAccepted", "message": None},
        {"historyId": "hist2", "runId": "run3", "status": "Accepted", "message": None},
    ]
    assert "Multi-ID server support remains unresolved" in result["synthesised"]["reason"]


def test_trigger_history_resubmit_partial_failure_raises_with_per_entry_outcomes():
    client = _Client(
        None,
        post_results=[(None, {"x-ms-workflow-run-id": "run2"}), RuntimeError("boom")])

    with pytest.raises(AzureResponseError) as exc_info:
        trigger_history_resubmit(_Cmd(), "rg", "site", "wf", "manual", ["hist1", "hist2"], client=client)

    message = str(exc_info.value)
    assert "completed with 1 failure" in message
    aggregate = json.loads(message[message.index("{"):])
    assert aggregate["value"][0] == {"historyId": "hist1", "runId": "run2", "status": "ResubmitAccepted", "message": None}
    assert aggregate["value"][1]["historyId"] == "hist2"
    assert aggregate["value"][1]["status"] == "Failed"
    assert "boom" in aggregate["value"][1]["message"]

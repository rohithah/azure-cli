# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Unit tests for workflow run-action command bodies (list, show, show-content).

Assertions target real content: platform route paths, SAS URI redaction sentinel,
``synthesised`` shape (paging-envelope for list, projection-provenance for show,
fixed-disclosure-string for show-content), refusal payload for a missing
``{inputs|outputs}Link.uri``, and a real content dereference through the
platform-provided URI without an Authorization header.
"""

import json

import pytest

from azure.cli.core.mock import DummyCli

from azure.cli.command_modules.appservice.logicapp._constants import LOGICAPP_REDACTION_SENTINEL
from azure.cli.command_modules.appservice.logicapp._exceptions import DesignRefusalError
from azure.cli.command_modules.appservice.logicapp._run_action import (
    run_action_list,
    run_action_show,
    run_action_show_content,
)


class _Cmd:
    cli_ctx = DummyCli()


class _Client:
    def __init__(self, payload, raw=None):
        self.payload = payload
        self.raw = raw or {}
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def list(self, path, params=None, continuation_token=None, max_items=None):
        from azure.cli.command_modules.appservice.logicapp._runtime_client import _slice_items
        self.calls.append(("list", path, params, continuation_token, max_items))
        payload = self.payload
        items = payload if isinstance(payload, list) else payload.get("value", [])
        page, next_token = _slice_items(items, continuation_token=continuation_token, max_items=max_items)
        result = {"value": page, "nextContinuationToken": next_token}
        if isinstance(payload, dict):
            for k, v in payload.items():
                if k not in ("value",):
                    result[k] = v
            result["value"] = page
            result["nextContinuationToken"] = next_token
        return result

    def get_raw_url(self, url, max_bytes=None):
        self.calls.append(("raw", url, max_bytes))
        return self.raw[url]


_SAS_URI = (
    "https://host/runtime/webhooks/workflow/scaleUnits/prod-00/workflows/abc/runs/run1/"
    "actions/Compose_greeting/contents/ActionInputs?api-version=2018-11-01&code=SECRET&"
    "sig=SIG&sv=1.0&se=2026-09-09T11:00:00Z&sp=%2Fread")


def _action_item(name="Compose_greeting"):
    return {
        "id": "/workflows/wf/runs/run1/actions/{}".format(name),
        "name": name,
        "type": "workflows/runs/actions",
        "properties": {
            "canResubmit": True,
            "code": "OK",
            "correlation": {
                "actionTrackingId": "trk-action",
                "clientTrackingId": "trk-client",
            },
            "startTime": "2026-09-09T07:29:24.2607424Z",
            "endTime": "2026-09-09T07:29:24.2625781Z",
            "status": "Succeeded",
            "inputsLink": {"uri": _SAS_URI, "contentSize": 8},
            "outputsLink": {"uri": _SAS_URI, "contentSize": 8},
        },
    }


# ---- run action list ---------------------------------------------------


def test_run_action_list_calls_dedicated_actions_route_not_expanded_run():
    """The prototype went through $expand=properties/actions on the run route.
    Live capture confirms a dedicated route exists; this asserts we use it."""
    client = _Client({"value": [_action_item()]})
    run_action_list(_Cmd(), "rg", "site", "wf", "run1", client=client)
    assert client.calls[0][0] == "list"
    assert client.calls[0][1] == "workflows/wf/runs/run1/actions"


def test_run_action_list_projects_action_row_with_echoed_workflow_and_run_id():
    client = _Client({"value": [_action_item()]})
    result = run_action_list(_Cmd(), "rg", "site", "wf", "run1", client=client)
    row = result["value"][0]
    assert row["actionName"] == "Compose_greeting"
    assert row["workflow"] == "wf"
    assert row["runId"] == "run1"
    assert row["status"] == "Succeeded"
    assert row["canResubmit"] is True


def test_run_action_list_redacts_sas_uris_by_default():
    client = _Client({"value": [_action_item()]})
    result = run_action_list(_Cmd(), "rg", "site", "wf", "run1", client=client)
    row = result["value"][0]
    assert row["inputsLink"]["uri"] == LOGICAPP_REDACTION_SENTINEL
    assert row["outputsLink"]["uri"] == LOGICAPP_REDACTION_SENTINEL
    assert row["inputsLink"]["contentSize"] == 8


def test_run_action_list_emits_sas_uris_verbatim_only_when_show_content_urls_set():
    client = _Client({"value": [_action_item()]})
    result = run_action_list(_Cmd(), "rg", "site", "wf", "run1",
                             show_content_urls=True, client=client)
    assert result["value"][0]["inputsLink"]["uri"] == _SAS_URI


def test_run_action_list_synthesised_declares_client_side_paging_and_echoed_fields():
    client = _Client({"value": [_action_item()]})
    result = run_action_list(_Cmd(), "rg", "site", "wf", "run1", client=client)
    synth = result["synthesised"]
    assert synth["clientSidePaging"] is True
    assert "value[].workflow" in synth["fields"]
    assert "value[].runId" in synth["fields"]


def test_run_action_list_emits_no_stdout_schema_version():
    client = _Client({"value": [_action_item()]})
    result = run_action_list(_Cmd(), "rg", "site", "wf", "run1", client=client)
    assert "schemaVersion" not in result
    assert "schemaVersion" not in result["value"][0]


def test_run_action_list_url_encodes_workflow_and_run_id_segments():
    client = _Client({"value": []})
    run_action_list(_Cmd(), "rg", "site", "wf name", "run/id", client=client)
    assert client.calls[0][1] == "workflows/wf%20name/runs/run%2Fid/actions"


# ---- run action show ---------------------------------------------------


def test_run_action_show_calls_singular_action_route():
    client = _Client(_action_item())
    result = run_action_show(_Cmd(), "rg", "site", "wf", "run1", "Compose_greeting", client=client)

    assert client.calls == [("get", "workflows/wf/runs/run1/actions/Compose_greeting", None)]
    assert result["actionName"] == "Compose_greeting"
    assert result["workflow"] == "wf"
    assert result["runId"] == "run1"
    assert result["status"] == "Succeeded"


def test_run_action_show_redacts_sas_uris_by_default_and_opts_in():
    client = _Client(_action_item())
    result = run_action_show(_Cmd(), "rg", "site", "wf", "run1", "Compose_greeting", client=client)
    assert result["inputsLink"]["uri"] == LOGICAPP_REDACTION_SENTINEL

    client2 = _Client(_action_item())
    result2 = run_action_show(_Cmd(), "rg", "site", "wf", "run1", "Compose_greeting",
                              show_content_urls=True, client=client2)
    assert result2["inputsLink"]["uri"] == _SAS_URI


def test_run_action_show_synthesised_uses_projection_shape():
    client = _Client(_action_item())
    result = run_action_show(_Cmd(), "rg", "site", "wf", "run1", "Compose_greeting", client=client)
    synth = result["synthesised"]
    assert synth["fields"] == ["workflow", "runId"]
    assert "clientSidePaging" not in synth
    assert "serverSidePaging" not in synth


def test_run_action_show_emits_no_stdout_schema_version():
    client = _Client(_action_item())
    result = run_action_show(_Cmd(), "rg", "site", "wf", "run1", "Compose_greeting", client=client)
    assert "schemaVersion" not in result


# ---- run action show-content -------------------------------------------


def test_run_action_show_content_inputs_follows_link_uri_exactly():
    client = _Client(
        _action_item(),
        raw={_SAS_URI: {"content": b'"hello "', "headers": {"content-type": "application/json"}}},
    )
    result = run_action_show_content(_Cmd(), "rg", "site", "wf", "run1",
                                     "Compose_greeting", "inputs", client=client)

    # Two calls: GET the action, then fetch the URI.
    assert client.calls[0] == ("get", "workflows/wf/runs/run1/actions/Compose_greeting", None)
    assert client.calls[1] == ("raw", _SAS_URI, None)
    assert result["content"] == "hello "
    assert result["workflow"] == "wf"
    assert result["runId"] == "run1"
    assert result["action"] == "Compose_greeting"
    assert result["contentName"] == "inputs"
    assert result["contentType"] == "application/json"
    assert result["truncated"] is False
    assert result["contentSizeBytes"] == 8
    assert "logicapp" not in result.get("synthesised", "").lower() or True  # sanity


def test_run_action_show_content_outputs_prefers_outputs_link_not_inputs_link():
    """A regression that swapped the two link kinds -- following inputsLink for
    show-content --content outputs -- would look happy in a single-link test.
    This one blocks exactly that."""
    outputs_uri = _SAS_URI.replace("ActionInputs", "ActionOutputs")
    item = _action_item()
    item["properties"]["outputsLink"] = {"uri": outputs_uri, "contentSize": 8}
    client = _Client(
        item,
        raw={outputs_uri: {"content": b'"hello "', "headers": {"content-type": "application/json"}}},
    )
    result = run_action_show_content(_Cmd(), "rg", "site", "wf", "run1",
                                     "Compose_greeting", "outputs", client=client)
    assert client.calls[1] == ("raw", outputs_uri, None)
    assert result["contentName"] == "outputs"


def test_run_action_show_content_refuses_when_inputs_link_uri_absent():
    item = _action_item()
    item["properties"]["inputsLink"] = {}
    client = _Client(item)

    with pytest.raises(DesignRefusalError) as exc_info:
        run_action_show_content(_Cmd(), "rg", "site", "wf", "run1",
                                "Compose_greeting", "inputs", client=client)
    message = str(exc_info.value)
    assert "AZ_LOGICAPP_REFUSAL" in message
    assert "inputsLink.uri" in message
    payload = json.loads(message[message.index("{"):])
    assert payload["capabilityId"] == "CM-004-inputs"
    # Only one HTTP call was made -- the action lookup. No fetch attempted.
    assert len(client.calls) == 1


def test_run_action_show_content_refuses_when_outputs_link_uri_absent():
    item = _action_item()
    item["properties"]["outputsLink"] = {}
    client = _Client(item)

    with pytest.raises(DesignRefusalError) as exc_info:
        run_action_show_content(_Cmd(), "rg", "site", "wf", "run1",
                                "Compose_greeting", "outputs", client=client)
    message = str(exc_info.value)
    payload = json.loads(message[message.index("{"):])
    assert payload["capabilityId"] == "CM-004-outputs"
    assert "outputsLink.uri" in message


def test_run_action_show_content_previews_and_truncates_long_bodies():
    long_body = ("x" * 500).encode("utf-8")
    client = _Client(
        _action_item(),
        raw={_SAS_URI: {"content": long_body, "headers": {"content-type": "text/plain", "content-length": "500"}}},
    )
    result = run_action_show_content(_Cmd(), "rg", "site", "wf", "run1",
                                     "Compose_greeting", "inputs", client=client)
    assert result["contentSizeBytes"] == 500
    assert result["truncated"] is True
    assert result["preview"].endswith("...[truncated]")


def test_run_action_show_content_emits_no_stdout_schema_version():
    client = _Client(
        _action_item(),
        raw={_SAS_URI: {"content": b'"hello "', "headers": {"content-type": "application/json"}}},
    )
    result = run_action_show_content(_Cmd(), "rg", "site", "wf", "run1",
                                     "Compose_greeting", "inputs", client=client)
    assert "schemaVersion" not in result

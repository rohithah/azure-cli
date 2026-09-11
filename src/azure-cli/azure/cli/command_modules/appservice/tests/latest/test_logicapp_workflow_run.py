# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Unit tests for workflow run command bodies (list, show).

Assertions target real content: platform route paths, redaction sentinel on
SAS-bearing URIs, ``synthesised`` block shape (paging-envelope for list,
projection-provenance for show), server-side paging with the ``$skiptoken``
parsed out of the platform ``nextLink`` verbatim, workflow-version hoist, and
--end-time client-side post-filter.
"""

import pytest

from azure.cli.core.azclierror import ValidationError
from azure.cli.core.mock import DummyCli

from azure.cli.command_modules.appservice.logicapp._constants import LOGICAPP_REDACTION_SENTINEL
from azure.cli.command_modules.appservice.logicapp._run import (
    run_list,
    run_show,
)


class _Cmd:
    cli_ctx = DummyCli()


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


# ---- run list ----------------------------------------------------------


_SAS_URI = (
    "https://host/runtime/webhooks/workflow/scaleUnits/prod-00/workflows/abc/runs/run1/"
    "contents/TriggerInputs?api-version=2018-11-01&code=SECRET&sig=SIG&sv=1.0&"
    "se=2026-09-09T11:00:00Z&sp=%2Fread")

_NEXT_LINK_WITH_SKIPTOKEN = (
    "https://management.azure.com:443/subscriptions/sub/resourceGroups/rg/providers/"
    "Microsoft.Web/sites/site/hostruntime/runtime/webhooks/workflow/api/management/"
    "workflows/wf/runs?api-version=2018-11-01&%24top=2&%24skiptoken=OPAQUE%3d")


def _run_item(run_id="run1"):
    return {
        "id": "/workflows/wf/runs/{}".format(run_id),
        "name": run_id,
        "type": "workflows/runs",
        "properties": {
            "status": "Succeeded",
            "startTime": "2026-09-09T07:29:23.9604263Z",
            "endTime": "2026-09-09T07:29:24.2990359Z",
            "waitEndTime": "2026-09-09T07:29:23.9604263Z",
            "correlation": {"clientTrackingId": run_id},
            "workflow": {
                "id": "/workflows/wf/versions/08584129710997436905",
                "name": "08584129710997436905",
                "type": "workflows/versions",
            },
            "trigger": {
                "name": "manual",
                "status": "Succeeded",
                "inputsLink": {"uri": _SAS_URI, "contentSize": 68},
                "outputsLink": {"uri": _SAS_URI, "contentSize": 1231},
            },
            "response": {
                "code": "OK",
                "status": "Succeeded",
                "outputsLink": {"uri": _SAS_URI, "contentSize": 36},
            },
        },
    }


def test_run_list_calls_platform_runs_route_with_no_client_side_slicing():
    client = _Client({"value": [_run_item()]})
    result = run_list(_Cmd(), "rg", "site", "wf", client=client)

    assert client.calls == [("get", "workflows/wf/runs", None)]
    assert result["value"][0]["runId"] == "run1"
    assert result["value"][0]["workflow"] == "wf"
    # workflowVersion hoisted from properties.workflow.name
    assert result["value"][0]["workflowVersion"] == "08584129710997436905"


def test_run_list_maps_max_items_to_dollar_top_and_next_token_to_dollar_skiptoken():
    client = _Client({"value": []})
    run_list(_Cmd(), "rg", "site", "wf", max_items=2, next_token="OPAQUE=", client=client)

    _, _, params = client.calls[0]
    assert params["$top"] == 2
    assert params["$skiptoken"] == "OPAQUE="


def test_run_list_maps_status_and_start_time_to_native_filter():
    client = _Client({"value": []})
    run_list(_Cmd(), "rg", "site", "wf",
             status="Failed", start_time="2026-09-01T00:00:00Z", client=client)

    _, _, params = client.calls[0]
    # Native OData $filter clause -- not client-side.
    assert "$filter" in params
    assert "status eq 'Failed'" in params["$filter"]
    assert "startTime ge 2026-09-01T00:00:00Z" in params["$filter"]


def test_run_list_extracts_next_continuation_token_from_platform_next_link_verbatim():
    """The prototype minted a CLI base64 offset token here. That was wrong: the
    platform ships $skiptoken embedded in nextLink and expects it back opaquely.
    This test blocks the regression that would have the CLI mint its own token."""
    client = _Client({
        "value": [_run_item()],
        "nextLink": _NEXT_LINK_WITH_SKIPTOKEN,
    })
    result = run_list(_Cmd(), "rg", "site", "wf", client=client)

    # Value URL-decoded from the encoded nextLink parameter.
    assert result["nextContinuationToken"] == "OPAQUE="
    assert result["nextLink"] == _NEXT_LINK_WITH_SKIPTOKEN


def test_run_list_returns_null_continuation_when_next_link_is_absent():
    client = _Client({"value": [_run_item()]})
    result = run_list(_Cmd(), "rg", "site", "wf", client=client)
    assert result["nextContinuationToken"] is None
    assert result["nextLink"] is None


def test_run_list_redacts_sas_bearing_content_link_uris_by_default():
    client = _Client({"value": [_run_item()]})
    result = run_list(_Cmd(), "rg", "site", "wf", client=client)

    trigger = result["value"][0]["trigger"]
    assert trigger["inputsLink"]["uri"] == LOGICAPP_REDACTION_SENTINEL
    assert trigger["outputsLink"]["uri"] == LOGICAPP_REDACTION_SENTINEL
    # Metadata preserved.
    assert trigger["inputsLink"]["contentSize"] == 68
    response = result["value"][0]["response"]
    assert response["outputsLink"]["uri"] == LOGICAPP_REDACTION_SENTINEL


def test_run_list_emits_sas_bearing_uris_verbatim_only_when_show_content_urls_set():
    client = _Client({"value": [_run_item()]})
    result = run_list(_Cmd(), "rg", "site", "wf", show_content_urls=True, client=client)
    assert result["value"][0]["trigger"]["inputsLink"]["uri"] == _SAS_URI


def test_run_list_synthesised_declares_workflow_version_hoist_and_server_side_paging():
    client = _Client({"value": [_run_item()]})
    result = run_list(_Cmd(), "rg", "site", "wf", client=client)

    synth = result["synthesised"]
    assert synth["serverSidePaging"] is True
    assert "clientSidePaging" not in synth
    assert "value[].workflowVersion" in synth["fields"]
    assert "value[].workflow" in synth["fields"]
    assert "workflowVersion" in synth["reason"]


def test_run_list_end_time_is_applied_client_side_and_disclosed():
    """--end-time is a client-side post-filter; the disclosure only appears when
    the filter is applied. Native --start-time is not listed as a client filter."""
    payload = {"value": [
        _run_item("early"),
        _run_item("late"),
    ]}
    # Give the two runs distinguishable end times.
    payload["value"][0]["properties"]["endTime"] = "2026-09-08T00:00:00Z"
    payload["value"][1]["properties"]["endTime"] = "2026-09-10T00:00:00Z"
    client = _Client(payload)

    result = run_list(_Cmd(), "rg", "site", "wf",
                      end_time="2026-09-09T00:00:00Z", client=client)

    ids = [item["runId"] for item in result["value"]]
    assert ids == ["early"]
    assert result["synthesised"]["clientSideFilters"] == ["endTime"]
    # Native filters are not disclosed as client-side.
    result_no_end = run_list(_Cmd(), "rg", "site", "wf",
                             status="Succeeded", client=client)
    assert "clientSideFilters" not in result_no_end["synthesised"]


def test_run_list_rejects_nonpositive_max_items():
    client = _Client({"value": []})
    with pytest.raises(ValidationError):
        run_list(_Cmd(), "rg", "site", "wf", max_items=0, client=client)


def test_run_list_rejects_bad_iso8601_start_time():
    client = _Client({"value": []})
    with pytest.raises(ValidationError):
        run_list(_Cmd(), "rg", "site", "wf", start_time="tomorrow", client=client)


def test_run_list_returns_null_workflow_version_when_platform_omits_workflow_object():
    payload = {"value": [{
        "name": "run1",
        "properties": {"status": "Succeeded"},
    }]}
    result = run_list(_Cmd(), "rg", "site", "wf", client=_Client(payload))
    assert result["value"][0]["workflowVersion"] is None


def test_run_list_falls_back_to_workflow_id_when_name_absent():
    payload = {"value": [{
        "name": "run1",
        "properties": {
            "status": "Succeeded",
            "workflow": {"id": "/workflows/wf/versions/8888"},
        },
    }]}
    result = run_list(_Cmd(), "rg", "site", "wf", client=_Client(payload))
    assert result["value"][0]["workflowVersion"] == "8888"


def test_run_list_url_encodes_workflow_segment():
    client = _Client({"value": []})
    run_list(_Cmd(), "rg", "site", "wf/name", client=client)
    assert client.calls[0][1] == "workflows/wf%2Fname/runs"


def test_run_list_emits_no_schema_version_on_stdout():
    client = _Client({"value": [_run_item()]})
    result = run_list(_Cmd(), "rg", "site", "wf", client=client)
    assert "schemaVersion" not in result
    assert "schemaVersion" not in result["value"][0]


# ---- run show ----------------------------------------------------------


def test_run_show_calls_singular_route_and_hoists_workflow_version():
    client = _Client(_run_item())
    result = run_show(_Cmd(), "rg", "site", "wf", "run1", client=client)

    assert client.calls == [("get", "workflows/wf/runs/run1", None)]
    assert result["runId"] == "run1"
    assert result["workflow"] == "wf"
    assert result["workflowVersion"] == "08584129710997436905"
    assert result["status"] == "Succeeded"


def test_run_show_synthesised_uses_projection_provenance_shape_not_paging_shape():
    client = _Client(_run_item())
    result = run_show(_Cmd(), "rg", "site", "wf", "run1", client=client)
    synth = result["synthesised"]
    assert synth["fields"] == ["workflow", "workflowVersion"]
    assert "serverSidePaging" not in synth
    assert "clientSidePaging" not in synth
    assert "workflowVersion" in synth["reason"]


def test_run_show_redacts_sas_bearing_uris_by_default_and_opts_in():
    client = _Client(_run_item())
    result = run_show(_Cmd(), "rg", "site", "wf", "run1", client=client)
    assert result["trigger"]["inputsLink"]["uri"] == LOGICAPP_REDACTION_SENTINEL

    client2 = _Client(_run_item())
    result2 = run_show(_Cmd(), "rg", "site", "wf", "run1",
                       show_content_urls=True, client=client2)
    assert result2["trigger"]["inputsLink"]["uri"] == _SAS_URI


def test_run_show_echoes_run_id_when_platform_omits_it():
    client = _Client({"properties": {"status": "Succeeded"}})
    result = run_show(_Cmd(), "rg", "site", "wf", "given-id", client=client)
    assert result["runId"] == "given-id"


def test_run_show_returns_null_workflow_version_when_platform_omits_workflow_object():
    client = _Client({"name": "run1", "properties": {"status": "Succeeded"}})
    result = run_show(_Cmd(), "rg", "site", "wf", "run1", client=client)
    assert result["workflowVersion"] is None


def test_run_show_passes_correlation_through_verbatim_without_synthesised_schema_version():
    """The prototype minted a `correlation.schemaVersion` field. Engineer ruling:
    strip that. The three platform siblings stay -- clientTrackingId,
    clientKeywords, correlationId -- and no CLI-minted schemaVersion is added."""
    payload = _run_item()
    payload["properties"]["correlation"] = {
        "clientTrackingId": "trk",
        "clientKeywords": "kw",
    }
    payload["properties"]["correlationId"] = "cor-1"
    client = _Client(payload)
    result = run_show(_Cmd(), "rg", "site", "wf", "run1", client=client)

    assert result["correlation"] == {"clientTrackingId": "trk", "clientKeywords": "kw"}
    assert result["correlationId"] == "cor-1"
    # Critically, no CLI-minted schemaVersion inside correlation.
    assert "schemaVersion" not in result["correlation"]


def test_run_show_emits_no_top_level_schema_version_on_stdout():
    client = _Client(_run_item())
    result = run_show(_Cmd(), "rg", "site", "wf", "run1", client=client)
    assert "schemaVersion" not in result


def test_run_show_url_encodes_run_id_segment():
    client = _Client(_run_item())
    run_show(_Cmd(), "rg", "site", "wf", "runs/with/slash", client=client)
    assert client.calls[0][1] == "workflows/wf/runs/runs%2Fwith%2Fslash"

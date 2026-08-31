"""Unit tests for the workflow site-runtime client seam."""

import pytest

from azure.cli.core.azclierror import HTTPError, ResourceNotFoundError
from azure.cli.core.mock import DummyCli
from azure.cli.command_modules.appservice.logicapp._runtime_client import (
    SiteRuntimeClient,
    action_repetitions_path,
    action_request_histories_path,
    generate_unit_test_path,
    mockable_operations_path,
    run_action_path,
    run_actions_path,
    trigger_callback_url_path,
    trigger_histories_path,
    trigger_history_path,
    trigger_history_resubmit_path,
    trigger_run_path,
    trigger_schema_path,
    workflow_run_path,
    workflow_runs_path,
    workflow_trigger_path,
    workflow_triggers_path,
)


_SITE_ID = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Web/sites/site"


class _Cmd:
    cli_ctx = DummyCli()


class _Response:
    content = b"{}"

    def __init__(self, payload, headers=None):
        self._payload = payload
        self.headers = headers or {}
        self.content = b"x" if payload is not None else b""

    def json(self):
        return self._payload


class _HttpResponse:
    status_code = 404


def test_runtime_client_builds_arm_hostruntime_management_url_and_uses_raw_sender():
    calls = []

    def sender(cli_ctx, method, url, headers=None, body=None):
        calls.append((cli_ctx, method, url, headers, body))
        return _Response({"value": []})

    client = SiteRuntimeClient(_Cmd(), _SITE_ID, sender=sender)
    result = client.get(workflow_runs_path("wf one"), params={"$expand": "properties/actions"})

    assert result == {"value": []}
    _, method, url, headers, body = calls[0]
    assert method == "GET"
    assert url.startswith(_SITE_ID + "/hostruntime/runtime/webhooks/workflow/api/management/workflows/wf%20one/runs?")
    assert "api-version=2018-11-01" in url
    assert "%24expand=properties%2Factions" in url
    assert headers == ["Content-Type=application/json"]
    assert body is None


def test_runtime_client_route_helpers_are_m1_management_paths():
    assert workflow_run_path("wf", "run") == "workflows/wf/runs/run"
    assert run_actions_path("wf", "run") == "workflows/wf/runs/run/actions"
    assert run_action_path("wf", "run", "A B") == "workflows/wf/runs/run/actions/A%20B"
    assert action_repetitions_path("wf", "run", "A") == "workflows/wf/runs/run/actions/A/repetitions"
    assert action_repetitions_path("wf", "run", "A", scope=True) == "workflows/wf/runs/run/actions/A/scopeRepetitions"
    assert action_request_histories_path("wf", "run", "A") == "workflows/wf/runs/run/actions/A/requestHistories"
    assert action_request_histories_path("wf", "run", "A", repetition="000000") == (
        "workflows/wf/runs/run/actions/A/repetitions/000000/requestHistories"
    )


def test_runtime_client_route_helpers_are_m2_management_paths():
    assert workflow_triggers_path("wf") == "workflows/wf/triggers"
    assert workflow_trigger_path("wf/name", "manual trigger") == "workflows/wf%2Fname/triggers/manual%20trigger"
    assert trigger_run_path("wf", "manual") == "workflows/wf/triggers/manual/run"
    assert trigger_callback_url_path("wf", "manual") == "workflows/wf/triggers/manual/listCallbackUrl"
    assert trigger_schema_path("wf", "manual") == "workflows/wf/triggers/manual/schemas/json"
    assert trigger_histories_path("wf", "manual") == "workflows/wf/triggers/manual/histories"
    assert trigger_history_path("wf", "manual", "hist 1") == "workflows/wf/triggers/manual/histories/hist%201"
    assert trigger_history_resubmit_path("wf", "manual", "hist") == "workflows/wf/triggers/manual/histories/hist/resubmit"
    assert mockable_operations_path() == "listMockableOperations"
    assert mockable_operations_path(http_only=True) == "listMockableHttpOperations"
    assert generate_unit_test_path("wf", "run") == "workflows/wf/runs/run/generateUnitTest"


def test_runtime_client_can_build_non_management_runtime_url():
    calls = []

    def sender(cli_ctx, method, url, headers=None, body=None):
        calls.append((method, url, headers, body))
        return _Response({"ok": True})

    client = SiteRuntimeClient(_Cmd(), _SITE_ID, sender=sender)
    result = client.get_runtime("diagnostics/ping")

    assert result == {"ok": True}
    method, url, headers, body = calls[0]
    assert method == "GET"
    assert url.startswith(_SITE_ID + "/hostruntime/runtime/webhooks/workflow/diagnostics/ping?")
    assert "api-version=2018-11-01" in url
    assert headers == ["Content-Type=application/json"]
    assert body is None


def test_runtime_client_get_raw_url_follows_uri_without_authorization_header():
    calls = []

    def sender(cli_ctx, method, url, headers=None, body=None, **kwargs):
        calls.append((method, url, headers, body, kwargs))
        return _Response({"ignored": True})

    client = SiteRuntimeClient(_Cmd(), _SITE_ID, sender=sender)
    client.get_raw_url("https://example.invalid/content?code=secret&sig=signature")

    method, url, headers, body, kwargs = calls[0]
    assert method == "GET"
    assert url == "https://example.invalid/content?code=secret&sig=signature"
    assert headers == ["Content-Type=application/json"]
    assert body is None
    assert kwargs["skip_authorization_header"] is True


def test_runtime_client_applies_client_side_continuation_and_max_items():
    def sender(*_args, **_kwargs):
        return _Response({"value": [{"name": "0"}, {"name": "1"}, {"name": "2"}]})

    client = SiteRuntimeClient(_Cmd(), _SITE_ID, sender=sender)
    first = client.list("workflows/w/runs", max_items=2)
    second = client.list("workflows/w/runs", continuation_token=first["nextContinuationToken"], max_items=2)

    assert [item["name"] for item in first["value"]] == ["0", "1"]
    assert [item["name"] for item in second["value"]] == ["2"]
    assert second["nextContinuationToken"] is None


def test_runtime_client_maps_404_to_resource_not_found_without_refusal_marker():
    def sender(*_args, **_kwargs):
        raise HTTPError("Not Found", _HttpResponse())

    client = SiteRuntimeClient(_Cmd(), _SITE_ID, sender=sender)
    with pytest.raises(ResourceNotFoundError) as exc_info:
        client.get("workflows/missing/runs/missing")
    assert "AZ_LOGICAPP_REFUSAL" not in str(exc_info.value)


def test_runtime_client_post_with_headers_returns_empty_body_headers():
    def sender(*_args, **_kwargs):
        return _Response(None, headers={"x-ms-workflow-run-id": "run2"})

    client = SiteRuntimeClient(_Cmd(), _SITE_ID, sender=sender)
    result = client.post_with_headers("workflows/wf/triggers/manual/histories/run1/resubmit")

    assert result == {"payload": None, "headers": {"x-ms-workflow-run-id": "run2"}}

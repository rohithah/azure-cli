# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long
"""Site-runtime client for Logic Apps Standard workflow run commands."""

import base64
import json
from urllib.parse import quote, urlencode

from azure.cli.core.azclierror import HTTPError, ResourceNotFoundError
from azure.cli.core.util import send_raw_request

DEFAULT_SITE_RUNTIME_API_VERSION = "2018-11-01"
_MANAGEMENT_PREFIX = "hostruntime/runtime/webhooks/workflow/api/management"
_RUNTIME_PREFIX = "hostruntime/runtime/webhooks/workflow"


def _quote_segment(value):
    return quote(str(value), safe="")


def workflow_runs_path(workflow):
    return "workflows/{}/runs".format(_quote_segment(workflow))


def workflow_triggers_path(workflow):
    return "workflows/{}/triggers".format(_quote_segment(workflow))


def workflow_trigger_path(workflow, trigger):
    return "{}/{}".format(workflow_triggers_path(workflow), _quote_segment(trigger))


def trigger_run_path(workflow, trigger):
    return "{}/run".format(workflow_trigger_path(workflow, trigger))


def trigger_callback_url_path(workflow, trigger):
    return "{}/listCallbackUrl".format(workflow_trigger_path(workflow, trigger))


def trigger_schema_path(workflow, trigger):
    return "{}/schemas/json".format(workflow_trigger_path(workflow, trigger))


def trigger_histories_path(workflow, trigger):
    return "{}/histories".format(workflow_trigger_path(workflow, trigger))


def trigger_history_path(workflow, trigger, history):
    return "{}/{}".format(trigger_histories_path(workflow, trigger), _quote_segment(history))


def trigger_history_resubmit_path(workflow, trigger, history):
    return "{}/resubmit".format(trigger_history_path(workflow, trigger, history))


def mockable_operations_path(http_only=False):
    return "listMockableHttpOperations" if http_only else "listMockableOperations"


def generate_unit_test_path(workflow, run_id):
    return "{}/generateUnitTest".format(workflow_run_path(workflow, run_id))


def workflow_run_path(workflow, run_id):
    return "{}/{}".format(workflow_runs_path(workflow), _quote_segment(run_id))


def run_actions_path(workflow, run_id):
    return "{}/actions".format(workflow_run_path(workflow, run_id))


def run_action_path(workflow, run_id, action):
    return "{}/{}".format(run_actions_path(workflow, run_id), _quote_segment(action))


def action_repetitions_path(workflow, run_id, action, scope=False):
    segment = "scopeRepetitions" if scope else "repetitions"
    return "{}/{}".format(run_action_path(workflow, run_id, action), segment)


def action_request_histories_path(workflow, run_id, action, repetition=None):
    base = run_action_path(workflow, run_id, action)
    if repetition is not None:
        base = "{}/repetitions/{}".format(base, _quote_segment(repetition))
    return "{}/requestHistories".format(base)


class SiteRuntimeClient:
    """Thin ARM-hostruntime proxy client shared by the workflow run and action commands.

    Public seam for command agents:
    - ``SiteRuntimeClient(cmd, site_resource_id, api_version=DEFAULT_SITE_RUNTIME_API_VERSION)``
    - ``get(relative_path, params=None)`` / ``post(relative_path, body=None, params=None)``
    - ``get_raw_url(url, max_bytes=None)`` for service-provided contentLink URIs
    - ``list(relative_path, params=None, continuation_token=None, max_items=None)``
    - route helpers above for workflow run/action paths.
    """

    def __init__(self, cmd, site_resource_id, api_version=DEFAULT_SITE_RUNTIME_API_VERSION, sender=None, not_found_as_resource=True):
        self.cmd = cmd
        self.site_resource_id = _normalize_site_resource_id(site_resource_id)
        self.api_version = api_version
        self._sender = sender or send_raw_request
        self._not_found_as_resource = not_found_as_resource

    def get(self, relative_path, params=None):
        return self.request("GET", relative_path, params=params)

    def post(self, relative_path, body=None, params=None):
        return self.request("POST", relative_path, body=body, params=params)

    def post_with_headers(self, relative_path, body=None, params=None):
        return self.request_with_headers("POST", relative_path, body=body, params=params)

    def post_raw(self, relative_path, body=None, params=None):
        """POST returning the raw response body.

        Routed through the same sender as every other request so a 404 is
        translated to ``ResourceNotFoundError``; callers that reach the sender
        directly bypass that translation and surface a not-found as exit 1.
        """
        response = self._send("POST", self._build_url(relative_path, params=params), body=body)
        return getattr(response, "content", b"") or b""

    def get_raw_url(self, url, max_bytes=None):
        headers = ["Content-Type=application/json"]
        if max_bytes is not None:
            headers.append("Range=bytes=0-{}".format(int(max_bytes) - 1))
        response = self._sender(self.cmd.cli_ctx, "GET", url, headers=headers, skip_authorization_header=True)
        return {"content": getattr(response, "content", b""), "headers": getattr(response, "headers", {})}

    def list(self, relative_path, params=None, continuation_token=None, max_items=None):
        payload = self.get(relative_path, params=params)
        items = _extract_items(payload)
        page, next_token = _slice_items(items, continuation_token=continuation_token, max_items=max_items)
        if isinstance(payload, list):
            return {"value": page, "nextContinuationToken": next_token}
        result = dict(payload or {})
        result["value"] = page
        result["nextContinuationToken"] = next_token
        return result

    def request(self, method, relative_path, body=None, params=None):
        return self.request_with_headers(method, relative_path, body=body, params=params)["payload"]

    def get_runtime(self, relative_path, params=None):
        return self.request_runtime("GET", relative_path, params=params)

    def request_runtime(self, method, relative_path, body=None, params=None):
        return self.request_with_headers(method, relative_path, body=body, params=params, prefix=_RUNTIME_PREFIX)["payload"]

    def request_with_headers(self, method, relative_path, body=None, params=None, prefix=_MANAGEMENT_PREFIX):
        url = self._build_url(relative_path, params=params, prefix=prefix)
        response = self._send(method, url, body=body)
        payload = response.json() if getattr(response, "content", None) else None
        return {"payload": payload, "headers": getattr(response, "headers", {}) or {}}

    def _send(self, method, url, body=None):
        serialized = json.dumps(body) if body is not None else None
        try:
            return self._sender(
                self.cmd.cli_ctx,
                method,
                url,
                headers=["Content-Type=application/json"],
                body=serialized,
            )
        except HTTPError as ex:
            status_code = getattr(getattr(ex, "response", None), "status_code", None)
            if status_code == 404 and self._not_found_as_resource:
                raise ResourceNotFoundError(str(ex)) from ex
            raise

    def _build_url(self, relative_path, params=None, prefix=_MANAGEMENT_PREFIX):
        path = str(relative_path).strip("/")
        query = {"api-version": self.api_version}
        if params:
            query.update({key: value for key, value in params.items() if value is not None})
        return "{}/{}/{}?{}".format(
            self.site_resource_id.rstrip("/"),
            prefix,
            path,
            urlencode(query, doseq=True),
        )


def _normalize_site_resource_id(site_resource_id):
    value = str(site_resource_id or "").strip()
    if not value.startswith("/subscriptions/"):
        raise ValueError("site_resource_id must be a Microsoft.Web/sites ARM resource id")
    if "/providers/Microsoft.Web/sites/" not in value:
        raise ValueError("site_resource_id must target a Microsoft.Web/sites resource")
    return value.rstrip("/")


def _extract_items(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        value = payload.get("value")
        if isinstance(value, list):
            return value
    raise ValueError("site-runtime list response must be an array or an object with a value array")


def _encode_continuation(offset):
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_continuation(token):
    if token in (None, ""):
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
        offset = int(data["offset"])
    except (ValueError, KeyError, TypeError) as ex:
        raise ValueError("continuation-token is not a valid Logic Apps site-runtime continuation token") from ex
    if offset < 0:
        raise ValueError("continuation-token offset must be non-negative")
    return offset


def _slice_items(items, continuation_token=None, max_items=None):
    start = _decode_continuation(continuation_token)
    if max_items is None:
        return items[start:], None
    count = int(max_items)
    if count <= 0:
        raise ValueError("max-items must be greater than zero")
    end = start + count
    next_token = _encode_continuation(end) if end < len(items) else None
    return items[start:end], next_token

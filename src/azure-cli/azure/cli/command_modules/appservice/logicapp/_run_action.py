# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long

"""Workflow run-action command bodies for Logic Apps Standard.

Ships the three read-only ``run action`` leaves in this pass: ``list``, ``show``, and
``show-content``. Two additional leaves (``show-retry`` and ``list-repetitions``) are
being captured under a separate task; this module is structured so those bodies can be
added alongside without churn -- each leaf here holds its own manifest constant and its
own body function, and the shared helpers below are already shape-agnostic to
repetition/retry payloads.

Divergences from the superseded extension prototype
(``tools/logicapps/cli-ext/azext_logicapp/_run_action.py``):

1. **Direct route consumption.** The prototype called ``client.get(run_route,
   params={"$expand": "properties/actions"})`` and pulled the ``value`` array out of the
   expanded run. Live capture at ``scratch/specDemo/run-action-list.json`` shows the
   platform ships a dedicated ``workflows/{workflow}/runs/{runId}/actions`` route that
   returns ``{value: [...]}`` directly. This module hits that route unchanged --
   avoiding the tell-me-everything expansion and one layer of shape juggling.
2. **SAS-bearing content-link URIs redacted by default.** ``run action list`` and
   ``run action show`` responses carry SAS URIs on ``properties.inputsLink`` and
   ``properties.outputsLink`` for every action; see live captures. Redaction reuses the
   trigger-history primitive; opt in via ``--show-content-urls``.
3. **No ``schemaVersion`` on stdout.** Manifest rows carry the identifier; stdout does
   not. Guarded by ``test_logicapp_workflow_stdout_schema_version``.
4. **``correlation.schemaVersion`` is not synthesised.** The prototype minted it; the
   engineer ruling declines that. ``correlation`` is passed through verbatim (which
   keeps the three platform siblings ``clientTrackingId``, ``clientKeywords``, and
   ``correlationId``).
"""

from ._constants import LOGICAPP_REDACTION_SENTINEL
from ._refusal_seam import REFUSAL_KIND_DESIGN, emit_refusal
from ._run import _redact_deep
from ._runtime_client import (
    SiteRuntimeClient,
    run_action_path,
    run_actions_path,
)

from azure.cli.core.commands.client_factory import get_subscription_id

RUN_ACTION_LIST_SCHEMA_DOCUMENT_VERSION = "logicapp.run-action-list-2026-08-29"
RUN_ACTION_SCHEMA_DOCUMENT_VERSION = "logicapp.run-action-2026-08-29"
RUN_ACTION_CONTENT_SCHEMA_DOCUMENT_VERSION = "logicapp.run-action-content-2026-08-29"
_SITE_PROVIDER = "/providers/Microsoft.Web/sites/"
_PREVIEW_BYTES = 256
_TRUNCATION_MARKER = "...[truncated]"


RUN_ACTION_LIST_MANIFEST = {
    "capabilityId": "CM-003",
    "command": "logicapp workflow run action list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": RUN_ACTION_LIST_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": True,
    "delegatedTo": None,
    "gap": (
        "The site-runtime run-actions route returns one collection with no observed nextLink; "
        "--max-items and --next-token are applied by the CLI after reading that collection."
    ),
    "modeCondition": None,
    "contentOnly": False,
}

RUN_ACTION_SHOW_MANIFEST = {
    "capabilityId": "CM-003-show",
    "command": "logicapp workflow run action show",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": RUN_ACTION_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": True,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}

RUN_ACTION_SHOW_CONTENT_MANIFEST = {
    "capabilityId": "CM-004",
    "command": "logicapp workflow run action show-content",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": RUN_ACTION_CONTENT_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": (
        "Content is obtained by following the platform-provided inputsLink/outputsLink URI exactly; "
        "the CLI refuses only when the corresponding link URI is absent."
    ),
    "modeCondition": None,
    "contentOnly": True,
}


def run_action_list(cmd, resource_group_name, name, workflow, run_id,
                    max_items=None, next_token=None, show_content_urls=False, client=None):
    """List one run's actions.

    Uses the dedicated ``workflows/{workflow}/runs/{runId}/actions`` route.
    Client-side paging is applied over the returned collection because no ``nextLink``
    has been observed on this route in live captures.
    """
    client = client or _client(cmd, resource_group_name, name)
    payload = client.list(run_actions_path(workflow, run_id),
                          continuation_token=next_token, max_items=max_items)
    items = [_action_response(item, workflow, run_id, show_content_urls) for item in _value(payload)]
    return {
        "value": items,
        "nextContinuationToken": payload.get("nextContinuationToken") if isinstance(payload, dict) else None,
        "synthesised": {
            "fields": ["value[].workflow", "value[].runId", "nextContinuationToken"],
            "clientSidePaging": True,
            "reason": (
                "workflow and runId are CLI inputs echoed onto each row. actionName is copied "
                "verbatim from the platform resource name. Content-link URIs on inputsLink and "
                "outputsLink are SAS-bearing and are withheld by default; pass --show-content-urls "
                "to emit them in full. The site-runtime run-actions route returns one collection; "
                "--max-items and --next-token are applied by the CLI after reading that collection."
            ),
        },
    }


def run_action_show(cmd, resource_group_name, name, workflow, run_id, action,
                    show_content_urls=False, client=None):
    """Show one action on a run."""
    client = client or _client(cmd, resource_group_name, name)
    payload = client.get(run_action_path(workflow, run_id, action))
    result = _action_response(payload, workflow, run_id, show_content_urls)
    result["synthesised"] = {
        "fields": ["workflow", "runId"],
        "reason": (
            "workflow and runId are CLI inputs echoed at the top level. actionName is copied "
            "verbatim from the platform resource name. Content-link URIs on inputsLink and "
            "outputsLink are SAS-bearing and are withheld by default; pass --show-content-urls "
            "to emit them in full."
        ),
    }
    return result


def run_action_show_content(cmd, resource_group_name, name, workflow, run_id, action, content, client=None):
    """Fetch the raw ``inputs`` or ``outputs`` content for one action.

    Follows ``properties.{inputs|outputs}Link.uri`` on the platform action payload
    exactly, without adding an Authorization header (the URI is SAS-bearing on the
    wire). Refuses when the corresponding link URI is absent -- same shape as
    ``trigger history show-inputs`` / ``show-outputs``.
    """
    if content not in ("inputs", "outputs"):
        raise ValueError("--content must be one of 'inputs' or 'outputs'")
    client = client or _client(cmd, resource_group_name, name)
    payload = client.get(run_action_path(workflow, run_id, action))
    props = _properties(payload or {})
    link = _field(props, content + "Link")
    uri = link.get("uri") if isinstance(link, dict) else None
    if not uri:
        capability_id = "CM-004-{}".format(content)
        emit_refusal(
            kind=REFUSAL_KIND_DESIGN,
            capability_id=capability_id,
            feasibility_state="refused",
            gap="The action payload does not include a {}Link.uri content pointer, so there is no "
                "platform-provided pre-authorized URI to follow.".format(content),
            remedy=("Run 'az logicapp workflow run action show' for the action and retry with "
                    "show-content --content {kind} only when the corresponding {kind}Link.uri "
                    "is present.").format(kind=content),
        )

    raw_content = client.get_raw_url(uri)
    content_bytes = raw_content.get("content") or b""
    content_type = _header(raw_content.get("headers"), "content-type") or (link.get("contentType") if isinstance(link, dict) else None)
    total_size = _content_size(raw_content.get("headers"), link, len(content_bytes))
    body = _deserialize_content(content_bytes)
    preview, truncated = _preview(content_bytes, total_size)
    return {
        "workflow": workflow,
        "runId": run_id,
        "action": action,
        "contentName": content,
        "content": body,
        "contentSizeBytes": total_size,
        "contentType": content_type,
        "preview": preview,
        "truncated": truncated,
        "synthesised": (
            "workflow, runId, action, and contentName are CLI inputs; contentSizeBytes, preview, "
            "and truncated are CLI-computed from the returned content; content was read by following "
            "the platform-provided {}Link.uri exactly.".format(content)
        ),
    }


def run_action_list_table_format(result):
    if not result:
        return []
    return [{
        "actionName": item.get("actionName"),
        "workflow": item.get("workflow"),
        "runId": item.get("runId"),
        "status": item.get("status"),
        "startTime": item.get("startTime"),
        "endTime": item.get("endTime"),
    } for item in result.get("value", [])]


def run_action_show_table_format(result):
    if not result:
        return []
    return [{
        "actionName": result.get("actionName"),
        "workflow": result.get("workflow"),
        "runId": result.get("runId"),
        "status": result.get("status"),
        "startTime": result.get("startTime"),
        "endTime": result.get("endTime"),
    }]


def run_action_show_content_table_format(result):
    if not result:
        return []
    return [{
        "workflow": result.get("workflow"),
        "runId": result.get("runId"),
        "action": result.get("action"),
        "contentName": result.get("contentName"),
        "contentSizeBytes": result.get("contentSizeBytes"),
        "contentType": result.get("contentType"),
        "truncated": result.get("truncated"),
    }]


# ------------------------------- helpers -------------------------------


def _client(cmd, resource_group_name, name):
    subscription_id = get_subscription_id(cmd.cli_ctx)
    return SiteRuntimeClient(
        cmd,
        "/subscriptions/{}/resourceGroups/{}{}{}".format(
            subscription_id, resource_group_name, _SITE_PROVIDER, name))


def _action_response(raw, workflow, run_id, show_content_urls):
    props = _properties(raw or {})
    return {
        "actionName": (raw or {}).get("name") or _field(props, "name"),
        "workflow": workflow,
        "runId": run_id,
        "status": _field(props, "status") or (raw or {}).get("status"),
        "code": _field(props, "code") or (raw or {}).get("code"),
        "error": _field(props, "error") or (raw or {}).get("error"),
        "startTime": _field(props, "startTime"),
        "endTime": _field(props, "endTime"),
        "correlation": _field(props, "correlation"),
        "trackedProperties": _field(props, "trackedProperties"),
        "retryHistory": _field(props, "retryHistory"),
        "canResubmit": _field(props, "canResubmit"),
        "inputsLink": _apply_link_redaction(_field(props, "inputsLink"), show_content_urls),
        "outputsLink": _apply_link_redaction(_field(props, "outputsLink"), show_content_urls),
    }


def _apply_link_redaction(link, show_content_urls):
    if show_content_urls or link is None or not isinstance(link, dict):
        return link
    if "uri" not in link:
        return link
    redacted = dict(link)
    redacted["uri"] = LOGICAPP_REDACTION_SENTINEL
    # Preserve non-secret descriptive fields (contentSize, contentType, contentHash),
    # matching the trigger-history redaction convention. Reuse ``_redact_deep`` to keep
    # a single write-path for the sentinel and cover the theoretical case where a
    # ``*Link`` nested field carries its own credential.
    return _redact_deep({"__link__": redacted})["__link__"]


def _properties(raw):
    if not isinstance(raw, dict):
        return {}
    props = raw.get("properties")
    return props if isinstance(props, dict) else raw


def _field(source, name, default=None):
    if not isinstance(source, dict):
        return default
    if name in source:
        return source[name]
    lowered = name.lower()
    for key, value in source.items():
        if str(key).lower() == lowered:
            return value
    return default


def _value(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("value") or []
    return []


def _deserialize_content(content_bytes):
    import json
    if not content_bytes:
        return None
    text = content_bytes.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _preview(content_bytes, total_size):
    truncated = total_size > _PREVIEW_BYTES or len(content_bytes) > _PREVIEW_BYTES
    visible = content_bytes[:_PREVIEW_BYTES].decode("utf-8", errors="replace")
    if truncated:
        visible += _TRUNCATION_MARKER
    return visible, truncated


def _content_size(headers, link, fallback):
    import re
    content_range = _header(headers, "content-range")
    if content_range:
        match = re.search(r"/(\d+)$", content_range)
        if match:
            return int(match.group(1))
    content_length = _header(headers, "content-length")
    if content_length:
        try:
            return int(content_length)
        except ValueError:
            pass
    for key in ("contentSize", "contentLength"):
        if isinstance(link, dict) and link.get(key) is not None:
            try:
                return int(link[key])
            except (TypeError, ValueError):
                pass
    return fallback


def _header(headers, name):
    if not headers:
        return None
    lowered = name.lower()
    for key, value in dict(headers).items():
        if str(key).lower() == lowered:
            return value
    return None

# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long

"""Workflow trigger history command bodies for Logic Apps Standard.

Split note: the extension's ``show-content --content-name`` command becomes two
commands in core, ``show-inputs`` and ``show-outputs``. The per-direction
D50-compliant refusal (raised when the corresponding ``inputsLink.uri`` /
``outputsLink.uri`` is absent) is preserved by hard-coding the ``content_name``
inside each command body, not by carrying a ``--content-name`` argument forward.
"""

import json

from azure.cli.core.azclierror import AzureResponseError, ResourceNotFoundError
from azure.cli.core.commands.client_factory import get_subscription_id

from ._constants import LOGICAPP_REDACTION_SENTINEL
from ._refusal_seam import REFUSAL_KIND_DESIGN, emit_refusal
from ._runtime_client import (
    SiteRuntimeClient,
    trigger_histories_path,
    trigger_history_path,
    trigger_history_resubmit_path,
    workflow_trigger_path,
)

TRIGGER_HISTORY_SCHEMA_DOCUMENT_VERSION = "logicapp.trigger-history-2026-08-29"
TRIGGER_HISTORY_ENTRY_SCHEMA_DOCUMENT_VERSION = "logicapp.trigger-history-entry-2026-08-29"
TRIGGER_HISTORY_CONTENT_SCHEMA_DOCUMENT_VERSION = "logicapp.trigger-history-content-2026-08-29"
TRIGGER_HISTORY_RESUBMIT_SCHEMA_DOCUMENT_VERSION = "logicapp.trigger-history-resubmit-2026-08-29"
_SITE_PROVIDER = "/providers/Microsoft.Web/sites/"


TRIGGER_HISTORY_LIST_MANIFEST = {
    "capabilityId": "CM-011",
    "command": "logicapp workflow trigger history list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_HISTORY_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": True,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}

TRIGGER_HISTORY_SHOW_MANIFEST = {
    "capabilityId": "CM-012-show",
    "command": "logicapp workflow trigger history show",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_HISTORY_ENTRY_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": True,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}

TRIGGER_HISTORY_SHOW_INPUTS_MANIFEST = {
    "capabilityId": "CM-012-show-inputs",
    "command": "logicapp workflow trigger history show-inputs",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_HISTORY_CONTENT_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": (
        "Content is obtained by following the platform-provided history entry inputsLink URI "
        "exactly; the CLI refuses only when the inputsLink URI is absent."
    ),
    "modeCondition": None,
    "contentOnly": True,
}

TRIGGER_HISTORY_SHOW_OUTPUTS_MANIFEST = {
    "capabilityId": "CM-012-show-outputs",
    "command": "logicapp workflow trigger history show-outputs",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_HISTORY_CONTENT_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": (
        "Content is obtained by following the platform-provided history entry outputsLink URI "
        "exactly; the CLI refuses only when the outputsLink URI is absent."
    ),
    "modeCondition": None,
    "contentOnly": True,
}

TRIGGER_HISTORY_RESUBMIT_MANIFEST = {
    "capabilityId": "CM-017",
    "command": "logicapp workflow trigger history resubmit",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_HISTORY_RESUBMIT_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": (
        "Server accepts one historyName per resubmit; CLI loops over one-or-more "
        "--history-ids values and reports an aggregated outcome. Multi-ID server support remains "
        "unresolved pending live observation."
    ),
    "modeCondition": None,
    "contentOnly": False,
}


def trigger_history_list(cmd, resource_group_name, name, workflow, trigger, max_items=None, next_token=None, show_content_urls=False, client=None):
    client = client or _client(cmd, resource_group_name, name)
    payload = client.list(trigger_histories_path(workflow, trigger), params={"$expand": "run/properties"}, continuation_token=next_token, max_items=max_items)
    items = [_apply_content_link_redaction(_history_response(item, workflow, trigger), show_content_urls)
             for item in _value(payload)]
    fields = ["value[].workflow", "value[].trigger", "value[].historyId", "value[].workflowVersion"]
    run_id_synthesised = [item.pop("_runIdSynthesised", False) for item in items]
    if any(run_id_synthesised):
        fields.append("value[].runId")
    return {
        "value": items,
        "nextLink": payload.get("nextLink") if isinstance(payload, dict) else None,
        "nextContinuationToken": payload.get("nextContinuationToken") if isinstance(payload, dict) else None,
        "synthesised": {
            "fields": fields + ["nextContinuationToken"],
            "clientSidePaging": True,
            "reason": (
                "workflow and trigger are CLI inputs; historyId is projected from the platform "
                "resource name. runId is listed only when projected from an inline platform run "
                "reference. workflowVersion is copied verbatim from the platform's nested "
                "run.properties.workflow object, never computed or inferred, and is null when the "
                "platform does not return that object. The site-runtime trigger histories route "
                "returns one collection; --max-items and --next-token are applied by the CLI after "
                "reading that collection."
            ),
        },
    }


def trigger_history_show(cmd, resource_group_name, name, workflow, trigger, history_id, show_content_urls=False, client=None):
    client = client or _client(cmd, resource_group_name, name)
    payload = client.get(trigger_history_path(workflow, trigger, history_id), params={"$expand": "run/properties"})
    result = _apply_content_link_redaction(_history_response(payload, workflow, trigger), show_content_urls)
    run_id_synthesised = result.pop("_runIdSynthesised", False)
    if not result.get("historyId"):
        result["historyId"] = history_id
    fields = ["workflow", "trigger"]
    if result.get("historyId") == history_id:
        fields.append("historyId")
    if run_id_synthesised:
        fields.append("runId")
    # workflowVersion is always CLI-added at the top level -- the platform only
    # ever reports it nested inside the run object -- so it is declared whether
    # or not the platform supplied a value.
    fields.append("workflowVersion")
    result["synthesised"] = {
        "fields": fields,
        "reason": (
            "workflow and trigger are CLI inputs; historyId is projected from the platform resource "
            "name or CLI input. runId is listed only when projected from an inline platform run reference. "
            "workflowVersion is copied verbatim from the platform's nested run.properties.workflow object, "
            "never computed or inferred, and is null when the platform does not return that object. The "
            "singular history route can omit it even though the CLI requests $expand=run/properties; when "
            "that happens, 'az logicapp workflow trigger history list' does return the executed version "
            "for the same entry."
        ),
    }
    return result


def trigger_history_show_inputs(cmd, resource_group_name, name, workflow, trigger, history_id, client=None):
    return _show_content(cmd, resource_group_name, name, workflow, trigger, history_id, "inputs", client=client)


def trigger_history_show_outputs(cmd, resource_group_name, name, workflow, trigger, history_id, client=None):
    return _show_content(cmd, resource_group_name, name, workflow, trigger, history_id, "outputs", client=client)


def trigger_history_resubmit(cmd, resource_group_name, name, workflow, trigger, history_ids, client=None):
    client = client or _client(cmd, resource_group_name, name)
    ids = history_ids if isinstance(history_ids, list) else [history_ids]
    outcomes = []
    failures = []
    saw_not_found = False
    for item in ids:
        try:
            response = client.post_with_headers(trigger_history_resubmit_path(workflow, trigger, item))
            outcome = _resubmit_outcome(item, response.get("payload"), response.get("headers"))
            outcomes.append(outcome)
            if _is_failure_outcome(outcome):
                failures.append(outcome)
        except Exception as ex:  # pylint: disable=broad-except
            if isinstance(ex, ResourceNotFoundError):
                saw_not_found = True
            outcome = {
                "historyId": item,
                "runId": None,
                "status": "Failed",
                "message": "Resubmit failed for history '{}': {}".format(item, ex),
            }
            outcomes.append(outcome)
            failures.append(outcome)
    if saw_not_found:
        # A 404 on the resubmit route means either the history entry is missing
        # -- a per-entry outcome that belongs in the aggregate -- or the whole
        # target is missing. Read the trigger to tell them apart so a missing
        # workflow or trigger keeps az's exit 3 instead of being flattened into
        # an aggregate failure.
        client.get(workflow_trigger_path(workflow, trigger))
    result = _resubmit_result(outcomes)
    if failures:
        raise AzureResponseError("Trigger history resubmit completed with {} failure(s): {}".format(len(failures), json.dumps(result, separators=(",", ":"))))
    return result


def trigger_history_table_format(result):
    if not result:
        return []
    return result.get("value", [])


def trigger_history_entry_table_format(result):
    if not result:
        return []
    return [result]


def _show_content(cmd, resource_group_name, name, workflow, trigger, history_id, content_name, client=None):
    client = client or _client(cmd, resource_group_name, name)
    # show_content_urls=True because this call is internal: the content link is
    # followed here rather than printed, so redacting it would make the fetch
    # target the sentinel string instead of the platform URI.
    history = trigger_history_show(cmd, resource_group_name, name, workflow, trigger, history_id,
                                   show_content_urls=True, client=client)
    link = _content_link_or_refuse(history, content_name)
    raw_content = client.get_raw_url(link["uri"])
    content_bytes = raw_content.get("content") or b""
    content = _deserialize_content(content_bytes)
    content_type = _header(raw_content.get("headers"), "content-type") or link.get("contentType")
    total_size = _content_size(raw_content.get("headers"), link, len(content_bytes))
    preview, truncated = _preview(content_bytes, total_size)
    return {
        "workflow": workflow,
        "trigger": trigger,
        "historyId": history_id,
        "contentName": content_name,
        "content": content,
        "contentSizeBytes": total_size,
        "contentType": content_type,
        "preview": preview,
        "truncated": truncated,
        "synthesised": (
            "workflow, trigger, historyId, and contentName are CLI inputs; contentSizeBytes, "
            "preview, and truncated are CLI-computed from the returned content; content was "
            "read by following the platform-provided content link URI exactly."
        ),
    }


def _content_link_or_refuse(history, content_name):
    link = _content_link(history, content_name)
    uri = link.get("uri") if isinstance(link, dict) else None
    if not uri:
        capability_id = "CM-012-show-{}".format(content_name)
        emit_refusal(
            kind=REFUSAL_KIND_DESIGN,
            capability_id=capability_id,
            feasibility_state="refused",
            gap="The history entry does not include a {}Link.uri content pointer, so there is no platform-provided pre-authorized URI to follow.".format(content_name),
            remedy="Run 'az logicapp workflow trigger history show' for the entry and retry with show-{name} only when the corresponding {name}Link.uri is present.".format(name=content_name),
        )
    return link


def _content_link(history, content_name):
    lowered = str(content_name or "").lower()
    preferred = _field(history, lowered + "Link")
    if preferred is not None:
        return preferred
    for key in ("inputsLink", "outputsLink"):
        link = _field(history, key)
        if isinstance(link, dict) and str(_field(link, "uri") or "").lower().rstrip("/").endswith("/contents/{}".format(lowered)):
            return link
    return None


def _resubmit_outcome(history_id, payload, headers):
    detail = _properties(payload or {}) if isinstance(payload, dict) else {}
    run_id = _field(detail, "runId") or _field(detail, "name") or _header(headers, "x-ms-workflow-run-id")
    status = _field(detail, "status") or ("ResubmitAccepted" if run_id else "ResubmitAcceptedNewRunIdUnknown")
    outcome = {
        "historyId": history_id,
        "runId": run_id,
        "status": status,
        "message": None,
    }
    if not run_id:
        outcome["message"] = "Resubmit was accepted, but the platform did not return a new run id; refusing to echo the submitted history id as a run id."
    return outcome


def _is_failure_outcome(outcome):
    status = str(outcome.get("status") or "").lower()
    return status in ("failed", "faulted", "canceled", "cancelled")


def _resubmit_result(outcomes):
    return {
        "value": outcomes,
        "synthesised": {
            "fields": ["value[]", "value[].historyId", "value[].status when no platform status is returned", "value[].message"],
            "reason": (
                "The platform resubmit route accepts one historyName; the CLI loops "
                "over one-or-more --history-ids values and aggregates per-entry outcomes. Multi-ID "
                "server support remains unresolved pending live observation."
            ),
        },
    }


def _deserialize_content(content_bytes):
    if not content_bytes:
        return None
    text = content_bytes.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _content_size(headers, link, fallback):
    content_length = _header(headers, "content-length")
    if content_length:
        try:
            return int(content_length)
        except ValueError:
            pass
    for key in ("contentSize", "contentLength"):
        value = link.get(key) if isinstance(link, dict) else None
        if value is not None:
            return int(value)
    return fallback


def _preview(content_bytes, total_size):
    truncated = total_size > 256 or len(content_bytes) > 256
    visible = content_bytes[:256].decode("utf-8", errors="replace")
    if truncated:
        visible += "...[truncated]"
    return visible, truncated


def _header(headers, name):
    if not headers:
        return None
    lowered = name.lower()
    for key, value in dict(headers).items():
        if str(key).lower() == lowered:
            return value
    return None


def _client(cmd, resource_group_name, name):
    subscription_id = get_subscription_id(cmd.cli_ctx)
    return SiteRuntimeClient(cmd, "/subscriptions/{}/resourceGroups/{}{}{}".format(subscription_id, resource_group_name, _SITE_PROVIDER, name))


def _value(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    return payload.get("value", [])


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


def _run_id(run):
    if isinstance(run, dict):
        props = _properties(run)
        value = run.get("name") or _field(props, "name") or _field(props, "runId")
        if value:
            return value
        return _run_id_from_text(run.get("id") or _field(props, "id"))
    if isinstance(run, str):
        return _run_id_from_text(run) or run
    return None


def _run_id_from_text(value):
    if isinstance(value, str) and "/runs/" in value.lower():
        return value.rstrip("/").split("/")[-1]
    return None


def _workflow_version(run):
    """Hoist the executed workflow-version identity out of the nested run object.

    The platform reports the version a run was pinned to at
    ``run.properties.workflow`` as ``{id, name, type}`` -- for example
    ``{"id": "/workflows/specDemo/versions/08584129710997436905",
       "name": "08584129710997436905", "type": "workflows/versions"}``.

    A run executes the workflow version that was current when it started, so a
    workflow redeployed after the run means the current definition is NOT the
    one that produced this history entry. Surfacing the version at the top level
    keeps that distinction visible without requiring the caller to know the
    nested shape. The value is copied from the platform payload -- never
    inferred, and ``None`` when the platform did not supply it.
    """
    props = _properties(run or {})
    workflow_ref = _field(props, "workflow")
    if not isinstance(workflow_ref, dict):
        return None
    return _field(workflow_ref, "name") or _version_from_text(_field(workflow_ref, "id"))


def _version_from_text(value):
    if isinstance(value, str) and "/versions/" in value.lower():
        return value.rstrip("/").split("/")[-1]
    return None


def _run_reference_state(props, run, run_id):
    if run_id:
        return "returned-inline"
    if run is not None:
        return "returned-inline-unparseable"
    if _field(props, "fired") is False:
        return "no-run-reference"
    return "not-returned-inline"


def _redact_content_link(link):
    """Return a content link with its pre-authorized URI withheld.

    The platform's ``inputsLink``/``outputsLink`` objects carry a pre-authorized
    (SAS-bearing) ``uri`` alongside non-secret descriptive fields such as content
    size, hash and type. Only the credential is withheld, so callers keep the
    metadata they need to decide whether to fetch, matching how
    ``az logicapp config appsettings`` redacts values while keeping keys visible.
    """
    if not isinstance(link, dict):
        return link
    redacted = dict(link)
    for key in list(redacted):
        if str(key).lower() == "uri" and redacted[key]:
            redacted[key] = LOGICAPP_REDACTION_SENTINEL
    return redacted


def _apply_content_link_redaction(entry, show_content_urls):
    if show_content_urls:
        return entry
    for key in ("inputsLink", "outputsLink"):
        if entry.get(key) is not None:
            entry[key] = _redact_content_link(entry[key])
    return entry


def _history_response(raw, workflow, trigger):
    props = _properties(raw or {})
    run = _field(props, "run")
    direct_run_id = _field(props, "runId")
    referenced_run_id = _run_id(run)
    run_id = referenced_run_id or direct_run_id
    return {
        "historyId": (raw or {}).get("name") or _field(props, "name") or _field(props, "historyId"),
        "workflow": workflow,
        "trigger": trigger,
        "status": _field(props, "status") or (raw or {}).get("status"),
        "code": _field(props, "code") or (raw or {}).get("code"),
        "error": _field(props, "error") or (raw or {}).get("error"),
        "startTime": _field(props, "startTime"),
        "endTime": _field(props, "endTime"),
        "scheduledTime": _field(props, "scheduledTime"),
        "fired": _field(props, "fired"),
        "sourceTriggerHistoryName": _field(props, "sourceTriggerHistoryName"),
        "trackingId": _field(props, "trackingId"),
        "correlation": _field(props, "correlation"),
        "run": run,
        "runId": run_id,
        "workflowVersion": _workflow_version(run),
        "runReferenceState": _run_reference_state(props, run, run_id),
        "_runIdSynthesised": bool(referenced_run_id),
        "inputsLink": _field(props, "inputsLink"),
        "outputsLink": _field(props, "outputsLink"),
    }

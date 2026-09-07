# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long

"""Workflow trigger command bodies for Logic Apps Standard.

Registration for this module lives in ``appservice/commands.py``; argument context
lives in ``appservice/_params.py``; help lives in ``appservice/logicapp/_help.py``.
This module contains only the callable command bodies, the schema-version
constants, and the local helpers those bodies use.
"""

import json

from azure.cli.core.azclierror import HTTPError, ResourceNotFoundError, ValidationError
from azure.cli.core.commands.client_factory import get_subscription_id

from ._refusal_seam import REFUSAL_KIND_DESIGN, emit_refusal
from ._runtime_client import (
    SiteRuntimeClient,
    trigger_callback_url_path,
    trigger_run_path,
    trigger_schema_path,
    workflow_trigger_path,
    workflow_triggers_path,
)

TRIGGER_SCHEMA_VERSION = "logicapp.trigger/2026-08-29"
TRIGGER_SCHEMA_DOCUMENT_VERSION = "logicapp.trigger-2026-08-29"
TRIGGER_SCHEMA_SCHEMA_VERSION = "logicapp.triggerSchema/2026-08-29"
TRIGGER_SCHEMA_SCHEMA_DOCUMENT_VERSION = "logicapp.triggerSchema-2026-08-29"
TRIGGER_CALLBACK_URL_SCHEMA_VERSION = "logicapp.triggerCallbackUrl/2026-08-29"
TRIGGER_CALLBACK_URL_SCHEMA_DOCUMENT_VERSION = "logicapp.triggerCallbackUrl-2026-08-29"
TRIGGER_RUN_SCHEMA_VERSION = "logicapp.triggerRun/2026-08-29"
TRIGGER_RUN_SCHEMA_DOCUMENT_VERSION = "logicapp.triggerRun-2026-08-29"
_SITE_PROVIDER = "/providers/Microsoft.Web/sites/"
_CM014_SCHEMA_404_CAUSE = (
    "The trigger schema endpoint returned 404; source shows only request triggers expose a JSON "
    "request schema, and the same status can also mean the trigger was not found."
)
_CM014_SCHEMA_404_REMEDY = (
    "Use 'az logicapp workflow trigger show' to inspect the trigger, or run show-schema against a "
    "request trigger."
)
_TRIGGER_RUN_UNKNOWN_MESSAGE = (
    "Trigger run request was accepted, but the platform response did not identify the new run id; "
    "recurrence and notification triggers can return OK with no body. The CLI will not infer or "
    "fabricate a run id. No deterministic follow-up run id is available from this response. Optional "
    "manual workaround: record the request time, list later workflow runs with --start-time, and "
    "inspect candidate runs for trigger.name matching this trigger; this is approximate, and under "
    "concurrent trigger firings it can attribute the wrong run. It is not a substitute for a "
    "returned runId."
)


# --- manifest rows exported for capability collection ---------------------
#
# Manifest rows are declared here as module-level dicts so registration
# (appservice/commands.py) can attach them via ``attach_manifest`` after
# each ``group.custom_command`` call, matching the extension's
# ``custom_command_with_manifest`` seam without inventing a core convention
# for a decorator kwarg on ``command_group``.

TRIGGER_LIST_MANIFEST = {
    "capabilityId": "CM-013-list",
    "command": "logicapp workflow trigger list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}

TRIGGER_SHOW_MANIFEST = {
    "capabilityId": "CM-013-show",
    "command": "logicapp workflow trigger show",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}

TRIGGER_SHOW_SCHEMA_MANIFEST = {
    "capabilityId": "CM-014",
    "command": "logicapp workflow trigger show-schema",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_SCHEMA_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}

TRIGGER_SHOW_CALLBACK_URL_MANIFEST = {
    "capabilityId": "CM-015",
    "command": "logicapp workflow trigger show-callback-url",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_CALLBACK_URL_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": True,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}

TRIGGER_RUN_MANIFEST = {
    "capabilityId": "CM-016",
    "command": "logicapp workflow trigger run",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": TRIGGER_RUN_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}


def trigger_list(cmd, resource_group_name, name, workflow, max_items=None, next_token=None, client=None):
    client = client or _client(cmd, resource_group_name, name)
    payload = client.list(workflow_triggers_path(workflow), continuation_token=next_token, max_items=max_items)
    return {
        "schemaVersion": TRIGGER_SCHEMA_VERSION,
        "value": [_trigger_response(item, workflow) for item in _value(payload)],
        "nextContinuationToken": payload.get("nextContinuationToken") if isinstance(payload, dict) else None,
        "synthesised": {
            "fields": ["nextContinuationToken"],
            "clientSidePaging": True,
            "reason": (
                "The site-runtime triggers route returns one collection; --max-items and "
                "--next-token are applied by the CLI after reading that collection."
            ),
        },
    }


def trigger_show(cmd, resource_group_name, name, workflow, trigger, client=None):
    client = client or _client(cmd, resource_group_name, name)
    return _trigger_response(client.get(workflow_trigger_path(workflow, trigger)), workflow, trigger)


def trigger_show_schema(cmd, resource_group_name, name, workflow, trigger, client=None):
    client = client or _client(cmd, resource_group_name, name)
    try:
        schema = client.get(trigger_schema_path(workflow, trigger))
    except (ResourceNotFoundError, HTTPError) as ex:
        if isinstance(ex, ResourceNotFoundError) or _status_code(ex) == 404:
            # The schema route answers 404 both when the workflow or trigger is
            # missing and when the trigger exists but exposes no request schema.
            # Read the trigger itself to tell them apart: a missing resource
            # propagates ResourceNotFoundError (exit 3, per the az show rule),
            # while an existing trigger means the schema is genuinely absent and
            # the capability refusal is the truthful answer.
            client.get(workflow_trigger_path(workflow, trigger))
            emit_refusal(
                kind=REFUSAL_KIND_DESIGN,
                capability_id="CM-014",
                feasibility_state="refused",
                gap=_CM014_SCHEMA_404_CAUSE,
                remedy=_CM014_SCHEMA_404_REMEDY,
                cause=ex,
            )
        raise
    return {
        "schemaVersion": TRIGGER_SCHEMA_SCHEMA_VERSION,
        "workflow": workflow,
        "trigger": trigger,
        "schema": schema,
    }


def trigger_show_callback_url(cmd, resource_group_name, name, workflow, trigger, client=None):
    client = client or _client(cmd, resource_group_name, name)
    payload = client.post(trigger_callback_url_path(workflow, trigger), body={})
    return {
        "schemaVersion": TRIGGER_CALLBACK_URL_SCHEMA_VERSION,
        "triggerName": trigger,
        "callbackUrl": _field(payload, "value") or _field(payload, "callbackUrl") or (payload if isinstance(payload, str) else None),
    }


def trigger_run(cmd, resource_group_name, name, workflow, trigger, payload_file=None, no_wait=False, client=None):  # pylint: disable=unused-argument
    client = client or _client(cmd, resource_group_name, name)
    body = _load_payload_file(payload_file) if payload_file else None
    response = _post_with_headers(client, trigger_run_path(workflow, trigger), body=body)
    payload = response["payload"]
    headers = response["headers"]
    props = _properties(payload or {})
    run_id = _header(headers, "x-ms-workflow-run-id") or _field(payload, "name") or _field(props, "name") or _field(props, "runId")
    result = {
        "schemaVersion": TRIGGER_RUN_SCHEMA_VERSION,
        "triggerName": trigger,
        "workflow": workflow,
        "runId": run_id,
        "status": _field(props, "status") or _field(payload, "status") or ("Accepted" if run_id else "AcceptedNewRunIdUnknown"),
    }
    if not run_id:
        result["message"] = _TRIGGER_RUN_UNKNOWN_MESSAGE
    return result


def trigger_list_table_format(result):
    if isinstance(result, dict) and isinstance(result.get("value"), list):
        return result["value"]
    return result or []


def trigger_show_table_format(result):
    return [result] if result else []


def trigger_schema_table_format(result):
    if not result:
        return []
    text = json.dumps(result.get("schema"), separators=(",", ":"), sort_keys=True)
    return [{
        "workflow": result.get("workflow"),
        "trigger": result.get("trigger"),
        "contentSizeBytes": len(text.encode("utf-8")),
        "preview": text[:120],
    }]


def trigger_callback_url_table_format(result):
    return [result] if result else []


def trigger_run_table_format(result):
    return [result] if result else []


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


def _trigger_response(raw, workflow, trigger=None):
    props = _properties(raw or {})
    return {
        "schemaVersion": TRIGGER_SCHEMA_VERSION,
        "triggerName": trigger or _field(raw, "name") or _field(props, "name") or _field(props, "triggerName"),
        "workflow": workflow,
        "type": _field(props, "type") or _field(raw, "type"),
        "kind": _field(props, "kind") or _field(raw, "kind"),
        "state": _field(props, "state") or _field(raw, "state"),
        "recurrence": _field(props, "recurrence"),
        "inputs": _field(props, "inputs"),
        "conditions": _field(props, "conditions"),
        "splitOn": _field(props, "splitOn"),
        "metadata": _field(props, "metadata"),
    }


def _post_with_headers(client, path, body=None):
    if hasattr(client, "post_with_headers"):
        return client.post_with_headers(path, body=body)
    return {"payload": client.post(path, body=body), "headers": {}}


def _header(headers, name):
    if not headers:
        return None
    lowered = name.lower()
    for key, value in headers.items() if hasattr(headers, "items") else []:
        if str(key).lower() == lowered:
            if isinstance(value, (list, tuple)):
                return str(value[0]) if value else None
            return str(value)
    return None


def _load_payload_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as ex:
        raise ValidationError("payload-file must contain valid JSON") from ex
    except OSError as ex:
        raise ValidationError("payload-file could not be read: {}".format(ex)) from ex


def _status_code(ex):
    return getattr(getattr(ex, "response", None), "status_code", None)

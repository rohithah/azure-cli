# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long

"""Workflow metadata command bodies for Logic Apps Standard.

Ships the two read-only workflow leaves: ``workflow list`` and ``workflow show``.
Both are thin pass-throughs over the site-runtime ``workflows`` route family.

Live capture against a deployed Logic Apps Standard app showed that
``GET .../workflows`` returns a top-level JSON array and does not support ``$top``; no
server-side or client-side paging flags are exposed here. The CLI wraps that
array in a ``value`` envelope to match the shipped workflow list-command
convention, but it does not mint ``nextContinuationToken`` or ``connectors``.
``workflow show`` returns the singleton platform object verbatim: no root
``state`` and no stdout ``schemaVersion`` are added.
"""

from azure.cli.core.commands.client_factory import get_subscription_id

from ._runtime_client import (
    SiteRuntimeClient,
    workflow_path,
    workflows_path,
)

WORKFLOW_LIST_SCHEMA_DOCUMENT_VERSION = "logicapp.workflow-list-2026-09-11"
WORKFLOW_SHOW_SCHEMA_DOCUMENT_VERSION = "logicapp.workflow-2026-09-11"
_SITE_PROVIDER = "/providers/Microsoft.Web/sites/"


WORKFLOW_LIST_MANIFEST = {
    "capabilityId": "CM-024-list",
    "command": "logicapp workflow list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": WORKFLOW_LIST_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": (
        "The site-runtime workflows route returns a top-level array with no observed nextLink "
        "or continuation token and does not support $top; this command therefore exposes no "
        "paging flags and emits no nextContinuationToken."
    ),
    "modeCondition": None,
    "contentOnly": False,
}

WORKFLOW_SHOW_MANIFEST = {
    "capabilityId": "CM-024-show",
    "command": "logicapp workflow show",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": WORKFLOW_SHOW_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}


def workflow_list(cmd, resource_group_name, name, client=None):
    """List workflows deployed in one Logic App Standard site."""
    client = client or _client(cmd, resource_group_name, name)
    payload = client.get(workflows_path())
    return _workflow_list_response(payload)


def workflow_show(cmd, resource_group_name, name, workflow, client=None):
    """Show one workflow's platform metadata."""
    client = client or _client(cmd, resource_group_name, name)
    return _workflow_response(client.get(workflow_path(workflow)))


def workflow_list_table_format(result):
    if not result:
        return []
    return [{
        "name": item.get("name"),
        "kind": item.get("kind"),
        "state": _health(item).get("state"),
        "isDisabled": item.get("isDisabled"),
        "href": item.get("href"),
        "definition_href": item.get("definition_href"),
    } for item in result.get("value", [])]


def workflow_show_table_format(result):
    if not result:
        return []
    return [{
        "name": result.get("name"),
        "kind": result.get("kind"),
        "state": _health(result).get("state"),
        "isDisabled": result.get("isDisabled"),
        "href": result.get("href"),
        "definition_href": result.get("definition_href"),
    }]


# ------------------------------- helpers -------------------------------


def _client(cmd, resource_group_name, name):
    subscription_id = get_subscription_id(cmd.cli_ctx)
    return SiteRuntimeClient(
        cmd,
        "/subscriptions/{}/resourceGroups/{}{}{}".format(
            subscription_id, resource_group_name, _SITE_PROVIDER, name))


def _workflow_response(payload):
    return dict(payload or {})


def _workflow_list_response(payload):
    return {
        "value": [_workflow_response(item) for item in _value(payload)],
        "synthesised": {
            "fields": ["value"],
            "reason": (
                "The platform workflows route returns a top-level array. This command wraps "
                "that array in value to match the shipped Logic Apps workflow list-command "
                "convention. Each value[] entry is copied verbatim; no state, schemaVersion, "
                "connectors, or nextContinuationToken field is added."
            ),
        },
    }


def _value(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("value") or []
    return []


def _health(raw):
    if not isinstance(raw, dict):
        return {}
    health = raw.get("health")
    return health if isinstance(health, dict) else {}

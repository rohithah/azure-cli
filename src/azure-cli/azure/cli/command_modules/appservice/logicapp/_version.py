# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long

"""Workflow version command bodies for Logic Apps Standard.

A Logic Apps Standard workflow is versioned by the platform: deploying changed
content over an existing workflow creates a new version rather than replacing
the old one. In-flight runs continue on the version they started under, and new
runs pick up the latest version.

That makes the deployed definition and the *executed* definition two different
things whenever a workflow has been redeployed since a run started. These
commands expose the version history so a caller debugging a historical run can
fetch the definition that actually ran, instead of reasoning from the current
one.

The site-runtime management API backs both leaves directly:

    GET api/management/workflows/{flowName}/versions/
    GET api/management/workflows/{flowName}/versions/{flowVersion}

Registration lives in ``appservice/commands.py``; argument context lives in
``appservice/_params.py``; help lives in ``appservice/logicapp/_help.py``.
"""

from azure.cli.core.commands.client_factory import get_subscription_id

from ._runtime_client import (
    SiteRuntimeClient,
    workflow_version_path,
    workflow_versions_path,
)

VERSION_SCHEMA_DOCUMENT_VERSION = "logicapp.workflowVersion-2026-09-07"
_SITE_PROVIDER = "/providers/Microsoft.Web/sites/"


VERSION_LIST_MANIFEST = {
    "capabilityId": "CM-021-list",
    "command": "logicapp workflow version list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": VERSION_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}

VERSION_SHOW_MANIFEST = {
    "capabilityId": "CM-021-show",
    "command": "logicapp workflow version show",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": VERSION_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}


def version_list(cmd, resource_group_name, name, workflow, max_items=None, next_token=None, client=None):
    client = client or _client(cmd, resource_group_name, name)
    payload = client.list(workflow_versions_path(workflow), continuation_token=next_token, max_items=max_items)
    return {
        "value": [_version_response(item, workflow, include_definition=False) for item in _value(payload)],
        "nextContinuationToken": payload.get("nextContinuationToken") if isinstance(payload, dict) else None,
        "synthesised": {
            "fields": ["value[].workflow", "value[].version", "nextContinuationToken"],
            "clientSidePaging": True,
            "reason": (
                "value[].workflow is the --workflow argument echoed back, not a platform "
                "field. value[].version is copied verbatim from the entry's "
                "properties.version when the platform supplies it; when it does not, it is "
                "CLI-computed as the trailing segment of the entry's composite name or "
                "resource id, because the versions route names an entry "
                "'{workflow}/{sequenceId}' while run payloads reference the bare sequence "
                "id -- both shapes are normalised to the value 'version show --version' "
                "accepts. nextContinuationToken is copied verbatim from the response. "
                "The site-runtime versions route returns one collection; --max-items and "
                "--next-token are applied by the CLI after reading that collection. The "
                "route does accept server-side top/continuationToken parameters, but this "
                "command reads the collection and pages client-side to match the paging "
                "behaviour of every other logicapp workflow list command."
            ),
        },
    }


def version_show(cmd, resource_group_name, name, workflow, version, client=None):
    client = client or _client(cmd, resource_group_name, name)
    payload = client.get(workflow_version_path(workflow, version))
    return _version_response(payload, workflow, version=version, include_definition=True)


def version_list_table_format(result):
    if isinstance(result, dict) and isinstance(result.get("value"), list):
        return result["value"]
    return result or []


def version_show_table_format(result):
    if not result:
        return []
    return [{
        "workflow": result.get("workflow"),
        "version": result.get("version"),
        "state": result.get("state"),
        "createdTime": result.get("createdTime"),
        "changedTime": result.get("changedTime"),
    }]


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


def _version_id(raw, props):
    """Resolve the version identifier the platform assigned.

    The versions route names an entry ``{flowName}/{flowSequenceId}`` while the
    run payloads that reference a version report the bare sequence id. Prefer
    the explicit ``properties.version`` field, then fall back to the trailing
    segment of the composite name or resource id so both shapes resolve to the
    same value a caller can pass back to ``version show``.
    """
    explicit = _field(props, "version")
    if explicit:
        return explicit
    for candidate in (_field(raw, "name"), _field(raw, "id")):
        if isinstance(candidate, str) and "/" in candidate:
            return candidate.rstrip("/").split("/")[-1]
        if candidate:
            return candidate
    return None


def _version_response(raw, workflow, version=None, include_definition=False):
    props = _properties(raw or {})
    response = {
        "workflow": workflow,
        "version": version or _version_id(raw or {}, props),
        "state": _field(props, "state"),
        "createdTime": _field(props, "createdTime"),
        "changedTime": _field(props, "changedTime"),
        "accessEndpoint": _field(props, "accessEndpoint"),
    }
    if include_definition:
        # The definition is the whole point of `version show`: it is the
        # authoring content that actually ran. `version list` omits it so a
        # listing does not carry a full definition per entry.
        response["definition"] = _field(props, "definition")
        response["parameters"] = _field(props, "parameters")
        response["connectionReferences"] = _field(props, "connectionReferences")
    return response

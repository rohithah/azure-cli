# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long

"""Connector catalog command bodies for Logic Apps Standard.

Ships the four read-only connector leaves in this pass: ``connector list``,
``connector show``, ``connector operation list``, and ``connector operation show``.
All four are thin pass-throughs over the site-runtime ``operationGroups`` route family.

Every design decision below is anchored to a live capture taken against
``la-cli-pilot-ri2bg8`` / ``rg-lacli-ri2bg8`` and stored under
``.squad/findings/scratch/connector/`` in the spec corpus.

Divergences from the design reference (``logicapps-standard-cli-reference.md:869-929``)
and why each one is evidence-driven:

1. **No ``--site`` alias.** The design signature spells ``--site <s>``; this module reuses
   the inherited ``-n/--name`` site argument declared once in ``_params.py`` (L1248), matching
   every other shipped ``logicapp workflow`` leaf.

2. **``nextContinuationToken`` is CLI-computed, not platform paging.** The design left this
   row undecided. Live probe settles it: ``operationGroups`` returns root key ``value`` only
   (50 items), with no ``nextLink`` and no ``nextContinuationToken``; adding ``$top=2`` returns
   a **byte-identical 280856-byte** response, so the route neither pages nor honours ``$top``.
   The operations route behaves the same way (4 items; ``$top=2`` byte-identical at 4208 bytes).
   ``--max-items``/``--next-token`` are therefore applied by the CLI after reading the whole
   collection, exactly as ``run action list`` does. Per ``decisions.md`` D66 this finding is
   scoped to these two routes and is not generalised to any other command family.

3. **``kind`` is not emitted and is not synthesised.** The design proposed a
   ``"schema" | "names-only"`` discriminator. Live ``operationGroups/{c}/operations/{op}``
   returns exactly ``id, name, properties, type`` -- no ``kind``. The engineer ruling against
   minting platform-absent fields (the stripped ``correlation.schemaVersion`` literal) applies.

4. **``manifest`` is not emitted and is not synthesised.** The string ``manifest`` does not
   appear anywhere in the live response, and ``$expand=manifest`` returns a **byte-identical
   942-byte** payload -- the expand is silently ignored. Because ``kind`` and ``manifest`` were
   the only two siblings the design's ``operation`` wrapper existed to separate, that wrapper
   would now be a single-key envelope carrying no information. This module therefore flattens
   the platform operation object to the response root and discloses CLI echoes under
   ``synthesised``, matching ``run_action_show``.

5. **No ``--names-only`` flag.** Its entire designed purpose was to opt down from
   ``expandManifest=true`` and flip ``kind`` to ``"names-only"``. Both the manifest expansion
   and the ``kind`` discriminator are proven absent, so the flag would toggle nothing.

6. **No ``--workflow-kind`` filter.** The design names the flag but never states its legal
   value set or what it filters against, and no server-side filtering was observed on this
   route. Implementing it would require inventing semantics.

7. **No ``source`` classification field.** The design illustration carries
   ``"source": "built-in" | "service-provider"``. Live capture does show a clean partition --
   19 of 50 entries have ids under ``connectionProviders/`` and 31 under ``serviceProviders/``
   -- but emitting a derived label is exactly the platform-absent minting the engineer ruling
   declines. The distinguishing ``id`` is passed through verbatim; callers can read it directly.

8. **``type`` is class-conditional, not universal.** Measured across the live list: ``type``
   is present on all 31 ``serviceProviders`` entries and absent on all 19 ``connectionProviders``
   entries; the singleton show route behaves identically (a ``connectionProviders`` show returns
   root keys ``id, name, properties`` only). The CLI does not backfill it.

9. **No ``schemaVersion`` on stdout.** Manifest rows carry the identifier; stdout does not.
   Guarded by ``test_logicapp_workflow_stdout_schema_version``.
"""

from ._runtime_client import (
    SiteRuntimeClient,
    operation_group_operation_path,
    operation_group_operations_path,
    operation_group_path,
    operation_groups_path,
)

from azure.cli.core.commands.client_factory import get_subscription_id

CONNECTOR_LIST_SCHEMA_DOCUMENT_VERSION = "logicapp.connector-list-2026-09-11"
CONNECTOR_SHOW_SCHEMA_DOCUMENT_VERSION = "logicapp.connector-2026-09-11"
CONNECTOR_OPERATION_LIST_SCHEMA_DOCUMENT_VERSION = "logicapp.connector-operation-list-2026-09-11"
CONNECTOR_OPERATION_SHOW_SCHEMA_DOCUMENT_VERSION = "logicapp.connector-operation-2026-09-11"

_SITE_PROVIDER = "/providers/Microsoft.Web/sites/"

_CLIENT_PAGING_GAP = (
    "The site-runtime operationGroups route returns one collection with no observed nextLink "
    "and silently ignores $top (a $top=2 probe returned a byte-identical response); "
    "--max-items and --next-token are applied by the CLI after reading that collection."
)


CONNECTOR_LIST_MANIFEST = {
    "capabilityId": "CM-022-list",
    "command": "logicapp workflow connector list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": CONNECTOR_LIST_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": _CLIENT_PAGING_GAP,
    "modeCondition": None,
    "contentOnly": False,
}

CONNECTOR_SHOW_MANIFEST = {
    "capabilityId": "CM-022-show",
    "command": "logicapp workflow connector show",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": CONNECTOR_SHOW_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}

CONNECTOR_OPERATION_LIST_MANIFEST = {
    "capabilityId": "CM-023-list",
    "command": "logicapp workflow connector operation list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": CONNECTOR_OPERATION_LIST_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": _CLIENT_PAGING_GAP,
    "modeCondition": None,
    "contentOnly": False,
}

CONNECTOR_OPERATION_SHOW_MANIFEST = {
    "capabilityId": "CM-023-show",
    "command": "logicapp workflow connector operation show",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": CONNECTOR_OPERATION_SHOW_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": (
        "The platform operation payload carries no manifest and no kind discriminator; "
        "$expand=manifest is silently ignored (byte-identical response), so parameter schemas "
        "are not available from this route."
    ),
    "modeCondition": None,
    "contentOnly": False,
}


def connector_list(cmd, resource_group_name, name, max_items=None, next_token=None, client=None):
    """List the connector catalog (operation groups) visible to one Logic App Standard site.

    Client-side paging is applied over the returned collection because the route neither
    emits a continuation token nor honours ``$top`` (see module docstring, item 2).
    """
    client = client or _client(cmd, resource_group_name, name)
    payload = client.list(operation_groups_path(),
                          continuation_token=next_token, max_items=max_items)
    return {
        "value": _value(payload),
        "nextContinuationToken": payload.get("nextContinuationToken") if isinstance(payload, dict) else None,
        "synthesised": {
            "fields": ["nextContinuationToken"],
            "clientSidePaging": True,
            "reason": (
                "Every value[] entry is copied verbatim from the platform operationGroups route; "
                "no field is added, renamed, or backfilled. Note that value[].type is emitted only "
                "for serviceProviders entries and is absent for connectionProviders entries -- the "
                "CLI does not synthesise it. nextContinuationToken is CLI-computed: the route returns "
                "one collection with no nextLink and ignores $top, so --max-items and --next-token "
                "are applied by the CLI after reading that collection."
            ),
        },
    }


def connector_show(cmd, resource_group_name, name, connector, client=None):
    """Show one connector (operation group) by name.

    The platform payload is returned verbatim. Root keys observed live are
    ``id, name, properties, type`` for a ``serviceProviders`` connector and
    ``id, name, properties`` for a ``connectionProviders`` connector; the CLI
    does not backfill the missing ``type``.
    """
    client = client or _client(cmd, resource_group_name, name)
    payload = client.get(operation_group_path(connector))
    result = dict(payload or {})
    result["synthesised"] = {
        "fields": [],
        "reason": (
            "The platform connector payload is passed through verbatim; no field is added, "
            "renamed, or backfilled. type is emitted only for serviceProviders connectors and "
            "is absent for connectionProviders connectors."
        ),
    }
    return result


def connector_operation_list(cmd, resource_group_name, name, connector,
                             max_items=None, next_token=None, client=None):
    """List the operations exposed by one connector."""
    client = client or _client(cmd, resource_group_name, name)
    payload = client.list(operation_group_operations_path(connector),
                          continuation_token=next_token, max_items=max_items)
    return {
        "connector": connector,
        "value": _value(payload),
        "nextContinuationToken": payload.get("nextContinuationToken") if isinstance(payload, dict) else None,
        "synthesised": {
            "fields": ["connector", "nextContinuationToken"],
            "clientSidePaging": True,
            "reason": (
                "connector is the CLI input echoed at the top level for context; the platform list "
                "route does not return it. Every value[] entry is copied verbatim. nextContinuationToken "
                "is CLI-computed: the route returns one collection with no nextLink and ignores $top, so "
                "--max-items and --next-token are applied by the CLI after reading that collection."
            ),
        },
    }


def connector_operation_show(cmd, resource_group_name, name, connector, operation, client=None):
    """Show one operation on one connector.

    The platform operation object is returned flattened at the response root
    (``id``, ``name``, ``type``, ``properties``), with the two CLI inputs echoed
    alongside and disclosed under ``synthesised``. No ``kind`` discriminator and no
    ``manifest`` are emitted -- neither exists on this route (see module docstring,
    items 3 and 4).
    """
    client = client or _client(cmd, resource_group_name, name)
    payload = client.get(operation_group_operation_path(connector, operation))
    result = dict(payload or {})
    result["connector"] = connector
    result["operation"] = operation
    result["synthesised"] = {
        "fields": ["connector", "operation"],
        "reason": (
            "connector and operation are CLI inputs echoed at the top level for context. Every other "
            "key is copied verbatim from the platform operation payload. No kind discriminator and no "
            "manifest are emitted: the live route returns exactly id, name, properties, and type, and "
            "$expand=manifest is silently ignored, so operation parameter schemas are not available here."
        ),
    }
    return result


def connector_list_table_format(result):
    if not result:
        return []
    return [{
        "name": item.get("name"),
        "id": item.get("id"),
        "type": item.get("type"),
        "displayName": _properties(item).get("displayName"),
        "description": _properties(item).get("description"),
    } for item in result.get("value", [])]


def connector_show_table_format(result):
    if not result:
        return []
    props = _properties(result)
    return [{
        "name": result.get("name"),
        "id": result.get("id"),
        "type": result.get("type"),
        "displayName": props.get("displayName"),
        "description": props.get("description"),
    }]


def connector_operation_list_table_format(result):
    if not result:
        return []
    return [{
        "name": item.get("name"),
        "connector": result.get("connector"),
        "type": item.get("type"),
        "summary": _properties(item).get("summary"),
        "operationType": _properties(item).get("operationType"),
        "visibility": _properties(item).get("visibility"),
    } for item in result.get("value", [])]


def connector_operation_show_table_format(result):
    if not result:
        return []
    props = _properties(result)
    return [{
        "name": result.get("name"),
        "connector": result.get("connector"),
        "type": result.get("type"),
        "summary": props.get("summary"),
        "operationType": props.get("operationType"),
        "visibility": props.get("visibility"),
    }]


# ------------------------------- helpers -------------------------------


def _client(cmd, resource_group_name, name):
    subscription_id = get_subscription_id(cmd.cli_ctx)
    return SiteRuntimeClient(
        cmd,
        "/subscriptions/{}/resourceGroups/{}{}{}".format(
            subscription_id, resource_group_name, _SITE_PROVIDER, name))


def _properties(raw):
    if not isinstance(raw, dict):
        return {}
    props = raw.get("properties")
    return props if isinstance(props, dict) else {}


def _value(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("value") or []
    return []

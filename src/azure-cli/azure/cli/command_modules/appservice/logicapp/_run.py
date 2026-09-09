# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long

"""Workflow run command bodies for Logic Apps Standard.

Ships the two read-only ``run`` leaves in this pass: ``run list`` and ``run show``.
The three write-shaped run operations (cancel, resubmit, wait) and the four run-action
leaves are registered in sibling modules, not here.

Two conscious deltas vs. the superseded extension prototype
(``tools/logicapps/cli-ext/azext_logicapp/_run.py``):

1. **Server-side paging on ``run list``.** The site-runtime plural runs route accepts
   ``$top`` and ``$skiptoken`` natively (source: ``FlowRunApiEngine.cs:157-224`` and
   ``QueryStringReader.cs:760-789``), and live capture at
   ``scratch/specDemo/run-list-top2.json`` confirms the response envelope embeds
   ``nextLink`` with an encoded ``$skiptoken`` query parameter. This module maps
   ``--max-items`` to ``$top``, maps ``--next-token`` to ``$skiptoken``, and surfaces
   ``nextContinuationToken`` as the token parsed out of the platform's ``nextLink`` --
   passed through verbatim, opaque to the CLI. The prototype instead read the entire
   collection and CLI-minted a base64({offset}) slice-token; that is declined here per
   ruling 3 because ``run list`` is unbuilt in core so there is no in-flight caller to
   migrate. Both sibling shipped list leaves (``trigger history list``, ``version list``)
   keep their client-side paging because they are already shipped.

2. **SAS-bearing content-link URIs are redacted by default.** Live captures at
   ``scratch/specDemo/run-list.json`` and ``run-show.json`` carry SAS-bearing URIs on
   ``properties.trigger.inputsLink``, ``properties.trigger.outputsLink``, and
   ``properties.response.outputsLink`` for every run. This module walks the returned
   run object and replaces the ``uri`` value on any ``*Link`` dict (matching the
   trigger-history convention) with ``LOGICAPP_REDACTION_SENTINEL``. Opt in via
   ``--show-content-urls`` to see them verbatim, using the same wording as the
   trigger-history disclosure.

No ``schemaVersion`` is emitted on stdout. Manifest rows carry the identifier for the
capability-list surface; stdout output does not. See
``tests/latest/test_logicapp_workflow_stdout_schema_version.py`` for the guard.
"""

from urllib.parse import parse_qs, urlparse

from azure.cli.core.azclierror import ValidationError
from azure.cli.core.commands.client_factory import get_subscription_id

from ._constants import LOGICAPP_REDACTION_SENTINEL
from ._runtime_client import SiteRuntimeClient, workflow_run_path, workflow_runs_path
from ._trigger_history import _redact_content_link

RUN_LIST_SCHEMA_DOCUMENT_VERSION = "logicapp.run-list-2026-08-29"
RUN_SCHEMA_DOCUMENT_VERSION = "logicapp.run-2026-08-29"
_SITE_PROVIDER = "/providers/Microsoft.Web/sites/"


RUN_LIST_MANIFEST = {
    "capabilityId": "CM-001",
    "command": "logicapp workflow run list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": RUN_LIST_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": True,
    "delegatedTo": None,
    "gap": (
        "The site-runtime plural runs route exposes native $filter clauses for status and startTime "
        "but no endTime filter; --end-time is a CLI post-filter and is disclosed under "
        "synthesised.clientSideFilters when it is applied."
    ),
    "modeCondition": None,
    "contentOnly": False,
}

RUN_SHOW_MANIFEST = {
    "capabilityId": "CM-002",
    "command": "logicapp workflow run show",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": RUN_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": True,
    "delegatedTo": None,
    "gap": None,
    "modeCondition": None,
    "contentOnly": False,
}


def run_list(cmd, resource_group_name, name, workflow,
             status=None, start_time=None, end_time=None,
             max_items=None, next_token=None,
             show_content_urls=False, client=None):
    """List workflow runs for one workflow, paging server-side.

    Native filters (``$filter status eq``, ``$filter startTime ge``) are assembled
    from ``--status`` and ``--start-time`` and sent to the runtime. ``--end-time`` is
    applied by the CLI after the response returns because the runtime exposes no
    native ``endTime`` filter on this route.
    """
    client = client or _client(cmd, resource_group_name, name)
    params = _run_list_params(status=status, start_time=start_time, max_items=max_items, next_token=next_token)
    payload = client.get(workflow_runs_path(workflow), params=params)
    items = [_run_summary_response(item, workflow, show_content_urls) for item in _value(payload)]

    client_side_filters = None
    if end_time is not None:
        cutoff = _parse_datetime(end_time, "end-time")
        items = [item for item in items if _datetime_le(item.get("endTime"), cutoff)]
        client_side_filters = ["endTime"]

    next_continuation_token = _extract_skiptoken(payload)
    synthesised = {
        "fields": ["value[].workflow", "value[].workflowVersion"],
        "serverSidePaging": True,
        "reason": (
            "workflow is the CLI --workflow input echoed onto each row. workflowVersion is copied "
            "verbatim from the platform's per-run properties.workflow object -- preferring "
            "properties.workflow.name, else the trailing segment of properties.workflow.id after "
            "'/versions/', else null -- and hoisted to a bare sequence identifier so it can be passed "
            "back to 'az logicapp workflow version show --version'. Paging is server-side: --max-items "
            "is sent as $top and --next-token is sent as $skiptoken; nextContinuationToken is the "
            "$skiptoken value extracted from the platform response envelope's nextLink URL, opaque to "
            "the CLI and passed through verbatim. --end-time remains a CLI post-filter because the "
            "runtime exposes no native EndTime filter on this route."
        ),
    }
    if client_side_filters is not None:
        synthesised["clientSideFilters"] = client_side_filters

    return {
        "value": items,
        "nextContinuationToken": next_continuation_token,
        "nextLink": payload.get("nextLink") if isinstance(payload, dict) else None,
        "synthesised": synthesised,
    }


def run_show(cmd, resource_group_name, name, workflow, run_id, show_content_urls=False, client=None):
    """Show one workflow run.

    Passes ``expandRunActions=false`` implicitly (default): actions are fetched
    via ``run action list``, not surfaced here. The run's executed workflow-version
    identity is hoisted from ``properties.workflow.name`` (source: source-read
    ``FlowRun.cs:773-782``, live-observed ``scratch/specDemo/run-show.json``).
    """
    client = client or _client(cmd, resource_group_name, name)
    payload = client.get(workflow_run_path(workflow, run_id))
    return _run_show_response(payload, workflow, run_id, show_content_urls)


def run_list_table_format(result):
    if not result:
        return []
    return [{
        "runId": item.get("runId"),
        "workflow": item.get("workflow"),
        "workflowVersion": item.get("workflowVersion"),
        "status": item.get("status"),
        "startTime": item.get("startTime"),
        "endTime": item.get("endTime"),
    } for item in result.get("value", [])]


def run_show_table_format(result):
    if not result:
        return []
    return [{
        "runId": result.get("runId"),
        "workflow": result.get("workflow"),
        "workflowVersion": result.get("workflowVersion"),
        "status": result.get("status"),
        "startTime": result.get("startTime"),
        "endTime": result.get("endTime"),
    }]


# ------------------------------- helpers -------------------------------


def _client(cmd, resource_group_name, name):
    subscription_id = get_subscription_id(cmd.cli_ctx)
    return SiteRuntimeClient(
        cmd,
        "/subscriptions/{}/resourceGroups/{}{}{}".format(
            subscription_id, resource_group_name, _SITE_PROVIDER, name))


def _run_list_params(status=None, start_time=None, max_items=None, next_token=None):
    params = {}
    clauses = []
    if status:
        clauses.append("status eq '{}'".format(str(status).replace("'", "''")))
    if start_time is not None:
        clauses.append("startTime ge {}".format(_odata_datetime_literal(start_time)))
    if clauses:
        params["$filter"] = " and ".join(clauses)
    if max_items is not None:
        top = int(max_items)
        if top <= 0:
            raise ValidationError("max-items must be greater than zero")
        params["$top"] = top
    if next_token:
        params["$skiptoken"] = next_token
    return params or None


def _extract_skiptoken(payload):
    if not isinstance(payload, dict):
        return None
    next_link = payload.get("nextLink")
    if not isinstance(next_link, str) or not next_link:
        return None
    parsed = urlparse(next_link)
    query = parse_qs(parsed.query, keep_blank_values=False)
    token_values = query.get("$skiptoken") or query.get("skiptoken")
    if not token_values:
        return None
    return token_values[0] or None


def _run_summary_response(raw, workflow, show_content_urls):
    props = _properties(raw)
    return {
        "runId": (raw or {}).get("name") or _field(props, "name") or _field(props, "runId"),
        "workflow": workflow,
        "workflowVersion": _workflow_version_from_run(props),
        "status": _field(props, "status") or (raw or {}).get("status"),
        "code": _field(props, "code"),
        "startTime": _field(props, "startTime"),
        "endTime": _field(props, "endTime"),
        "waitEndTime": _field(props, "waitEndTime"),
        "correlation": _field(props, "correlation"),
        "correlationId": _field(props, "correlationId"),
        "trigger": _apply_redaction(_field(props, "trigger"), show_content_urls),
        "response": _apply_redaction(_field(props, "response"), show_content_urls),
        "outputs": _field(props, "outputs"),
    }


def _run_show_response(raw, workflow, run_id, show_content_urls):
    props = _properties(raw or {})
    result = {
        "runId": (raw or {}).get("name") or _field(props, "name") or _field(props, "runId") or run_id,
        "workflow": workflow,
        "workflowVersion": _workflow_version_from_run(props),
        "status": _field(props, "status") or (raw or {}).get("status"),
        "code": _field(props, "code") or (raw or {}).get("code"),
        "error": _field(props, "error") or (raw or {}).get("error"),
        "startTime": _field(props, "startTime"),
        "endTime": _field(props, "endTime"),
        "waitEndTime": _field(props, "waitEndTime"),
        "correlation": _field(props, "correlation"),
        "correlationId": _field(props, "correlationId"),
        "trigger": _apply_redaction(_field(props, "trigger"), show_content_urls),
        "response": _apply_redaction(_field(props, "response"), show_content_urls),
        "outputs": _field(props, "outputs"),
        "previousRunId": _field(props, "previousRunId") or _field(props, "sourceTriggerHistoryName"),
    }
    result["synthesised"] = {
        "fields": ["workflow", "workflowVersion"],
        "reason": (
            "workflow is the CLI --workflow input echoed at the top level. workflowVersion is copied "
            "verbatim from the platform's properties.workflow object -- preferring "
            "properties.workflow.name, else the trailing segment of properties.workflow.id after "
            "'/versions/', else null -- and hoisted to a bare sequence identifier so it can be passed "
            "back to 'az logicapp workflow version show --version'. Content-link URIs on "
            "properties.trigger and properties.response are SAS-bearing on this route (source: "
            "ContentLink.GetSharedAccessOperationContentLinkDefinition) and are withheld by default; "
            "pass --show-content-urls to emit them in full."
        ),
    }
    return result


def _workflow_version_from_run(props):
    """Hoist the executed workflow-version identity from the run's nested workflow ref.

    Live capture at ``scratch/specDemo/run-list.json`` / ``run-show.json`` shows every
    entry carries ``properties.workflow`` as ``{id, name, type}`` where ``name`` is the
    bare ``FlowSequenceId`` -- the same field-path shape the trigger-history hoist uses
    (see ``_trigger_history._workflow_version``). The value is copied verbatim from the
    platform payload; the *hoist* to the top level is CLI projection provenance, which is
    what the ``synthesised`` disclosure declares.
    """
    if not isinstance(props, dict):
        return None
    workflow_ref = _field(props, "workflow")
    if not isinstance(workflow_ref, dict):
        return None
    name_value = _field(workflow_ref, "name")
    if name_value:
        return name_value
    return _version_from_id(_field(workflow_ref, "id"))


def _version_from_id(value):
    if isinstance(value, str) and "/versions/" in value.lower():
        return value.rstrip("/").split("/")[-1]
    return None


def _apply_redaction(value, show_content_urls):
    """Redact any ``inputsLink``/``outputsLink`` URIs found under ``value``.

    ``value`` is passed through the trigger-history primitive ``_redact_content_link``
    on every ``*Link`` dict that carries a ``uri`` field, at any nesting depth. When
    ``show_content_urls`` is true the value is returned unchanged.
    """
    if show_content_urls or value is None:
        return value
    return _redact_deep(value)


def _redact_deep(value):
    if isinstance(value, dict):
        redacted = {}
        for key, inner in value.items():
            if isinstance(key, str) and key.lower().endswith("link") and isinstance(inner, dict) and "uri" in inner:
                redacted[key] = _redact_content_link(inner)
            else:
                redacted[key] = _redact_deep(inner)
        return redacted
    if isinstance(value, list):
        return [_redact_deep(item) for item in value]
    return value


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


def _parse_datetime(value, label):
    from datetime import datetime, timezone
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as ex:
            raise ValidationError("{} must be an ISO 8601 timestamp".format(label)) from ex
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _odata_datetime_literal(value):
    dt = _parse_datetime(value, "start-time")
    return dt.isoformat().replace("+00:00", "Z")


def _datetime_le(value, cutoff):
    if value in (None, ""):
        return False
    return _parse_datetime(value, "endTime") <= cutoff


# Deliberately unused module-level re-export so tests that touch the redaction sentinel
# do not have to reach across modules. The value is the same string the trigger-history
# helper writes.
_REDACTION_SENTINEL = LOGICAPP_REDACTION_SENTINEL

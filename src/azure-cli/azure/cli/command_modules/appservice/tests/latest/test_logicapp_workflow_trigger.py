# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Unit tests for workflow workflow trigger command bodies.

Adapted from the extension prototype's trigger tests.  Adaptations
that were required by the port (see {TEAM_ROOT}\\findings\\port-execution.md
"Falsified port-plan assumptions"):

* the extension's ``load_command_table`` per-module aggregator no longer exists
  (registration is flattened into ``appservice/commands.py``), so per-module
  "5 commands + manifest rows" assertions live in
  ``test_logicapp_workflow_manifest.py`` against the full appservice table;
* ``trigger_list_callback_url`` was renamed to ``trigger_show_callback_url``
  and its command verb went ``list-callback-url`` -> ``show-callback-url``;
* the ``_help_trigger`` fragment module was flattened into ``_help.py``; the
  help-string-in-source check now targets ``_help.py`` directly, and the walk
  (test-execution.md) asserts the same strings against ``az -h`` rendered
  output.

Every ``self.cmd()``/action call asserts real content (D3, team.md #12) —
trigger names, schema-version stamps, honest-unknown ``runId: null`` disclosure,
route path (URL-encoded), and the ``synthesised`` marker where it applies.
"""

import pytest

from azure.cli.core.azclierror import ResourceNotFoundError
from azure.cli.core.mock import DummyCli
from knack.help_files import helps as knack_helps

from azure.cli.command_modules.appservice.logicapp._exceptions import DesignRefusalError
from azure.cli.command_modules.appservice.logicapp._trigger import (
    trigger_list,
    trigger_run,
    trigger_show,
    trigger_show_callback_url,
    trigger_show_schema,
)


def _reload_logicapp_help():
    """Re-register the logicapp help entries into knack's global ``helps`` dict.

    ``helps`` is process-global and is reset whenever another test module loads
    the full CLI (``azure.cli.testsdk`` does this). A bare
    ``import ..._help`` cannot repair that: the module is already in
    ``sys.modules``, so the import is a no-op and the registration side effect
    never runs again. The help assertions then pass or fail purely on which
    test file pytest happened to run first -- they passed under ``azdev test``
    (xdist workers, separate processes) while failing under a single-process
    ``pytest`` run of the same files.

    Reloading forces the registration to re-run, making these tests
    order-independent instead of accidentally-ordered.
    """
    import importlib
    import azure.cli.command_modules.appservice.logicapp._help as _logicapp_help
    importlib.reload(_logicapp_help)


class _Cmd:
    cli_ctx = DummyCli()


class _Client:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def list(self, path, params=None, continuation_token=None, max_items=None):
        from azure.cli.command_modules.appservice.logicapp._runtime_client import _slice_items
        self.calls.append(("list", path, params, continuation_token, max_items))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        items = payload if isinstance(payload, list) else payload.get("value", [])
        page, next_token = _slice_items(items, continuation_token=continuation_token, max_items=max_items)
        result = {"value": page, "nextContinuationToken": next_token}
        if isinstance(payload, dict):
            result.update({k: v for k, v in payload.items() if k != "value"})
            result["value"] = page
            result["nextContinuationToken"] = next_token
        return result

    def post(self, path, body=None, params=None):
        self.calls.append(("post", path, body, params))
        payload = self.payloads.pop(0) if self.payloads else None
        if isinstance(payload, Exception):
            raise payload
        return payload

    def post_with_headers(self, path, body=None, params=None):
        payload = self.post(path, body=body, params=params)
        if isinstance(payload, tuple):
            return {"payload": payload[0], "headers": payload[1]}
        return {"payload": payload, "headers": {}}


def test_trigger_list_and_show_call_site_runtime_routes_and_map_shape():
    client = _Client([{
        "value": [{"name": "manual", "properties": {"type": "Request", "kind": "Http", "state": "Enabled", "inputs": {"schema": {"type": "object"}}}}]
    }, {"name": "recurrence", "properties": {"type": "Recurrence", "recurrence": {"frequency": "Minute", "interval": 1}}}])

    listed = trigger_list(_Cmd(), "rg", "site", "wf", client=client)
    shown = trigger_show(_Cmd(), "rg", "site", "wf", "recurrence", client=client)

    assert client.calls == [
        ("list", "workflows/wf/triggers", None, None, None),
        ("get", "workflows/wf/triggers/recurrence", None),
    ]
    assert listed["nextContinuationToken"] is None
    assert listed["synthesised"]["clientSidePaging"] is True
    assert listed["value"] == [{
        "triggerName": "manual",
        "workflow": "wf",
        "type": "Request",
        "kind": "Http",
        "state": "Enabled",
        "recurrence": None,
        "inputs": {"schema": {"type": "object"}},
        "conditions": None,
        "splitOn": None,
        "metadata": None,
    }]
    assert shown["triggerName"] == "recurrence"
    assert shown["recurrence"] == {"frequency": "Minute", "interval": 1}


def test_trigger_list_client_side_paging_limits_and_continues_to_next_item():
    client = _Client([{"value": [
        {"name": "manual", "properties": {"type": "Request"}},
        {"name": "recurrence", "properties": {"type": "Recurrence"}},
    ]}, {"value": [
        {"name": "manual", "properties": {"type": "Request"}},
        {"name": "recurrence", "properties": {"type": "Recurrence"}},
    ]}])

    first = trigger_list(_Cmd(), "rg", "site", "wf", max_items=1, client=client)
    second = trigger_list(_Cmd(), "rg", "site", "wf", max_items=1, next_token=first["nextContinuationToken"], client=client)

    assert [item["triggerName"] for item in first["value"]] == ["manual"]
    assert [item["triggerName"] for item in second["value"]] == ["recurrence"]
    assert first["synthesised"]["clientSidePaging"] is True
    assert "applied by the CLI" in first["synthesised"]["reason"]
    # The continuation token is a real value, not None — proves paging is a real
    # thing and not a "return the whole thing anyway" cheat.
    assert first["nextContinuationToken"] is not None
    assert second["nextContinuationToken"] is None


def test_trigger_routes_url_encode_workflow_and_trigger_segments():
    client = _Client([{"name": "manual trigger", "properties": {}}])

    trigger_show(_Cmd(), "rg", "site", "wf/name", "manual trigger", client=client)

    assert client.calls[0][1] == "workflows/wf%2Fname/triggers/manual%20trigger"


def test_trigger_show_schema_maps_request_trigger_schema():
    client = _Client([{"type": "object", "properties": {"id": {"type": "string"}}}])

    result = trigger_show_schema(_Cmd(), "rg", "site", "wf", "manual", client=client)

    assert client.calls == [("get", "workflows/wf/triggers/manual/schemas/json", None)]
    assert result == {
        "workflow": "wf",
        "trigger": "manual",
        "schema": {"type": "object", "properties": {"id": {"type": "string"}}},
    }


def test_trigger_show_schema_404_refuses_with_cause_and_alternative_for_non_request_triggers():
    # The schema route 404s, but the trigger itself reads back, so the trigger
    # exists and simply exposes no request schema: a capability refusal.
    client = _Client([ResourceNotFoundError("not found"), {"name": "recurrence"}])

    with pytest.raises(DesignRefusalError) as raised:
        trigger_show_schema(_Cmd(), "rg", "site", "wf", "recurrence", client=client)

    message = str(raised.value)
    assert "AZ_LOGICAPP_REFUSAL" in message
    assert "only request triggers expose a JSON request schema" in message
    assert "trigger show" in message


def test_trigger_show_schema_404_reports_not_found_when_the_trigger_is_missing():
    """A missing workflow or trigger must keep az's exit-3 not-found contract.

    The schema route answers 404 for two different situations, so the command
    reads the trigger to tell them apart.  Refusing here instead would claim the
    trigger exists but has no schema, which is a false statement about a
    resource that is not there.
    """
    client = _Client([ResourceNotFoundError("not found"), ResourceNotFoundError("not found")])

    with pytest.raises(ResourceNotFoundError):
        trigger_show_schema(_Cmd(), "rg", "site", "wf", "ghost", client=client)

    assert client.calls == [
        ("get", "workflows/wf/triggers/ghost/schemas/json", None),
        ("get", "workflows/wf/triggers/ghost", None),
    ]


def test_trigger_show_callback_url_maps_platform_value_to_unredacted_callback_url():
    """The platform's response body contains the fully credentialed URL; the
    CLI passes it through verbatim.  A regression that redacted the ``code=``
    query segment (a plausible-looking "fix") would break the whole point of
    this endpoint — the URL is what the operator uses to invoke the trigger.
    """
    url = "https://example.invalid/workflows/wf/triggers/manual?api-version=2018-11-01&code=secret"
    client = _Client([{"value": url, "method": "POST", "queries": {"api-version": "2018-11-01", "code": "secret"}}])

    result = trigger_show_callback_url(_Cmd(), "rg", "site", "wf", "manual", client=client)

    assert client.calls == [("post", "workflows/wf/triggers/manual/listCallbackUrl", {}, None)]
    assert result == {
        "triggerName": "manual",
        "callbackUrl": url,
    }
    assert "code=secret" in result["callbackUrl"]


def test_trigger_run_uses_header_run_id_when_returned_by_platform():
    client = _Client([(None, {"x-ms-workflow-run-id": "run2"})])

    result = trigger_run(_Cmd(), "rg", "site", "wf", "manual", client=client)

    assert client.calls == [("post", "workflows/wf/triggers/manual/run", None, None)]
    assert result == {
        "triggerName": "manual",
        "workflow": "wf",
        "runId": "run2",
        "status": "Accepted",
    }


def test_trigger_run_accepted_without_run_id_is_honest_unknown_not_fabricated():
    """workflow's disclosed defect 2: recurrence trigger_run returns ``runId: null``
    and MUST NOT fabricate one.  A regression that filled in the trigger name
    as the run id, or returned status "Succeeded", would silently pass a
    ``checks=[]`` probe (fabricated run id + exit 0 — this is the exact class
    caught previously in the extension prototype).
    """
    client = _Client([None])

    result = trigger_run(_Cmd(), "rg", "site", "wf", "recurrence", client=client)

    assert result["runId"] is None
    assert result["status"] == "AcceptedNewRunIdUnknown"
    assert "will not infer or fabricate" in result["message"]
    assert "No deterministic follow-up run id is available" in result["message"]
    assert "Optional manual workaround" in result["message"]
    assert "under concurrent trigger firings it can attribute the wrong run" in result["message"]
    # No synthesised marker here — this is genuine platform behaviour, not
    # something the CLI synthesised on top of the response.  Adding a
    # ``synthesised`` field would be a HONESTY lie (implying we made
    # ``runId: null`` up, when the platform actually returned no id).
    assert "synthesised" not in result


def test_trigger_help_registered_in_knack_help_files_reaches_disclosure_strings():
    """The help fragment ships as ``_help.py`` under the port (module renamed
    from ``_help_trigger``). Import it and assert the exact disclosure strings
    that would fail workflow defect #1 in reverse (right text in source, wrong text
    in installed help) are present. The end-to-end walk (see
    findings/test-execution.md) exercises the SAME strings against ``az -h``
    rendered output — this is the source-side check; the walk is the rendered
    check.
    """
    _reload_logicapp_help()

    assert "logicapp workflow trigger show-schema" in knack_helps
    assert "logicapp workflow trigger show-callback-url" in knack_helps
    assert "logicapp workflow trigger run" in knack_helps
    show_schema = knack_helps["logicapp workflow trigger show-schema"]
    show_callback = knack_helps["logicapp workflow trigger show-callback-url"]
    trigger_run_help = knack_helps["logicapp workflow trigger run"]

    assert "Only request triggers expose a JSON request schema" in show_schema
    # The callback URL is the credential; the help must not soften that.
    assert "list-callback-url" not in show_callback  # renamed under the port
    assert "runId: null" in trigger_run_help
    assert "manual workaround: record the request time" in trigger_run_help
    assert "concurrent trigger firings it can attribute the wrong run" in trigger_run_help
    # `--no-wait` was dropped before merge (engineer ruling, 2026-09-07). It was a
    # no-op: the CLI returns as soon as the platform accepts the trigger whether or
    # not the flag was passed, and no matching ``wait`` verb exists anywhere in the
    # appservice module. The help must still document the fire-and-forget behaviour
    # -- that part was always true and callers depend on it -- but must no longer
    # advertise a flag the command does not accept. If a future rev re-adds the flag
    # without also shipping a ``wait`` verb, this assertion will fire.
    assert "--no-wait" not in trigger_run_help
    assert "fire-and-forget" in trigger_run_help
    assert "does not poll for terminal state" in trigger_run_help
    assert "No `wait` verb is shipped" in trigger_run_help


# Review fix: reclassify H3 as an instance of the known defect class
# (a documented no-fabrication guarantee that lives in the runtime `message`
# field but is missing from the rendered --help). If the two surfaces disclose
# the same guarantee in two different wordings and nothing keeps them in sync,
# that is a STRUCTURAL gap — the same shape as "help string never reached the
# user". This test enforces the sync: the key phrase that lives on the
# runtime message is a substring of the rendered help long-summary. Prove-fail
# is via mutating one string; captured in guard-proofs/proof-fb4-*.txt.
def test_no_fabrication_guarantee_is_reproduced_in_help_long_summary():
    """H3 sync guard.

    The runtime ``message`` field returned by ``trigger_run`` (when the
    platform declines to disclose a run id) contains the phrase
    ``"will not infer or fabricate a run id"``. That phrase is the
    user-visible commitment that no fabrication will happen. The rendered
    ``--help`` long-summary MUST include the same phrase so a user
    reading the help before invoking the command sees the same
    guarantee that the runtime prints when the command is invoked.

    Two disclosures of the same guarantee that drift out of sync is a
    structural gap of the workflow Defect #1 class ("documented text that did
    not reach the user"). One string reaches the customer at invocation
    time; the other reaches them at read time. If they diverge, the
    customer sees one and not the other.
    """
    from azure.cli.command_modules.appservice.logicapp._trigger import (
        _TRIGGER_RUN_UNKNOWN_MESSAGE,
    )
    _reload_logicapp_help()

    trigger_run_help = knack_helps["logicapp workflow trigger run"]

    # The canonical phrase — pinned by construction. Present in the
    # runtime message; MUST also be in the rendered help.
    canonical_phrase = "will not infer or fabricate a run id"
    assert canonical_phrase in _TRIGGER_RUN_UNKNOWN_MESSAGE, (
        "runtime message must contain the canonical no-fabrication phrase; "
        "found: {!r}".format(_TRIGGER_RUN_UNKNOWN_MESSAGE)
    )
    assert canonical_phrase in trigger_run_help, (
        "help long-summary must repeat the runtime no-fabrication phrase "
        "verbatim so a user reading --help sees the same guarantee that "
        "trigger_run prints when the platform returns no run id. Two "
        "surfaces of the same disclosure with drifted wording is the "
        "workflow Defect #1 class in a mirror: help text that never reflects "
        "the runtime commitment. Missing phrase: {!r}".format(canonical_phrase)
    )

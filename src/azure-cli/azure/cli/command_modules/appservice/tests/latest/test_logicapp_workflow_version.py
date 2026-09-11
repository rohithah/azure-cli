# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Unit tests for the workflow version surface and the run/version link.

Two related changes are covered here, both from the engineer's 2026-09-07 ruling
on ``workflow-versioning-surface.md``:

* **C** -- ``az logicapp workflow version list`` / ``show``, backed by the
  site-runtime routes ``api/management/workflows/{flowName}/versions/`` and
  ``.../versions/{flowVersion}``.
* **B** -- a first-class ``workflowVersion`` field on trigger history output,
  hoisted from the platform's nested ``run.properties.workflow`` object.

The two are tested together because they are only useful together: C makes a
version fetchable, B makes it discoverable from a run. Either alone leaves the
"which definition actually produced this failed run?" question unanswerable.
"""

from azure.cli.core.mock import DummyCli
from knack.help_files import helps as knack_helps

from azure.cli.command_modules.appservice.logicapp._trigger_history import (
    trigger_history_list,
    trigger_history_show,
)
from azure.cli.command_modules.appservice.logicapp._version import (
    VERSION_LIST_MANIFEST,
    VERSION_SHOW_MANIFEST,
    version_list,
    version_show,
)


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
        items = payload if isinstance(payload, list) else payload.get("value", [])
        page, next_token = _slice_items(items, continuation_token=continuation_token, max_items=max_items)
        return {"value": page, "nextContinuationToken": next_token}


def _version_entry(sequence_id, state="Published"):
    """A versions-route entry in the platform's own shape.

    The route names an entry ``{flowName}/{flowSequenceId}`` while run payloads
    reference the bare sequence id; both must resolve to the same value.
    """
    return {
        "name": "specDemo/{}".format(sequence_id),
        "id": "/workflows/specDemo/versions/{}".format(sequence_id),
        "type": "workflows/versions",
        "properties": {
            "version": sequence_id,
            "state": state,
            "createdTime": "2026-09-01T10:00:00Z",
            "changedTime": "2026-09-02T11:30:00Z",
            "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {}},
            "parameters": {"p1": {"type": "String"}},
            "connectionReferences": {"c1": {"connectionName": "conn1"}},
        },
    }


# --------------------------------------------------------------------------
# C -- version list / show
# --------------------------------------------------------------------------

def test_version_list_reads_the_versions_route_and_projects_metadata():
    client = _Client([{"value": [_version_entry("08584129710997436905"),
                                 _version_entry("08584129999999999999")]}])
    result = version_list(_Cmd(), "rg", "app", "specDemo", client=client)

    assert client.calls[0][0] == "list"
    assert client.calls[0][1] == "workflows/specDemo/versions"

    assert [item["version"] for item in result["value"]] == [
        "08584129710997436905", "08584129999999999999"]
    assert all(item["workflow"] == "specDemo" for item in result["value"])
    assert result["value"][0]["state"] == "Published"
    assert result["value"][0]["createdTime"] == "2026-09-01T10:00:00Z"


def test_version_list_omits_definitions():
    # A listing must not carry a full workflow definition per entry; that is
    # what `version show` is for. If this regresses, `version list` on a
    # workflow with many versions becomes unusable.
    client = _Client([{"value": [_version_entry("0858412971")]}])
    result = version_list(_Cmd(), "rg", "app", "specDemo", client=client)

    entry = result["value"][0]
    assert "definition" not in entry
    assert "parameters" not in entry
    assert "connectionReferences" not in entry


def test_version_list_discloses_client_side_paging():
    client = _Client([{"value": [_version_entry("a"), _version_entry("b"), _version_entry("c")]}])
    result = version_list(_Cmd(), "rg", "app", "specDemo", max_items=2, client=client)

    assert len(result["value"]) == 2
    assert result["synthesised"]["clientSidePaging"] is True
    assert "value[].version" in result["synthesised"]["fields"]
    # The route does support server-side paging; the reason field must say we
    # deliberately did not use it, so nobody reads client-side paging here as a
    # platform limitation.
    assert "server-side" in result["synthesised"]["reason"]


def test_version_list_reason_accounts_for_every_field_it_declares():
    # The defect this guards: the reason string explained only paging, while
    # `fields` declared three CLI-added values. A caller reading the disclosure
    # could not tell which were copied from the platform and which the CLI
    # derived -- the same copy-vs-computed ambiguity fixed on trigger history.
    #
    # Naming a field is not enough and a bare substring check on the whole
    # reason is not enough either: with several fields disclosed, dropping one
    # field's classification leaves the others' wording intact, so a whole-string
    # assertion still passes. Each field is checked in its OWN sentence.
    client = _Client([{"value": [_version_entry("08584129710997436905")]}])
    result = version_list(_Cmd(), "rg", "app", "specDemo", client=client)

    synthesised = result["synthesised"]
    reason = synthesised["reason"]
    sentences = [s for s in reason.split(". ") if s.strip()]
    # "minted by the CLI" was added when `nextContinuationToken` was found to be
    # falsely attributed to the platform. It is a distinct classification from
    # "CLI-computed": that describes a value derived from platform data, whereas
    # the paging cursor has no platform counterpart at all.
    derivations = ("copied verbatim", "CLI-computed", "echoed back", "minted by the CLI")

    for field in synthesised["fields"]:
        owning = [s for s in sentences if field in s]
        assert owning, "declared field %r is not explained in the reason" % field
        assert any(d in s for s in owning for d in derivations), (
            "field %r is named but never classified as copied, computed or echoed; "
            "the reader cannot tell where the value came from" % field
        )


def test_version_list_reason_explains_the_composite_name_normalisation():
    # `version` is verbatim when the platform supplies properties.version and
    # computed from the composite name otherwise. Both branches ship, so both
    # must be disclosed; a reader who only learns about the verbatim branch
    # cannot explain a value that came from the fallback.
    client = _Client([{"value": [_version_entry("08584129710997436905")]}])
    reason = version_list(_Cmd(), "rg", "app", "specDemo", client=client)["synthesised"]["reason"]

    assert "trailing segment" in reason
    assert "version show --version" in reason


def test_version_show_reads_the_single_version_route_and_returns_the_definition():
    client = _Client([_version_entry("08584129710997436905")])
    result = version_show(_Cmd(), "rg", "app", "specDemo", "08584129710997436905", client=client)

    assert client.calls[0][0] == "get"
    assert client.calls[0][1] == "workflows/specDemo/versions/08584129710997436905"

    assert result["workflow"] == "specDemo"
    assert result["version"] == "08584129710997436905"
    # The definition is the entire point of this command.
    assert result["definition"] == {"triggers": {"manual": {"type": "Request"}}, "actions": {}}
    assert result["parameters"] == {"p1": {"type": "String"}}
    assert result["connectionReferences"] == {"c1": {"connectionName": "conn1"}}


def test_version_show_url_encodes_the_workflow_and_version_segments():
    client = _Client([_version_entry("v/1")])
    version_show(_Cmd(), "rg", "app", "my workflow", "v/1", client=client)
    assert client.calls[0][1] == "workflows/my%20workflow/versions/v%2F1"


def test_version_id_resolves_the_composite_name_to_the_bare_sequence_id():
    # The versions route names entries "{flowName}/{flowSequenceId}" but runs
    # reference the bare sequence id. If list reported the composite name, the
    # value could not be fed back into `version show` or matched against a
    # run's workflowVersion.
    entry = _version_entry("08584129710997436905")
    del entry["properties"]["version"]  # force the fallback path
    client = _Client([{"value": [entry]}])
    result = version_list(_Cmd(), "rg", "app", "specDemo", client=client)
    assert result["value"][0]["version"] == "08584129710997436905"


def test_version_manifest_rows_are_declared():
    assert VERSION_LIST_MANIFEST["capabilityId"] == "CM-021-list"
    assert VERSION_SHOW_MANIFEST["capabilityId"] == "CM-021-show"
    for row in (VERSION_LIST_MANIFEST, VERSION_SHOW_MANIFEST):
        assert row["plane"] == "site-runtime"
        assert row["feasibilityState"] == "supported"
        # A workflow definition is authoring content, not a credential.
        assert row["credentialBearing"] is False


def test_version_help_documents_why_versions_matter():
    assert "logicapp workflow version" in knack_helps
    assert "logicapp workflow version list" in knack_helps
    assert "logicapp workflow version show" in knack_helps
    group_help = knack_helps["logicapp workflow version"]
    # The whole reason this surface exists: a run is pinned to the version that
    # was current when it started. If that disappears from the help, callers
    # lose the reason to reach for these commands at all.
    assert "creates a new workflow version" in group_help
    assert "continue on the version" in group_help
    assert "workflowVersion" in group_help


# --------------------------------------------------------------------------
# B -- the run -> version link on trigger history
# --------------------------------------------------------------------------

def _history_entry(version_object):
    run = {
        "name": "08584129710997436905",
        "id": "/workflows/specDemo/runs/08584129710997436905",
        "properties": {},
    }
    if version_object is not None:
        run["properties"]["workflow"] = version_object
    return {
        "name": "08584129710997436905",
        "properties": {"status": "Failed", "fired": True, "startTime": "2026-09-01T10:00:00Z", "run": run},
    }


_LIVE_VERSION_OBJECT = {
    "id": "/workflows/specDemo/versions/08584129710997436905",
    "name": "08584129710997436905",
    "type": "workflows/versions",
}


def test_trigger_history_list_hoists_the_executed_workflow_version():
    # Shape is the live-observed one recorded in the escalation: the platform
    # nests the version identity at run.properties.workflow.
    client = _Client([{"value": [_history_entry(_LIVE_VERSION_OBJECT)]}])
    result = trigger_history_list(_Cmd(), "rg", "app", "specDemo", "manual", client=client)

    assert result["value"][0]["workflowVersion"] == "08584129710997436905"
    assert "value[].workflowVersion" in result["synthesised"]["fields"]


def test_trigger_history_show_hoists_the_executed_workflow_version():
    # NOTE: this proves the hoist logic, not that the data arrives. The
    # platform's singular history route can return `run` without the nested
    # `properties.workflow` object even though the CLI requests
    # $expand=run/properties -- see the asymmetry test below. A synthetic
    # payload that always carries the version would hide that entirely.
    client = _Client([_history_entry(_LIVE_VERSION_OBJECT)])
    result = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", "hist1", client=client)
    assert result["workflowVersion"] == "08584129710997436905"


def test_trigger_history_show_declares_workflow_version_in_its_disclosure():
    # The payload and the disclosure must agree. `show` emitted workflowVersion
    # while omitting it from synthesised.fields -- a disclosure that contradicts
    # the payload, on the one field whose honesty is the basis of the
    # no-fabrication guarantee.
    client = _Client([_history_entry(_LIVE_VERSION_OBJECT)])
    result = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", "hist1", client=client)
    assert "workflowVersion" in result["synthesised"]["fields"]
    # Declared even when the platform supplied nothing: the top-level field is
    # CLI-added either way, so its provenance always needs disclosing.
    client = _Client([_history_entry(None)])
    result = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", "hist1", client=client)
    assert result["workflowVersion"] is None
    assert "workflowVersion" in result["synthesised"]["fields"]


def test_both_leaves_declare_every_cli_added_field_they_emit():
    # Structural guard for the whole class, not just workflowVersion. Any
    # top-level key the CLI synthesises or hoists must appear in
    # synthesised.fields. `show` previously emitted workflowVersion without
    # declaring it and nothing caught the drift, because every existing test
    # checked one named field at a time.
    #
    # historyId is included only because this entry's platform name matches the
    # requested id, which is the case in which the CLI projects it. When they
    # differ the value came from the platform resource name and is correctly
    # left undeclared -- so the set below is the always-CLI-added set plus that
    # one conditional field, deliberately exercised in its declared state.
    cli_added = {"workflow", "trigger", "historyId", "workflowVersion"}
    entry_id = "08584129710997436905"

    show_client = _Client([_history_entry(_LIVE_VERSION_OBJECT)])
    shown = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", entry_id, client=show_client)
    declared = set(shown["synthesised"]["fields"])
    assert cli_added <= declared, "undeclared CLI-added fields on show: {}".format(cli_added - declared)

    list_client = _Client([{"value": [_history_entry(_LIVE_VERSION_OBJECT)]}])
    listed = trigger_history_list(_Cmd(), "rg", "app", "specDemo", "manual", client=list_client)
    declared = {field.replace("value[].", "") for field in listed["synthesised"]["fields"]}
    assert cli_added <= declared, "undeclared CLI-added fields on list: {}".format(cli_added - declared)


def test_singular_history_route_may_omit_the_version_and_the_disclosure_says_so():
    # Live-observed platform asymmetry: for the SAME history entry, the
    # collection route returns run.properties.workflow while the singular route
    # returns only run.id. The CLI sends $expand=run/properties on both, so this
    # is platform behaviour, not CLI wiring. A caller who hits null on `show`
    # needs to be told the value is recoverable from `list` rather than
    # concluding the run has no version.
    client = _Client([{"name": "hist1", "properties": {"status": "Failed", "run": {"id": "/workflows/specDemo/runs/0858"}}}])
    result = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", "hist1", client=client)

    assert result["workflowVersion"] is None
    assert client.calls[0][2] == {"$expand": "run/properties"}
    reason = result["synthesised"]["reason"]
    assert "copied verbatim" in reason
    assert "trigger history list" in reason


def test_workflow_version_falls_back_to_the_resource_id_when_name_is_absent():
    client = _Client([_history_entry({"id": "/workflows/specDemo/versions/08584129710997436905",
                                      "type": "workflows/versions"})])
    result = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", "hist1", client=client)
    assert result["workflowVersion"] == "08584129710997436905"


def test_workflow_version_is_none_when_the_platform_does_not_supply_it():
    # No-fabrication rule: an absent version must read as unknown, never be
    # inferred from "the latest version" -- inferring it would produce exactly
    # the wrong-definition diagnosis this field exists to prevent.
    client = _Client([_history_entry(None)])
    result = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", "hist1", client=client)
    assert result["workflowVersion"] is None


def test_workflow_version_is_none_when_the_version_object_is_present_but_unparseable():
    # Sharper than the test above, and the one that actually guards against a
    # fallback being introduced. The absent-object case returns None from an
    # early type check, so it stays green even if someone adds a "guess the
    # latest version" fallback further down. This case reaches that code path:
    # the platform supplied a workflow reference, but neither a name nor a
    # parseable versioned id. The only honest answer is still None.
    client = _Client([_history_entry({"type": "workflows/versions"})])
    result = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", "hist1", client=client)
    assert result["workflowVersion"] is None

    # An id that is not a versioned resource id must not be mined for a value.
    client = _Client([_history_entry({"id": "/workflows/specDemo", "type": "workflows/versions"})])
    result = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", "hist1", client=client)
    assert result["workflowVersion"] is None


def test_workflow_version_survives_a_run_reference_that_is_a_bare_string():
    client = _Client([{"name": "h1", "properties": {"status": "Failed", "run": "/workflows/specDemo/runs/0858"}}])
    result = trigger_history_show(_Cmd(), "rg", "app", "specDemo", "manual", "h1", client=client)
    assert result["runId"] == "0858"
    assert result["workflowVersion"] is None


def test_version_reported_by_history_is_accepted_by_version_show():
    # The two halves of the ruling must compose: the identifier B surfaces has
    # to be the identifier C accepts. If these two ever drift apart, the
    # debugging path silently breaks even though both commands still "work".
    history_client = _Client([{"value": [_history_entry(_LIVE_VERSION_OBJECT)]}])
    history = trigger_history_list(_Cmd(), "rg", "app", "specDemo", "manual", client=history_client)
    discovered = history["value"][0]["workflowVersion"]

    version_client = _Client([_version_entry(discovered)])
    shown = version_show(_Cmd(), "rg", "app", "specDemo", discovered, client=version_client)

    assert version_client.calls[0][1] == "workflows/specDemo/versions/{}".format(discovered)
    assert shown["version"] == discovered
    assert shown["definition"] is not None


def test_no_shipped_disclosure_uses_the_ambiguous_word_projected():
    """The word "projected" hides the distinction the disclosure exists to make.

    Review found three `synthesised.reason` strings using "projected" for both
    values copied verbatim from the platform AND values the CLI derived. A
    caller cannot act on that: a verbatim copy can be trusted as platform truth,
    a computed value cannot. This guard is deliberately vocabulary-level and
    cross-command, because the defect recurred once per command as each was
    written -- fixing the instances without banning the word leaves the next
    author free to reintroduce it.
    """
    version_reason = version_list(
        _Cmd(), "rg", "app", "specDemo",
        client=_Client([{"value": [_version_entry("08584129710997436905")]}]),
    )["synthesised"]["reason"]

    listed = trigger_history_list(
        _Cmd(), "rg", "app", "specDemo", "manual",
        client=_Client([{"value": [_history_entry({"name": "08584129710997436905"})]}]),
    )["synthesised"]["reason"]

    shown = trigger_history_show(
        _Cmd(), "rg", "app", "specDemo", "manual", "08584129710997436905",
        client=_Client([_history_entry({"name": "08584129710997436905"})]),
    )["synthesised"]["reason"]

    for label, reason in (("version list", version_reason),
                          ("trigger history list", listed),
                          ("trigger history show", shown)):
        assert "projected" not in reason, (
            "%s discloses a field as 'projected', which does not tell the caller "
            "whether the value was copied from the platform or computed by the CLI" % label
        )
        # And the replacement vocabulary must actually be present, so the word
        # cannot simply be deleted to satisfy the ban above.
        assert "copied verbatim" in reason, "%s no longer states what is verbatim" % label


def test_paging_cursor_is_cli_minted_and_is_never_attributed_to_the_platform():
    """``nextContinuationToken`` is the CLI's own cursor, and must say so.

    This guard exists because the previous disclosure fix introduced a worse
    defect than the one it removed. Replacing the ambiguous word "projected"
    produced the precise-sounding sentence "nextContinuationToken is copied
    verbatim from the response" -- factually false. ``_slice_items`` mints the
    token via ``_encode_continuation`` as ``base64({"offset": N})``; the value
    read at ``_version.py`` comes from the client's own return dict, not the
    wire. An agent reading that sentence would trust a CLI-internal cursor as a
    platform value and could try to hand it to another client.

    The earlier vocabulary guards could not catch it: a false attribution
    passes a per-field classification check and a ban on the word "projected"
    while still being wrong about which side computed the value. So this guard
    asserts the *fact* -- the wire carries no token, therefore any token the
    command returns was necessarily minted here -- and only then checks that
    the prose agrees.
    """
    from azure.cli.command_modules.appservice.logicapp._runtime_client import _decode_continuation

    entries = [_version_entry("0858412971099743690%d" % i) for i in range(3)]
    # The wire payload carries no continuation token of any kind.
    payload = {"value": entries}
    assert "nextContinuationToken" not in payload

    result = version_list(_Cmd(), "rg", "app", "specDemo", max_items=2, client=_Client([payload]))

    token = result["nextContinuationToken"]
    assert token, "expected a cursor when the collection is longer than --max-items"
    # It decodes as the CLI's own offset cursor. A platform-supplied opaque
    # token would not, so this is proof of origin rather than of wording.
    assert _decode_continuation(token) == 2

    reason = result["synthesised"]["reason"]
    assert result["synthesised"]["clientSidePaging"] is True
    assert "minted by the CLI" in reason, (
        "version list must state that nextContinuationToken is CLI-minted"
    )
    for claim in ("nextContinuationToken is copied verbatim",
                  "copied verbatim from the response"):
        assert claim not in reason, (
            "version list attributes its own paging cursor to the platform (%r); "
            "a caller cannot distinguish a CLI-internal cursor from a platform value" % claim
        )

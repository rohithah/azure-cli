# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Unit tests for the workflow ``mock list`` and ``unit-test create`` command bodies.

Adapted from the extension prototype's unit-test-generation tests.  Port
renames (see {TEAM_ROOT}\\findings\\port-execution.md):

* ``run_list_mockable_operations`` + ``run_list_mockable_http_operations``
  collapsed to ``mock_list(http=<bool>)``; command verb
  ``list-mockable-operations`` / ``list-mockable-http-operations`` -> ``mock list [--http]``.
* ``run_generate_unit_test`` -> ``unit_test_create``; command verb
  ``run generate-unit-test`` -> ``unit-test create``.
* Signature change: ``mock_list`` no longer takes ``workflow`` or ``run_id`` —
  the platform route is global-only and taking those arguments would have
  implied run-scoping the CLI cannot honestly deliver. This IS the workflow defect
  being stayed disclosed post-port.

The load-bearing "returns a GLOBAL catalog, not a run-scoped list" disclosure
survives verbatim in ``meaning`` + the ``synthesised`` marker, and is asserted
here on real content (not exit code).
"""

import io
import json
import re
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest
from azure.cli.core.azclierror import CLIInternalError, FileOperationError, ValidationError
from azure.cli.core.mock import DummyCli
from knack.help_files import helps

from azure.cli.command_modules.appservice.logicapp import _run_unit_test
from azure.cli.command_modules.appservice.logicapp._run_unit_test import (
    GENERATED_UNIT_TEST_SCHEMA_VERSION,
    MOCKABLE_OPERATION_LIST_SCHEMA_VERSION,
    mock_list,
    unit_test_create,
)
from azure.cli.command_modules.appservice.tests.latest._guard_vacuity_scope import derived_scope


class _Cmd:
    cli_ctx = DummyCli()


class _Client:
    def __init__(self, get_payload=None, zip_bytes=None):
        self.get_payload = get_payload
        self.zip_bytes = zip_bytes
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        return self.get_payload

    def list(self, path, params=None, continuation_token=None, max_items=None):
        from azure.cli.command_modules.appservice.logicapp._runtime_client import _slice_items
        self.calls.append(("list", path, params, continuation_token, max_items))
        payload = self.get_payload
        items = payload if isinstance(payload, list) else payload.get("value", [])
        page, next_token = _slice_items(items, continuation_token=continuation_token, max_items=max_items)
        return {"value": page, "nextContinuationToken": next_token}

    def post_raw(self, path, body=None):
        self.calls.append(("post_raw", path, body))
        return self.zip_bytes


def _zip_with_mock(mock_document):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("unit-mock.json", json.dumps(mock_document))
    return buffer.getvalue()


def _json_from_zip(zip_bytes):
    with ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".json")]
        assert len(names) == 1
        return json.loads(archive.read(names[0]).decode("utf-8-sig"))


def _scratch_dir():
    root = Path("test-results-unit")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    return root


def test_mock_list_uses_global_static_route_and_says_not_run_specific():
    """workflow defect #4: the platform route returns a GLOBAL catalog regardless
    of the workflow or run.  The port removed the ``--workflow`` / ``--run-id``
    arguments to make that impossibility explicit at the argument level, and
    the response envelope keeps the disclosure verbatim.
    A naive ``checks=[]`` probe would see exit-0 and a value list and pass;
    this test fails if the disclosure fields go missing OR change value.
    """
    client = _Client(get_payload=["Compose", "Http"])

    result = mock_list(_Cmd(), "rg", "site", client=client)

    assert client.calls == [("list", "listMockableOperations", None, None, None)]
    assert result["schemaVersion"] == MOCKABLE_OPERATION_LIST_SCHEMA_VERSION
    assert result["value"] == ["Compose", "Http"]
    assert result["httpOnly"] is False
    assert result["referenceScope"] == "global-operation-type-catalog"
    assert result["itemKind"] == "operation-type-name"
    assert result["operationSchemaIncluded"] is False
    assert result["runScoped"] is False
    assert result["workflowScoped"] is False
    # These four labels are load-bearing: they are the CLI's contract to the
    # user that this list is NOT the run-scoped answer they might expect.
    assert "meaning" in result["synthesised"]["fields"]
    assert "runScoped" in result["synthesised"]["fields"]
    assert "workflowScoped" in result["synthesised"]["fields"]
    assert result["synthesised"]["clientSidePaging"] is True


def test_mock_list_meaning_field_is_the_disclosure_that_would_have_prevented_m2_defect_4():
    """A separate, single-purpose test on the exact disclosure string.  If
    the copy is softened (e.g. "list of mockable operation types" without
    "not a run-scoped list"), that is precisely the workflow defect being
    smuggled back in.
    """
    client = _Client(get_payload=["Compose"])
    result = mock_list(_Cmd(), "rg", "site", client=client)
    assert "Global catalog of mockable operation types" in result["meaning"]
    assert "not a run-scoped list" in result["meaning"]
    assert "not operations from the supplied run and not operation schemas" in result["meaning"]


def test_mock_list_http_only_uses_http_only_route_and_reports_http_only_flag():
    client = _Client(get_payload={"value": ["Http", "HttpWebhook"]})

    result = mock_list(_Cmd(), "rg", "site", http=True, client=client)

    assert client.calls == [("list", "listMockableHttpOperations", None, None, None)]
    assert result["httpOnly"] is True
    assert result["value"] == ["Http", "HttpWebhook"]
    assert result["referenceScope"] == "global-operation-type-catalog"


def test_mock_list_client_side_paging_limits_and_continues():
    client = _Client(get_payload=["First", "Second", "Third"])

    first = mock_list(_Cmd(), "rg", "site", max_items=1, client=client)
    client.get_payload = ["First", "Second", "Third"]
    second = mock_list(_Cmd(), "rg", "site", max_items=1, next_token=first["nextContinuationToken"], client=client)

    assert first["value"] == ["First"]
    assert second["value"] == ["Second"]
    assert first["nextContinuationToken"] is not None
    assert first["synthesised"]["clientSidePaging"] is True


def test_unit_test_create_sends_only_unit_test_name_warns_and_writes_redacted_zip_bytes(caplog):
    root = _scratch_dir()
    try:
        zip_bytes = _zip_with_mock({"TriggerMocks": {"manual": {"status": "Succeeded"}}, "ActionMocks": {}})
        client = _Client(zip_bytes=zip_bytes)
        output = root / "unit-test.zip"

        result = unit_test_create(_Cmd(), "rg", "site", "wf", "run1", "checkoutTest", output_file=str(output), client=client)

        assert "redacted by the CLI before emission" in caplog.text
        assert "credential-bearing URI query parameters" in caplog.text
        assert "review before committing" in caplog.text
        assert client.calls == [("post_raw", "workflows/wf/runs/run1/generateUnitTest", {"UnitTestName": "checkoutTest"})]
        assert output.read_bytes() != zip_bytes
        written = _json_from_zip(output.read_bytes())
        assert written["TriggerMocks"]["manual"]["status"] == "Succeeded"
        # The redaction marker is present even when nothing needed redaction —
        # that is the D5 "guard never observed to fail" fix: every JSON entry
        # carries the marker, always, so bypass is behaviourally detectable.
        assert written["x-logicapp-cli-redaction"]["redactionApplied"] is False
        assert written["x-logicapp-cli-redaction"]["redactedQueryParameters"] == []
        assert result["schemaVersion"] == GENERATED_UNIT_TEST_SCHEMA_VERSION
        assert result["artifact"] == "generated unit-test zip written to local file"
        assert result["artifactPath"].endswith("unit-test.zip")
        assert result["contentType"] == "application/zip"
        assert result["contentSizeBytes"] == output.stat().st_size
        # The `synthesised` marker names that artifactPath / contentSizeBytes
        # are CLI-side calculations, not fields the platform returned — this
        # is the honest disclaimer that the JSON is metadata, not the artifact.
        assert "metadata and is not the generated unit-test artifact" in result["synthesised"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unit_test_create_without_output_file_streams_raw_zip_bytes(monkeypatch):
    zip_bytes = _zip_with_mock({"TriggerMocks": {}, "ActionMocks": {"A": {"status": "Succeeded"}}})
    client = _Client(zip_bytes=zip_bytes)
    stream = io.BytesIO()

    class _Stdout:
        buffer = stream

    monkeypatch.setattr(_run_unit_test.sys, "stdout", _Stdout())

    result = unit_test_create(_Cmd(), "rg", "site", "wf", "run1", "checkoutTest", client=client)

    assert result is None
    streamed = _json_from_zip(stream.getvalue())
    assert streamed["ActionMocks"]["A"]["status"] == "Succeeded"
    assert streamed["x-logicapp-cli-redaction"]["redactionApplied"] is False


def test_unit_test_create_write_failure_does_not_exit_success_or_leave_partial_file():
    root = _scratch_dir()
    try:
        zip_bytes = _zip_with_mock({"TriggerMocks": {"manual": {}}, "ActionMocks": {}})
        client = _Client(zip_bytes=zip_bytes)
        output_directory = root / "already-directory"
        output_directory.mkdir()

        with pytest.raises(FileOperationError, match="path is a directory"):
            unit_test_create(_Cmd(), "rg", "site", "wf", "run1", "checkoutTest", output_file=str(output_directory), client=client)

        assert not (root / ".already-directory.__logicapp_partial").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unit_test_create_empty_mock_artifact_is_explicit_failure_with_reason():
    root = _scratch_dir()
    try:
        zip_bytes = _zip_with_mock({"TriggerMocks": {}, "ActionMocks": {}})
        client = _Client(zip_bytes=zip_bytes)

        with pytest.raises(ValidationError) as exc_info:
            unit_test_create(_Cmd(), "rg", "site", "wf", "run1", "checkoutTest", output_file=str(root / "empty.zip"), client=client)

        message = str(exc_info.value)
        assert "contains no eligible trigger or action mocks" in message
        # D3: cause AND alternative. Both are asserted.
        assert "Alternative: rerun after a workflow run with at least one eligible operation" in message
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unit_test_create_help_registers_disclosure_that_generated_artifact_is_mock_definition_not_runnable_test():
    """workflow defect #3: the artifact this command produces is a mock-definition
    JSON zip, not a runnable test.  The disclosure that names it clearly
    ("mock-definition zip artifact") is load-bearing; a "friendly" rewrite
    to "unit-test project" would be an honesty regression.
    """
    import azure.cli.command_modules.appservice.logicapp._help  # noqa: F401

    assert "logicapp workflow unit-test create" in helps
    text = helps["logicapp workflow unit-test create"]
    assert "mock-definition" in text
    assert "not a runnable test" in text
    assert "redacts site-runtime tokens" in text
    assert "x-logicapp-cli-redaction" in text
    assert "logicapp workflow mock list" in helps
    mock_text = helps["logicapp workflow mock list"]
    # The "global catalog" disclosure appears in help too, not just in the
    # response envelope — this is where documented help meets the same
    # workflow-defect-4 assertion as the response.
    assert "global catalog" in mock_text
    assert "not a run-scoped list" in mock_text


def test_generate_unit_test_redacts_credential_bearing_query_parameters_by_class():
    root = _scratch_dir()
    try:
        credential_url = (
            "https://example.invalid/path?sig=signature-value&sp=rw&sv=2024-01-01&se=2030-01-01"
            "&st=2026-01-01&sr=b&code=function-key&access_token=token-value&sas=sas-value"
            "&customSignature=signature-like&sessionToken=token-like&businessId=kept"
        )
        zip_bytes = _zip_with_mock({
            "triggerMocks": {
                "manual": {
                    "outputs": {
                        "body": {
                            "callback": credential_url,
                            "benign": "https://example.invalid/path?businessId=123&category=code-sample",
                        }
                    },
                },
            },
            "actionMocks": {},
        })
        client = _Client(zip_bytes=zip_bytes)

        result = unit_test_create(_Cmd(), "rg", "site", "wf", "run1", "checkoutTest", output_file=str(root / "query-redacted.zip"), client=client)

        document = _json_from_zip(Path(result["artifactPath"]).read_bytes())
        callback = document["triggerMocks"]["manual"]["outputs"]["body"]["callback"]
        assert "businessId=kept" in callback
        assert "<redacted-by-az-logicapp-cli>" in callback
        # Every named credential-class parameter is redacted, benign ones pass through.
        assert not _credential_query_parameter_regex().search(json.dumps(document))
        assert document["triggerMocks"]["manual"]["outputs"]["body"]["benign"].endswith("businessId=123&category=code-sample")
        metadata = document["x-logicapp-cli-redaction"]
        assert metadata["synthesised"] is True
        assert metadata["redactionApplied"] is True
        assert metadata["redactedQueryParameters"] == [
            "access_token", "code", "customSignature", "sas", "se", "sessionToken",
            "sig", "sp", "sr", "st", "sv",
        ]
    finally:
        shutil.rmtree(root, ignore_errors=True)


# Guard Integrity vacuity-mechanism finding (2026-08-31): this test's oracle
# used to hardcode its OWN independent copy of the credential-class query
# parameter names (a second, hand-maintained ``sig|sp|sv|...`` list living
# only in the test file). Production's redaction name-lists
# (``_REDACT_EXACT_QUERY_PARAMETER_NAMES`` /
# ``_REDACT_QUERY_PARAMETER_NAME_MARKERS`` in ``_run_unit_test.py``) could be
# renamed, restructured, or extended without this duplicate ever noticing --
# a production regression on a renamed/new credential class would be
# invisible to this test, because the test's own oracle never knew the new
# name either. This is the exact "name-coupled selector, highest-risk class"
# defect the widened standing rule names, just with the duplication running
# test-side instead of a rename running production-side.
#
# Fixed by DERIVING the name fragments from the real production constants
# (keeping the exact-match-vs-substring-marker MATCHING LOGIC independently
# written here, so this test still independently verifies the redaction
# behaviour rather than asking the implementation-under-test to grade
# itself). Each derivation is floor-checked via ``@derived_scope``: if either
# list should ever come back empty, the guard fails loudly instead of
# quietly becoming an oracle that can never find anything.
@derived_scope(
    "credential-bearing-query-parameters-are-redacted (exact-name fragments)",
    "_run_unit_test._REDACT_EXACT_QUERY_PARAMETER_NAMES came back empty -- "
    "production's exact credential-parameter name list was emptied, renamed, "
    "or restructured; re-derive this test's oracle to match the new shape.",
)
def _derive_exact_credential_query_parameter_names():
    return sorted(_run_unit_test._REDACT_EXACT_QUERY_PARAMETER_NAMES)  # pylint: disable=protected-access


@derived_scope(
    "credential-bearing-query-parameters-are-redacted (substring markers)",
    "_run_unit_test._REDACT_QUERY_PARAMETER_NAME_MARKERS came back empty -- "
    "production's credential-parameter substring-marker list was emptied, "
    "renamed, or restructured; re-derive this test's oracle to match the "
    "new shape.",
)
def _derive_credential_query_parameter_name_markers():
    return sorted(_run_unit_test._REDACT_QUERY_PARAMETER_NAME_MARKERS)  # pylint: disable=protected-access


def _credential_query_parameter_regex():
    exact_names = _derive_exact_credential_query_parameter_names()
    markers = _derive_credential_query_parameter_name_markers()
    exact_alternation = "|".join(re.escape(name) for name in exact_names)
    marker_alternation = "|".join(re.escape(marker) for marker in markers)
    return re.compile(
        r"(?i)(?:[?&;]|&amp;)"
        r"(?:(?:{exact})|(?:[^=&#;\s\"'<>]*(?:{markers})[^=&#;\s\"'<>]*))"
        r"=".format(exact=exact_alternation, markers=marker_alternation)
    )


def test_redaction_preserves_header_reading_mock_resolution_for_non_redacted_header():
    """A workflow may READ a non-redacted header from the mock; the redactor
    must not touch string values on non-redacted headers, even when those
    values look like expression syntax.  This exists to prevent a "helpful"
    over-broad redaction that would break the mock resolution the artifact
    is supposed to enable.
    """
    zip_bytes = _zip_with_mock({
        "triggerMocks": {
            "manual": {
                "outputs": {
                    "headers": {
                        "x-ms-site-token": "header.payload.signature",
                        "x-experiment-header": "header-experiment-value-F",
                    },
                    "body": {"echo": "@triggerOutputs()?['headers']?['x-experiment-header']"},
                },
            },
        },
        "actionMocks": {},
    })

    redacted = _run_unit_test._redact_generated_unit_test_zip(zip_bytes)  # pylint: disable=protected-access
    document = _json_from_zip(redacted)

    assert document["triggerMocks"]["manual"]["outputs"]["headers"]["x-ms-site-token"] == "<redacted-by-az-logicapp-cli>"
    assert document["triggerMocks"]["manual"]["outputs"]["headers"]["x-experiment-header"] == "header-experiment-value-F"
    assert document["triggerMocks"]["manual"]["outputs"]["body"]["echo"] == "@triggerOutputs()?['headers']?['x-experiment-header']"

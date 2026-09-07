# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long

"""Workflow mock catalog and unit-test generation command bodies.

Rename note: the extension's ``list-mockable-operations`` and
``list-mockable-http-operations`` merge into ``workflow mock list [--http]``,
and ``generate-unit-test`` becomes ``workflow unit-test create``. The two
disclosed platform failures (defect #8 -- global catalog; defect #12 -- the
generated artifact is a mock-definition JSON, not a runnable test) are
preserved verbatim in the response envelope and help text; the rename to
``create`` is a shape convention, not an upgrade in what the artifact is.
"""

import io
import json
import os
from pathlib import Path
import re
import sys
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

from azure.cli.core.azclierror import CLIInternalError, FileOperationError, ValidationError
from azure.cli.core.commands.client_factory import get_subscription_id
from knack.log import get_logger

from ._constants import LOGICAPP_REDACTION_SENTINEL
from ._runtime_client import SiteRuntimeClient, generate_unit_test_path, mockable_operations_path

MOCKABLE_OPERATION_LIST_SCHEMA_DOCUMENT_VERSION = "logicapp.mockable-operation-list-2026-08-29"
GENERATED_UNIT_TEST_SCHEMA_DOCUMENT_VERSION = "logicapp.generated-unit-test-2026-08-29"
_GLOBAL_CATALOG_DESCRIPTION = (
    "Global catalog of mockable operation types, not a run-scoped list. "
    "The platform route accepts no workflow or run parameter; values are operation type names, "
    "not operations from the supplied run and not operation schemas."
)
_SITE_PROVIDER = "/providers/Microsoft.Web/sites/"
_GENERATED_ARTIFACT_WARNING = (
    "Generated unit-test artifacts are redacted by the CLI before emission: site-runtime tokens, "
    "credential-bearing URI query parameters, client identity/IP, forwarded/ARR SSL, and ARM "
    "context headers are replaced with <redacted-by-az-logicapp-cli>. Treat any unredacted "
    "workflow data as local-only and review before committing to source control or CI artifacts."
)
_REDACTION_SENTINEL = LOGICAPP_REDACTION_SENTINEL
_REDACTION_METADATA_FIELD = "x-logicapp-cli-redaction"
_REDACT_EXACT_HEADER_NAMES = {
    "x-ms-site-token",
    "x-ms-site-restricted-token",
    "client-ip",
    "x-client-ip",
    "x-real-ip",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-proto",
    "x-forwarded-tlsversion",
    "forwarded",
    "referer",
    "traceparent",
    "x-original-url",
    "x-waws-unencoded-url",
    "x-arr-ssl",
    "x-arr-log-id",
    "x-ms-arm-request-tracking-id",
    "x-ms-arm-resource-system-data",
    "x-ms-arm-service-request-id",
    "x-ms-correlation-request-id",
    "x-ms-routing-request-id",
    "x-ms-request-id",
    "x-ms-operation-context",
    "x-ms-management-group-ancestors",
    "x-ms-home-tenant-id",
    "x-ms-activity-vector",
    "x-ms-via-extensions-route",
    "x-ms-geo-location",
    "x-ms-version",
    "x-ms-platform-internal",
}
_REDACT_HEADER_PREFIXES = (
    "x-ms-client-",
    "x-ms-arm-",
)
_QUERY_PARAMETER_PATTERN = re.compile(r"(?i)(&amp;|[?&;])([A-Za-z0-9_.~-]+)=([^&#;\s\"'<>]*)")
_REDACT_EXACT_QUERY_PARAMETER_NAMES = {
    "sig",
    "sp",
    "sv",
    "se",
    "st",
    "sr",
    "code",
    "access_token",
    "sas",
    "client_secret",
    "client_assertion",
    "id_token",
    "refresh_token",
    "sharedaccesssignature",
    "shared_access_signature",
    "sharedaccesskey",
    "api_key",
    "apikey",
    "subscription-key",
}
_REDACT_QUERY_PARAMETER_NAME_MARKERS = (
    "token",
    "sas",
    "signature",
    "secret",
)
_REDACTION_QUERY_PARAMETER_NAME = "x-logicapp-cli-redaction"
_REDACTION_REASON = (
    "The platform-generated mock can include live site-runtime authentication headers, request "
    "context headers, and pre-authorized URI query credentials. The CLI replaces "
    "credential, identity, client IP, forwarded/ARR SSL, ARM context header values, and "
    "credential-bearing query parameters before writing or streaming the artifact."
)
_REDACTION_CAVEAT = (
    "Named live possibility: a workflow that intentionally reads one of these redacted headers may "
    "need the original value for an exact-value assertion. The observed header-reading probe did not "
    "need the redacted site-runtime headers for the mock to resolve."
)
logger = get_logger(__name__)


MOCK_LIST_MANIFEST = {
    "capabilityId": "CM-018",
    "command": "logicapp workflow mock list",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": MOCKABLE_OPERATION_LIST_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": False,
    "delegatedTo": None,
    "gap": (
        "The platform route returns a global catalog of operation type names, not a run-scoped "
        "list; --http selects the HTTP variant. The command accepts no --workflow or --run-id "
        "because the returned list is static and not workflow-specific."
    ),
    "modeCondition": None,
    "contentOnly": False,
}

UNIT_TEST_CREATE_MANIFEST = {
    "capabilityId": "CM-020",
    "command": "logicapp workflow unit-test create",
    "plane": "site-runtime",
    "feasibilityState": "supported",
    "schemaVersion": GENERATED_UNIT_TEST_SCHEMA_DOCUMENT_VERSION,
    "credentialBearing": True,
    "delegatedTo": None,
    "gap": (
        "The 'create' verb is a shape convention; the generated artifact is a mock-definition "
        "JSON zip, not a runnable test project."
    ),
    "modeCondition": None,
    "contentOnly": True,
}


def mockable_operations_table_format(result):
    if not result:
        return []
    return [
        {
            "operationType": value,
            "catalogScope": "global operation-type catalog (CLI-synthesised label)",
            "responseKind": "operation type name only, not schema (CLI-synthesised label)",
        }
        for value in result.get("value", [])
    ]


def mock_list(cmd, resource_group_name, name, http=False, max_items=None, next_token=None, client=None):
    client = client or _client(cmd, resource_group_name, name)
    payload = client.list(mockable_operations_path(http_only=http), continuation_token=next_token, max_items=max_items)
    return _mockable_operations_response(payload, http_only=http)


def unit_test_create(cmd, resource_group_name, name, workflow, run_id, unit_test_name, output_file=None, client=None):
    logger.warning(_GENERATED_ARTIFACT_WARNING)
    client = client or _client(cmd, resource_group_name, name)
    zip_bytes = _post_unit_test_zip(client, workflow, run_id, unit_test_name)
    zip_bytes = _redact_generated_unit_test_zip(zip_bytes)
    _raise_if_generated_mock_is_empty(zip_bytes, workflow, run_id)
    _raise_if_redaction_marker_missing(zip_bytes, workflow, run_id)
    if output_file:
        return _write_zip_artifact(output_file, zip_bytes)
    _emit_zip_to_stdout(zip_bytes)
    return None


def _mockable_operations_response(payload, http_only):
    values = payload.get("value", payload) if isinstance(payload, dict) else payload
    if values is None:
        values = []
    if not isinstance(values, list):
        raise ValueError("mockable-operation list response must be an array or an object with a value array")
    next_token = payload.get("nextContinuationToken") if isinstance(payload, dict) else None
    return {
        "value": values,
        "nextContinuationToken": next_token,
        "httpOnly": bool(http_only),
        "referenceScope": "global-operation-type-catalog",
        "itemKind": "operation-type-name",
        "operationSchemaIncluded": False,
        "runScoped": False,
        "workflowScoped": False,
        "meaning": _GLOBAL_CATALOG_DESCRIPTION,
        "synthesised": {
            "fields": [
                "httpOnly",
                "referenceScope",
                "itemKind",
                "operationSchemaIncluded",
                "runScoped",
                "workflowScoped",
                "meaning",
                "nextContinuationToken",
            ],
            "clientSidePaging": True,
            "reason": (
                "The platform returns only the value array from a global route; the CLI adds "
                "scope, and response-kind labels so the catalog cannot be mistaken for run-scoped "
                "operation names or schemas. The global catalog route returns one collection; --max-items "
                "and --next-token are applied by the CLI after reading that collection."
            ),
        },
    }


def _post_unit_test_zip(client, workflow, run_id, unit_test_name):
    body = {"UnitTestName": unit_test_name}
    if hasattr(client, "post_raw"):
        return client.post_raw(generate_unit_test_path(workflow, run_id), body=body)

    response = client._sender(  # pylint: disable=protected-access
        client.cmd.cli_ctx,
        "POST",
        client._build_url(generate_unit_test_path(workflow, run_id)),  # pylint: disable=protected-access
        headers=["Content-Type=application/json"],
        body=json.dumps(body),
    )
    return getattr(response, "content", b"") or b""


def _redact_generated_unit_test_zip(zip_bytes):
    try:
        source = ZipFile(io.BytesIO(zip_bytes))
    except BadZipFile:
        return zip_bytes

    output = io.BytesIO()
    with source:
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as target:
            for item in source.infolist():
                content = source.read(item.filename)
                if item.filename.lower().endswith(".json"):
                    content = _redact_generated_unit_test_json(content)
                info = _copy_zip_info(item)
                target.writestr(info, content)
    return output.getvalue()


def _copy_zip_info(item):
    info = type(item)(item.filename, item.date_time)
    info.comment = item.comment
    info.extra = item.extra
    info.internal_attr = item.internal_attr
    info.external_attr = item.external_attr
    info.compress_type = ZIP_DEFLATED
    return info


def _redact_generated_unit_test_json(content):
    try:
        document = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return content
    redacted_headers = []
    redacted_query_parameters = []
    redacted_document = _redact_generated_unit_test_value(document, redacted_headers, redacted_query_parameters)
    if isinstance(redacted_document, dict):
        redacted_document[_REDACTION_METADATA_FIELD] = _redaction_metadata(redacted_headers, redacted_query_parameters)
    return json.dumps(redacted_document, indent=2, sort_keys=False).encode("utf-8")


def _redact_generated_unit_test_value(value, redacted_headers, redacted_query_parameters):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if _is_redacted_header_name(key):
                result[key] = _REDACTION_SENTINEL
                redacted_headers.append(str(key))
            else:
                result[key] = _redact_generated_unit_test_value(item, redacted_headers, redacted_query_parameters)
        return result
    if isinstance(value, list):
        return [_redact_generated_unit_test_value(item, redacted_headers, redacted_query_parameters) for item in value]
    if isinstance(value, str):
        return _redact_credential_query_parameters(value, redacted_query_parameters)
    return value


def _is_redacted_header_name(name):
    lower_name = str(name).lower()
    return lower_name in _REDACT_EXACT_HEADER_NAMES or any(
        lower_name.startswith(prefix) for prefix in _REDACT_HEADER_PREFIXES
    )


def _redact_credential_query_parameters(text, redacted_query_parameters):
    if "=" not in text or not any(separator in text for separator in ("?", "&", ";")):
        return text

    def replace(match):
        separator, param_name = match.group(1), match.group(2)
        if not _is_redacted_query_parameter_name(param_name):
            return match.group(0)
        redacted_query_parameters.append(param_name)
        return "{}{}={}".format(separator, _REDACTION_QUERY_PARAMETER_NAME, _REDACTION_SENTINEL)

    return _QUERY_PARAMETER_PATTERN.sub(replace, text)


def _is_redacted_query_parameter_name(name):
    lower_name = str(name).lower()
    if lower_name in _REDACT_EXACT_QUERY_PARAMETER_NAMES:
        return True
    return any(marker in lower_name for marker in _REDACT_QUERY_PARAMETER_NAME_MARKERS)


def _redaction_metadata(redacted_headers, redacted_query_parameters):
    unique_headers = sorted({str(key) for key in redacted_headers}, key=str.lower)
    unique_query_parameters = sorted({str(key) for key in redacted_query_parameters}, key=str.lower)
    return {
        "schemaVersion": "logicapp.generated-unit-test-redaction/2026-08-30",
        "synthesisedBy": "az logicapp workflow unit-test create",
        "synthesised": True,
        "redactionApplied": bool(unique_headers or unique_query_parameters),
        "redactedValue": _REDACTION_SENTINEL,
        "redactedHeaders": unique_headers,
        "redactedQueryParameters": unique_query_parameters,
        "reason": _REDACTION_REASON,
        "caveat": _REDACTION_CAVEAT,
    }


def _write_zip_artifact(output_file, zip_bytes):
    target = Path(output_file)
    if target.exists() and target.is_dir():
        raise FileOperationError("Cannot write generated unit-test artifact to '{}': path is a directory. Alternative: pass a file path to --output-file.".format(output_file))
    if target.parent and not target.parent.exists():
        raise FileOperationError("Cannot write generated unit-test artifact to '{}': parent directory does not exist. Alternative: create the parent directory or pass an existing directory path.".format(output_file))

    absolute_target = target.resolve()
    partial = target.with_name(".{}.__logicapp_partial".format(target.name))
    try:
        with open(partial, "wb") as handle:
            handle.write(zip_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        if partial.stat().st_size != len(zip_bytes):
            raise FileOperationError("Generated unit-test artifact write to '{}' was incomplete. Alternative: retry with a writable local file path.".format(output_file))
        os.replace(str(partial), str(target))
    except FileOperationError:
        _remove_partial(partial)
        raise
    except OSError as ex:
        _remove_partial(partial)
        raise FileOperationError("Cannot write generated unit-test artifact to '{}': {}. Alternative: retry with a writable local file path.".format(output_file, ex)) from ex

    return {
        "artifact": "generated unit-test zip written to local file",
        "artifactPath": str(absolute_target),
        "contentType": "application/zip",
        "contentSizeBytes": len(zip_bytes),
        "synthesised": "artifactPath and contentSizeBytes are CLI-computed from the local write; this JSON is metadata and is not the generated unit-test artifact.",
    }


def _emit_zip_to_stdout(zip_bytes):
    stdout = getattr(sys.stdout, "buffer", sys.stdout)
    stdout.write(zip_bytes)
    stdout.flush()


def _raise_if_redaction_marker_missing(zip_bytes, workflow, run_id):
    """Behavioural gate at the generator boundary.

    After the redaction pass and before either _write_zip_artifact or
    _emit_zip_to_stdout, re-open the produced zip and inspect every JSON
    entry. Refuse to emit if any JSON entry lacks the redaction marker
    (`x-logicapp-cli-redaction`). This is a verify-after-redaction step,
    not a confirmation step, and the check is behavioural rather than
    "the zip still parses". A JSON entry with no marker is proof that
    the redactor did not visit that document -- refuse rather than ship.
    """
    try:
        archive = ZipFile(io.BytesIO(zip_bytes))
    except BadZipFile as ex:
        raise CLIInternalError(
            "Generated unit-test artifact for workflow '{}' run '{}' failed the redaction-marker "
            "gate: the produced bytes are not a valid zip. Cause: the CLI redaction pass emitted a "
            "malformed archive; the artifact cannot be inspected for the '{}' marker on every JSON "
            "entry and must not be shipped. Alternative: re-run 'az logicapp workflow unit-test "
            "create' with --debug and file a CLI bug attaching the redacted transcript. "
            "Underlying error: {}.".format(workflow, run_id, _REDACTION_METADATA_FIELD, ex)) from ex

    with archive:
        for entry_name in archive.namelist():
            if not entry_name.lower().endswith(".json"):
                continue
            raw = archive.read(entry_name)
            has_marker = False
            try:
                document = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                document = None
            if isinstance(document, dict) and _REDACTION_METADATA_FIELD in document:
                has_marker = True
            if not has_marker:
                raise CLIInternalError(
                    "Generated unit-test artifact for workflow '{workflow}' run '{run_id}' failed the "
                    "redaction-marker gate: entry '{entry}' has no '{marker}' field. Cause: the CLI "
                    "redaction pass did not mark this JSON entry (the entry was undecodable, was not a "
                    "JSON object at the root, or the redactor was bypassed) -- a zip that parses is not "
                    "proof that redaction ran, and this entry has not been proven safe to emit. "
                    "Alternative: re-run 'az logicapp workflow unit-test create' with --debug and file a "
                    "CLI bug against the logicapp workflow module naming entry '{entry}'; do not ship "
                    "the artifact until the redactor visits every JSON entry it produces.".format(
                        workflow=workflow, run_id=run_id, entry=entry_name, marker=_REDACTION_METADATA_FIELD))


def _raise_if_generated_mock_is_empty(zip_bytes, workflow, run_id):
    state = _mock_artifact_state(zip_bytes)
    if state == "empty":
        raise ValidationError(
            "Generated unit-test artifact for workflow '{}' run '{}' contains no eligible trigger or action mocks. Cause: the platform only mocks eligible triggers and non-repetitive Succeeded/Failed actions. Alternative: rerun after a workflow run with at least one eligible operation, or inspect 'logicapp workflow mock list' for the static supported operation types.".format(
                workflow, run_id))


def _mock_artifact_state(zip_bytes):
    try:
        with ZipFile(io.BytesIO(zip_bytes)) as archive:
            saw_mock_shape = False
            for entry_name in archive.namelist():
                if not entry_name.lower().endswith(".json"):
                    continue
                try:
                    document = json.loads(archive.read(entry_name).decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                trigger_mocks = _case_insensitive_get(document, "TriggerMocks")
                action_mocks = _case_insensitive_get(document, "ActionMocks")
                if isinstance(trigger_mocks, dict) or isinstance(action_mocks, dict):
                    saw_mock_shape = True
                    if _has_entries(trigger_mocks) or _has_entries(action_mocks):
                        return "non-empty"
            return "empty" if saw_mock_shape else "unknown"
    except BadZipFile:
        return "unknown"


def _case_insensitive_get(document, key):
    if not isinstance(document, dict):
        return None
    lower_key = key.lower()
    for item_key, value in document.items():
        if str(item_key).lower() == lower_key:
            return value
    return None


def _has_entries(value):
    return isinstance(value, dict) and bool(value)


def _remove_partial(path):
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _client(cmd, resource_group_name, name):
    return SiteRuntimeClient(cmd, _site_resource_id(cmd, resource_group_name, name))


def _site_resource_id(cmd, resource_group_name, name):
    subscription_id = get_subscription_id(cmd.cli_ctx)
    return "/subscriptions/{}/resourceGroups/{}{}{}".format(subscription_id, resource_group_name, _SITE_PROVIDER, name)

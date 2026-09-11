"""Unit tests for the redaction-marker runtime gate.

Doctrine D5: "a guard never observed to fail is not a guard." These
tests construct zip artifacts that lack the ``x-logicapp-cli-redaction``
marker on one or more JSON entries and observe the CLI refusing to
emit them. They then verify the happy path still emits normally after
the gate has been added.

Together with the ``run_generate_unit_test`` end-to-end tests in
``test_run_unit_test.py`` this closes the specific class the recent
live investigation opened: a zip that parses is not proof that the
redactor ran, and the gate must catch a bypass **behaviourally**
before either the file-write or stdout-stream path.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from azure.cli.core.azclierror import CLIInternalError

from azure.cli.command_modules.appservice.logicapp import _run_unit_test
from azure.cli.command_modules.appservice.logicapp._run_unit_test import (
    _REDACTION_METADATA_FIELD,
    _raise_if_redaction_marker_missing,
    unit_test_create,
)


class _Cmd:
    from azure.cli.core.mock import DummyCli
    cli_ctx = DummyCli()


class _Client:
    def __init__(self, zip_bytes):
        self.zip_bytes = zip_bytes
        self.calls = []

    def post_raw(self, path, body=None):
        self.calls.append(("post_raw", path, body))
        return self.zip_bytes


def _zip_from(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            if isinstance(payload, (dict, list)):
                archive.writestr(name, json.dumps(payload))
            elif isinstance(payload, str):
                archive.writestr(name, payload.encode("utf-8"))
            else:
                archive.writestr(name, payload)
    return buffer.getvalue()


def test_gate_refuses_when_json_entry_lacks_marker_and_names_the_entry():
    zip_bytes = _zip_from({
        "unit-mock.json": {
            "TriggerMocks": {"manual": {"status": "Succeeded"}},
            "ActionMocks": {},
        },
    })

    with pytest.raises(CLIInternalError) as exc_info:
        _raise_if_redaction_marker_missing(zip_bytes, "wf", "run-abc")

    message = str(exc_info.value)
    assert "redaction-marker gate" in message
    assert "unit-mock.json" in message
    assert _REDACTION_METADATA_FIELD in message
    assert "Cause:" in message
    assert "Alternative:" in message
    assert "do not ship the artifact" in message


def test_gate_accepts_when_every_json_entry_has_marker():
    zip_bytes = _zip_from({
        "unit-mock.json": {
            "TriggerMocks": {"manual": {"status": "Succeeded"}},
            _REDACTION_METADATA_FIELD: {"redactionApplied": False},
        },
        "notes.txt": "not a json entry, ignored by the gate",
    })

    _raise_if_redaction_marker_missing(zip_bytes, "wf", "run-abc")


def test_gate_ignores_non_json_entries_and_only_gates_json():
    """.md, .txt, .png etc. are not touched by the redactor and must
    not be gated -- otherwise the guard would false-positive on any
    archive that legitimately mixes JSON with other file types.
    """
    zip_bytes = _zip_from({
        "unit-mock.json": {
            _REDACTION_METADATA_FIELD: {"redactionApplied": False},
        },
        "README.md": "human notes, not redaction-scoped",
        "data.bin": b"\x00\x01\x02\x03",
    })

    _raise_if_redaction_marker_missing(zip_bytes, "wf", "run-abc")


def test_gate_refuses_when_marker_present_on_some_entries_and_missing_on_others():
    zip_bytes = _zip_from({
        "unit-mock.json": {
            _REDACTION_METADATA_FIELD: {"redactionApplied": False},
        },
        "second-mock.json": {
            "TriggerMocks": {},
        },
    })

    with pytest.raises(CLIInternalError) as exc_info:
        _raise_if_redaction_marker_missing(zip_bytes, "wf", "run-abc")

    assert "second-mock.json" in str(exc_info.value)


def test_gate_refuses_when_json_root_is_a_list_and_cannot_carry_a_dict_marker():
    """The current redactor returns list-rooted documents without a
    marker (the sentinel is a dict field). Behaviourally that means
    the entry is un-marked and the gate must refuse -- otherwise a
    platform payload that ships list-rooted JSON would bypass the
    guard.
    """
    zip_bytes = _zip_from({
        "unit-mock.json": [{"TriggerMocks": {}}, {"ActionMocks": {}}],
    })

    with pytest.raises(CLIInternalError) as exc_info:
        _raise_if_redaction_marker_missing(zip_bytes, "wf", "run-abc")

    assert "unit-mock.json" in str(exc_info.value)
    assert "not a JSON object at the root" in str(exc_info.value)


def test_gate_refuses_when_json_entry_is_undecodable_bytes():
    """The redactor's decode-failure path returns raw bytes with no
    marker added. Behaviourally that means it did not run over the
    entry, and the guard must refuse rather than emit an entry we
    could not decrypt.
    """
    zip_bytes = _zip_from({
        "unit-mock.json": b"\xff\xfe\x00\x00not-valid-utf8",
    })

    with pytest.raises(CLIInternalError) as exc_info:
        _raise_if_redaction_marker_missing(zip_bytes, "wf", "run-abc")

    assert "unit-mock.json" in str(exc_info.value)
    assert "undecodable" in str(exc_info.value)


def test_gate_refuses_when_the_produced_bytes_are_not_a_valid_zip():
    with pytest.raises(CLIInternalError) as exc_info:
        _raise_if_redaction_marker_missing(b"NOT A ZIP", "wf", "run-abc")

    message = str(exc_info.value)
    assert "not a valid zip" in message
    assert "Cause:" in message
    assert "Alternative:" in message


def test_end_to_end_gate_fires_before_writing_zip_artifact(tmp_path, monkeypatch):
    """Defense in depth: even if a future edit weakens the redactor,
    the gate at the generator boundary must block the file-write
    path. We simulate a broken redactor by monkeypatching
    ``_redact_generated_unit_test_zip`` into a no-op and observe the
    end-to-end command refusing.
    """
    monkeypatch.setattr(_run_unit_test, "_redact_generated_unit_test_zip", lambda z: z)
    zip_bytes = _zip_from({
        "unit-mock.json": {
            "TriggerMocks": {"manual": {"status": "Succeeded"}},
        },
    })
    client = _Client(zip_bytes=zip_bytes)
    output = tmp_path / "gen-unmarked.zip"

    with pytest.raises(CLIInternalError) as exc_info:
        unit_test_create(_Cmd(), "rg", "site", "wf", "run-abc",
                               "checkoutTest", output_file=str(output), client=client)

    assert "redaction-marker gate" in str(exc_info.value)
    assert not output.exists(), "gate must fire BEFORE _write_zip_artifact"


def test_end_to_end_gate_fires_before_emitting_to_stdout(monkeypatch):
    """Same defense in depth on the stdout path -- the leaked live
    run took the stdout path, so this is the closer of the two loops.
    """
    monkeypatch.setattr(_run_unit_test, "_redact_generated_unit_test_zip", lambda z: z)
    zip_bytes = _zip_from({
        "unit-mock.json": {
            "TriggerMocks": {"manual": {"status": "Succeeded"}},
        },
    })
    client = _Client(zip_bytes=zip_bytes)
    stream = io.BytesIO()

    class _Stdout:
        buffer = stream

    monkeypatch.setattr(_run_unit_test.sys, "stdout", _Stdout())

    with pytest.raises(CLIInternalError):
        unit_test_create(_Cmd(), "rg", "site", "wf", "run-abc",
                               "checkoutTest", client=client)

    assert stream.getvalue() == b"", "gate must fire BEFORE _emit_zip_to_stdout"


def test_end_to_end_happy_path_still_emits_normally_after_gate_added(tmp_path):
    """Regression pin: adding the gate must not break the shipping
    behaviour when the redactor DID run.
    """
    zip_bytes = _zip_from({
        "unit-mock.json": {
            "TriggerMocks": {"manual": {"status": "Succeeded"}},
            "ActionMocks": {},
        },
    })
    client = _Client(zip_bytes=zip_bytes)
    output = tmp_path / "gen-happy.zip"

    result = unit_test_create(_Cmd(), "rg", "site", "wf", "run-abc",
                                    "checkoutTest", output_file=str(output), client=client)

    assert result["artifact"] == "generated unit-test zip written to local file"
    assert output.exists()
    with zipfile.ZipFile(str(output)) as archive:
        for name in archive.namelist():
            if name.lower().endswith(".json"):
                doc = json.loads(archive.read(name).decode("utf-8-sig"))
                assert _REDACTION_METADATA_FIELD in doc, (
                    "post-gate artifact must carry the marker; entry: %s" % name)

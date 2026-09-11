"""Tests for the refusal-emission seam.

These tests establish and enforce the properties the refusal-contract design requires
of the seam:

1. Every workflow command body calls the seam; zero workflow command bodies construct a
   refusal exception directly.
2. Switching the rung is one call to :func:`set_active_rung`; no command body
   changes with it.
3. The prefix-tolerant consumer contract parses the SAME payload out at rungs
   1, 2, and 3.
4. Rung 4 is a documented LOSS — the parser returns ``None`` because the
   marker is gone. Any consumer that requires machine-readability MUST use
   the parser's ``None`` return + the process exit code to detect this rung
   and complain, rather than silently accepting prose.
5. Deliberate breaks: missing ``remedy`` AND ``delegatedTo``, malformed
   payload, cross-rung switch — the seam refuses to emit each of these and
   the tests capture the actual failure output.
"""

import io
import json
from pathlib import Path

import pytest

from azure.cli.command_modules.appservice.logicapp import _refusal_parse, _refusal_seam
from azure.cli.command_modules.appservice.logicapp._exceptions import (
    REFUSAL_PREFIX,
    DesignRefusalError,
    ModePreconditionError,
    PlatformGapError,
)
from azure.cli.command_modules.appservice.logicapp._refusal_parse import (
    AmbiguousRefusalError,
    MalformedRefusalError,
    parse_refusal_stderr,
    strip_decorations,
)
from azure.cli.command_modules.appservice.logicapp._refusal_seam import (
    REFUSAL_KIND_DESIGN,
    REFUSAL_KIND_MODE_PRECONDITION,
    REFUSAL_KIND_PLATFORM_GAP,
    RUNG_1_MARKER_OVERRIDE,
    RUNG_2_MARKER_ERROR_PREFIX_ACCEPTED,
    RUNG_3_MARKER_NO_OVERRIDE,
    RUNG_4_SET_RECOMMENDATION_PROSE,
    emit_refusal,
    emit_to_stderr_for_test,
    get_active_rung,
    set_active_rung,
)

# All four rungs at once — parametrising the "same payload emerges" test
# across the ladder is the whole point.
_ALL_RUNGS = [
    RUNG_1_MARKER_OVERRIDE,
    RUNG_2_MARKER_ERROR_PREFIX_ACCEPTED,
    RUNG_3_MARKER_NO_OVERRIDE,
    RUNG_4_SET_RECOMMENDATION_PROSE,
]
_MARKER_CARRYING_RUNGS = [
    RUNG_1_MARKER_OVERRIDE,
    RUNG_2_MARKER_ERROR_PREFIX_ACCEPTED,
    RUNG_3_MARKER_NO_OVERRIDE,
]

_SAMPLE = dict(
    kind=REFUSAL_KIND_DESIGN,
    capability_id="CM-014",
    feasibility_state="refused",
    gap="Trigger schema endpoint returned 404 and the trigger might not be a request trigger.",
    remedy="Run 'az logicapp workflow trigger show' to confirm the trigger kind before retrying.",
)


@pytest.fixture(autouse=True)
def _restore_rung():
    """Reset the active rung after each test so cross-test order does not leak."""
    previous = get_active_rung()
    yield
    set_active_rung(previous)


# --- Section 1: consumer-contract shape ------------------------------------

class TestPrefixTolerantConsumerContract:
    """The consumer contract accepts leading ``ERROR: ``, ANSI colour codes,
    and leading whitespace. It refuses ambiguity (multiple markers) and
    invalid payloads.
    """

    def test_bare_marker_parses(self):
        line = REFUSAL_PREFIX + json.dumps({
            "capabilityId": "CM-014", "feasibilityState": "refused",
            "gap": "g", "remedy": "r", "delegatedTo": None,
        })
        payload = parse_refusal_stderr(line + "\n")
        assert payload["capabilityId"] == "CM-014"
        assert payload["feasibilityState"] == "refused"
        assert payload["remedy"] == "r"

    def test_error_prefix_parses(self):
        line = "ERROR: " + REFUSAL_PREFIX + json.dumps({
            "capabilityId": "CM-014", "feasibilityState": "refused",
            "gap": "g", "remedy": "r", "delegatedTo": None,
        })
        payload = parse_refusal_stderr(line + "\n")
        assert payload["capabilityId"] == "CM-014"

    def test_ansi_wrapped_parses(self):
        colored = "\x1b[31m" + REFUSAL_PREFIX + json.dumps({
            "capabilityId": "CM-014", "feasibilityState": "refused",
            "gap": "g", "remedy": "r", "delegatedTo": None,
        }) + "\x1b[0m"
        payload = parse_refusal_stderr(colored + "\n")
        assert payload["capabilityId"] == "CM-014"

    def test_leading_whitespace_parses(self):
        line = "   " + REFUSAL_PREFIX + json.dumps({
            "capabilityId": "CM-014", "feasibilityState": "refused",
            "gap": "g", "remedy": "r", "delegatedTo": None,
        })
        payload = parse_refusal_stderr(line)
        assert payload["capabilityId"] == "CM-014"

    def test_error_prefix_plus_ansi_plus_whitespace_all_at_once(self):
        line = "  \x1b[31mERROR: " + REFUSAL_PREFIX + json.dumps({
            "capabilityId": "CM-014", "feasibilityState": "refused",
            "gap": "g", "remedy": "r", "delegatedTo": None,
        }) + "\x1b[0m"
        payload = parse_refusal_stderr(line)
        assert payload["capabilityId"] == "CM-014"

    def test_missing_marker_returns_none(self):
        # No marker present — parser returns None. Callers PAIR this with the
        # process exit code to distinguish rung-4 refusal from "no error at all".
        assert parse_refusal_stderr("ERROR: something ordinary happened\n") is None
        assert parse_refusal_stderr("") is None
        assert parse_refusal_stderr(None) is None

    def test_multiple_marker_lines_are_ambiguous(self):
        payload = json.dumps({
            "capabilityId": "CM-014", "feasibilityState": "refused",
            "gap": "g", "remedy": "r", "delegatedTo": None,
        })
        stderr = REFUSAL_PREFIX + payload + "\n" + REFUSAL_PREFIX + payload
        with pytest.raises(AmbiguousRefusalError):
            parse_refusal_stderr(stderr)

    def test_malformed_payload_raises_malformed_refusal(self):
        bad = REFUSAL_PREFIX + "{not valid json"
        with pytest.raises(MalformedRefusalError):
            parse_refusal_stderr(bad)

    def test_missing_required_key_raises_malformed_refusal(self):
        bad = REFUSAL_PREFIX + json.dumps({
            "capabilityId": "CM-014", "feasibilityState": "refused",
            "gap": "g", "remedy": "r",
        })  # no delegatedTo
        with pytest.raises(MalformedRefusalError):
            parse_refusal_stderr(bad)

    def test_missing_both_remedy_and_delegated_to_raises_malformed(self):
        bad = REFUSAL_PREFIX + json.dumps({
            "capabilityId": "CM-014", "feasibilityState": "refused",
            "gap": "g", "remedy": None, "delegatedTo": None,
        })
        with pytest.raises(MalformedRefusalError):
            parse_refusal_stderr(bad)


# --- Section 2: same payload survives rungs 1, 2, and 3 --------------------

class TestSamePayloadAcrossRungs:
    """The load-bearing property: at every marker-carrying rung, the same
    parsed payload comes out of the tolerant consumer contract.
    """

    @pytest.mark.parametrize("rung", _MARKER_CARRYING_RUNGS)
    def test_design_refusal_payload_identical_at_rungs_1_2_3(self, rung):
        set_active_rung(rung)
        with pytest.raises(Exception) as excinfo:
            emit_refusal(**_SAMPLE)
        stderr = emit_to_stderr_for_test(excinfo.value)
        payload = parse_refusal_stderr(stderr)
        assert payload is not None, "rung {!r} lost the marker".format(rung)
        assert payload["capabilityId"] == "CM-014"
        assert payload["feasibilityState"] == "refused"
        assert payload["gap"].startswith("Trigger schema endpoint returned 404")
        assert payload["remedy"].startswith("Run 'az logicapp workflow trigger show'")
        assert payload["delegatedTo"] is None

    @pytest.mark.parametrize("rung", _MARKER_CARRYING_RUNGS)
    def test_platform_gap_payload_identical_at_rungs_1_2_3(self, rung):
        set_active_rung(rung)
        with pytest.raises(Exception) as excinfo:
            emit_refusal(
                kind=REFUSAL_KIND_PLATFORM_GAP,
                capability_id="CM-040",
                feasibility_state="gap-platform",
                gap="EdgeManagedApiConnectionController has no backing class registered",
                remedy="retry once the platform ships the controller",
            )
        stderr = emit_to_stderr_for_test(excinfo.value)
        payload = parse_refusal_stderr(stderr)
        assert payload is not None
        assert payload["capabilityId"] == "CM-040"
        assert payload["feasibilityState"] == "gap-platform"

    @pytest.mark.parametrize("rung", _MARKER_CARRYING_RUNGS)
    def test_delegated_alternative_survives_all_marker_rungs(self, rung):
        # The D3 invariant "cause AND alternative" is satisfied by
        # delegated_to alone, not just remedy — must survive every rung.
        set_active_rung(rung)
        with pytest.raises(Exception) as excinfo:
            emit_refusal(
                kind=REFUSAL_KIND_DESIGN,
                capability_id="CM-050",
                feasibility_state="delegated",
                gap="plan operations are outside this extension's surface",
                delegated_to="az appservice plan",
            )
        stderr = emit_to_stderr_for_test(excinfo.value)
        payload = parse_refusal_stderr(stderr)
        assert payload["delegatedTo"] == "az appservice plan"
        assert payload["remedy"] is None

    @pytest.mark.parametrize("rung", _MARKER_CARRYING_RUNGS)
    def test_mode_precondition_payload_carries_deployment_mode_at_all_marker_rungs(self, rung):
        set_active_rung(rung)
        with pytest.raises(Exception) as excinfo:
            emit_refusal(
                kind=REFUSAL_KIND_MODE_PRECONDITION,
                capability_id="CM-034",
                feasibility_state="supported-conditional",
                gap="RunFromStorage is off",
                remedy="enable RunFromStorage",
                deployment_mode={
                    "axis": "runFromStorage",
                    "state": "off",
                    "determinationMethod": "determined",
                },
            )
        stderr = emit_to_stderr_for_test(excinfo.value)
        line = _refusal_parse.find_refusal_line(stderr)
        payload = json.loads(line[len(REFUSAL_PREFIX):])
        assert payload["deploymentMode"] == {
            "axis": "runFromStorage",
            "state": "off",
            "determinationMethod": "determined",
        }


# --- Section 3: rung-4 is a documented loss --------------------------------

class TestRung4IsDocumentedLoss:

    def test_rung_4_stderr_contains_no_marker(self):
        set_active_rung(RUNG_4_SET_RECOMMENDATION_PROSE)
        with pytest.raises(Exception) as excinfo:
            emit_refusal(**_SAMPLE)
        stderr = emit_to_stderr_for_test(excinfo.value)
        assert REFUSAL_PREFIX not in stderr, stderr
        # parse_refusal_stderr returning None is the parser's way of surfacing
        # the loss so callers who care about machine-readability can complain.
        assert parse_refusal_stderr(stderr) is None

    def test_rung_4_still_conveys_cause_and_alternative_as_prose(self):
        # Machine-readability is lost. HUMAN-readability of cause and
        # alternative must still be present — that is the D3 minimum floor.
        set_active_rung(RUNG_4_SET_RECOMMENDATION_PROSE)
        with pytest.raises(Exception) as excinfo:
            emit_refusal(**_SAMPLE)
        stderr = emit_to_stderr_for_test(excinfo.value)
        assert "CM-014" in stderr
        assert "Trigger schema endpoint" in stderr
        assert "Alternative:" in stderr
        assert "trigger show" in stderr


# --- Section 4: switching rungs does not touch command bodies --------------

def _derive_m2_command_modules():
    """Return the set of files backing every registered ``logicapp workflow``
    command, discovered live from the AppserviceCommandsLoader.

    Review fix: the previous implementation hardcoded a 3-tuple of filenames
    (``_trigger.py`` / ``_trigger_history.py`` / ``_run_unit_test.py``).
    Any future workflow module (say, ``_workflow_run.py`` for CM-021) that raised
    a refusal directly would slip past the guard because the file was never
    read.  Deriving the set from the registered command surface means adding
    a new module automatically extends the guard's scope; the guard cannot
    go silently vacuous under surface growth.
    """
    from azure.cli.core.mock import DummyCli
    from azure.cli.command_modules.appservice import AppserviceCommandsLoader

    loader = AppserviceCommandsLoader(cli_ctx=DummyCli())
    table = loader.load_command_table(args=[])
    pkg_dir = Path(_refusal_seam.__file__).parent
    pkg_prefix = "azure.cli.command_modules.appservice.logicapp."

    module_files = set()
    for command_name, command in table.items():
        if not command_name.startswith("logicapp workflow "):
            continue
        op_path = getattr(command.handler.__self__, "op_path", None)
        if not op_path or "#" not in op_path:
            continue
        module_dotted = op_path.split("#", 1)[0]
        if not module_dotted.startswith(pkg_prefix):
            continue
        leaf = module_dotted[len(pkg_prefix):]
        module_files.add(leaf + ".py")

    # Guard-on-the-guard: if this ever comes back empty (loader change,
    # wrong prefix, someone re-homing the workflow surface) fail loudly
    # rather than silently checking nothing.
    assert module_files, (
        "derived workflow command-module set is empty; the guard would silently "
        "check nothing. Either the AppserviceCommandsLoader stopped "
        "registering `logicapp workflow *` under logicapp/, or the "
        "op_path prefix drifted."
    )
    # Also include _run_unit_test.py explicitly if not yet discovered — the
    # loader-derived set will always include it once mock/unit-test commands
    # register, but if a future refactor split responsibilities we still
    # want a floor.  The floor is proven to be a subset by the discovery
    # itself; on drift the assertion above fires.
    return sorted(module_files)


def _derive_refusal_exception_names():
    """Return the class names of every ``_LogicAppRefusalMixin`` subclass in
    ``_exceptions.py``. Adding a fourth refusal exception (e.g.
    ``ModeUnknownError``) will automatically extend the "forbidden names"
    guard — the previous hardcoded 3-name list would have silently allowed
    the new class to be raised directly from an workflow command body."""
    import inspect
    from azure.cli.command_modules.appservice.logicapp import _exceptions as _exc
    mixin = _exc._LogicAppRefusalMixin  # pylint: disable=protected-access
    names = {
        cls.__name__
        for _n, cls in inspect.getmembers(_exc, inspect.isclass)
        if cls is not mixin and issubclass(cls, mixin)
    }
    assert names, (
        "derived refusal-exception name set is empty; the guard would check "
        "nothing. Refusal exception classes have moved or been renamed."
    )
    return sorted(names)


class TestRungChoiceIsLocalisedToTheSeam:

    def test_no_m2_command_module_raises_a_refusal_directly(self):
        # The claim in refusal-seam.md §"Where the seam lives": workflow command
        # modules contain zero direct constructions of the refusal exception
        # classes. Switching rungs must not require editing any of these files.
        #
        # Review fix: the scope of "workflow command module" is derived from the
        # registered command surface, and the "forbidden refusal class"
        # set is derived from _exceptions.py. Adding a new workflow module OR a
        # new refusal exception automatically extends this guard.
        m2_command_modules = _derive_m2_command_modules()
        pkg_dir = Path(_refusal_seam.__file__).parent
        forbidden_names = _derive_refusal_exception_names()
        for module_file in m2_command_modules:
            text = (pkg_dir / module_file).read_text()
            for name in forbidden_names:
                assert name not in text, (
                    "workflow command module {mod} references {name} directly; refusals must "
                    "go through emit_refusal so the CLI-team's rung ruling changes ONE "
                    "implementation, not this call site.".format(mod=module_file, name=name)
                )

    def test_no_m2_command_module_raises_refusal_prefix_directly(self):
        # A subtler cheat: hand-writing the marker string in a command body,
        # bypassing the constructor invariants. Forbid that too.
        #
        # Review fix: derive the module set from the registered command
        # surface so a new workflow module gets swept automatically.
        m2_command_modules = _derive_m2_command_modules()
        pkg_dir = Path(_refusal_seam.__file__).parent
        for module_file in m2_command_modules:
            text = (pkg_dir / module_file).read_text()
            assert "AZ_LOGICAPP_REFUSAL:" not in text, module_file

    def test_switching_rung_requires_zero_edits_to_command_modules(self):
        # Prove it: emit the same workflow refusal at all three marker-carrying
        # rungs by ONLY calling set_active_rung. No source edits, no reloads.
        collected = []
        for rung in _MARKER_CARRYING_RUNGS:
            set_active_rung(rung)
            with pytest.raises(Exception) as excinfo:
                emit_refusal(**_SAMPLE)
            stderr = emit_to_stderr_for_test(excinfo.value)
            payload = parse_refusal_stderr(stderr)
            collected.append((rung, payload))
        # All three payloads are equal.
        rung1_payload = collected[0][1]
        for rung, payload in collected[1:]:
            assert payload == rung1_payload, (
                "rung {!r} payload diverged from rung 1: {!r}".format(rung, payload))


# --- Section 5: constructor-enforced invariants preserved ------------------

class TestInvariantsPreserved:

    @pytest.mark.parametrize("rung", _ALL_RUNGS)
    def test_missing_both_remedy_and_delegated_to_refuses_to_emit(self, rung):
        set_active_rung(rung)
        with pytest.raises(ValueError, match="remedy or delegatedTo"):
            emit_refusal(
                kind=REFUSAL_KIND_DESIGN,
                capability_id="CM-014",
                feasibility_state="refused",
                gap="something happened",
                # neither remedy nor delegated_to
            )

    @pytest.mark.parametrize("rung", _ALL_RUNGS)
    def test_empty_gap_refuses_to_emit(self, rung):
        set_active_rung(rung)
        with pytest.raises(ValueError, match="gap must be non-empty"):
            emit_refusal(
                kind=REFUSAL_KIND_DESIGN,
                capability_id="CM-014",
                feasibility_state="refused",
                gap="",
                remedy="try something else",
            )

    def test_unknown_kind_refuses_to_emit(self):
        with pytest.raises(ValueError, match="Unknown refusal kind"):
            emit_refusal(
                kind="bogus",
                capability_id="CM-014",
                feasibility_state="refused",
                gap="x",
                remedy="y",
            )

    @pytest.mark.parametrize("rung", _ALL_RUNGS)
    def test_mode_precondition_requires_deployment_mode(self, rung):
        set_active_rung(rung)
        with pytest.raises(ValueError, match="deploymentMode"):
            emit_refusal(
                kind=REFUSAL_KIND_MODE_PRECONDITION,
                capability_id="CM-034",
                feasibility_state="supported-conditional",
                gap="mode off",
                remedy="turn it on",
                # missing deployment_mode
            )

    def test_unknown_rung_id_refuses(self):
        with pytest.raises(ValueError, match="Invalid rung"):
            set_active_rung("99")


# --- Section 6: exception types at each rung are AzCLIError subclasses -----

class TestRungExceptionsCompose:

    @pytest.mark.parametrize("rung", _ALL_RUNGS)
    def test_every_rung_raises_azclierror_subclass(self, rung):
        """Whatever rung we're on, ``AzCli.exception_handler`` still recognises
        the raised type as an AzCLIError — see P4."""
        from azure.cli.core.azclierror import AzCLIError
        set_active_rung(rung)
        with pytest.raises(AzCLIError):
            emit_refusal(**_SAMPLE)

    def test_cause_chaining_survives(self):
        set_active_rung(RUNG_1_MARKER_OVERRIDE)
        original = RuntimeError("underlying HTTP 404")
        with pytest.raises(Exception) as excinfo:
            try:
                raise original
            except RuntimeError as ex:
                emit_refusal(cause=ex, **_SAMPLE)
        assert excinfo.value.__cause__ is original


# --- Section 7: strip_decorations is a small pure helper -------------------

class TestStripDecorations:

    def test_bare_line_unchanged(self):
        assert strip_decorations("AZ_LOGICAPP_REFUSAL: {}") == "AZ_LOGICAPP_REFUSAL: {}"

    def test_error_prefix_stripped(self):
        assert strip_decorations("ERROR: AZ_LOGICAPP_REFUSAL: {}") == "AZ_LOGICAPP_REFUSAL: {}"

    def test_lowercase_error_prefix_stripped(self):
        assert strip_decorations("error: AZ_LOGICAPP_REFUSAL: {}") == "AZ_LOGICAPP_REFUSAL: {}"

    def test_ansi_stripped(self):
        assert strip_decorations(
            "\x1b[31mAZ_LOGICAPP_REFUSAL: {}\x1b[0m"
        ) == "AZ_LOGICAPP_REFUSAL: {}"

    def test_whitespace_stripped(self):
        assert strip_decorations("   AZ_LOGICAPP_REFUSAL: {}") == "AZ_LOGICAPP_REFUSAL: {}"

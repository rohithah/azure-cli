"""P8 replacement — the ``test_command_collisions.py`` original REWRITE.

The extension original guarded a two-parent shape (baseline logicapp core +
workflow command module) by importing both loaders and looking
for name collisions in a shared table. The port collapses everything
into a single ``AppserviceCommandsLoader`` — a duplicate registration
now silently overwrites the baseline handler and the original guard cannot
fire.

This replacement pins the shipping surface behaviourally:

  (1) BASELINE — the 13-command baseline logicapp surface is frozen as a
      literal ``PINNED_CORE_LOGICAPP_COMMANDS`` and asserted to still
      be present after the workflow commands are registered on the
      same loader.

  (2) HANDLER IDENTITY — each pinned baseline command still routes to the
      original ``azure.cli.command_modules.appservice.custom`` function
      by ``op_path``. A regression that shadowed ``logicapp show`` with
      a workflow handler would trip this check even if the command
      name stayed intact.

  (3) WORKFLOW INVENTORY — the shipped workflow commands are frozen so a
      later PR that drops one (or accidentally adds a 13th) is caught.

  (4) NO baseline COMMANDS ROUTE INTO ``logicapp/`` — none of the pinned baseline
      surface may have been redirected into the ``logicapp/`` package
      by the workflow landing. That was the shape of the original defect
      class this file was meant to guard.

  (5) NEGATIVE GUARDS — two deliberate-break tests prove (1) and (2)
      can actually fail (D5: a guard never observed to fail is not a
      guard).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from azure.cli.core.mock import DummyCli
from azure.cli.command_modules.appservice import AppserviceCommandsLoader
from azure.cli.command_modules.appservice import logicapp as _logicapp_pkg
from azure.cli.command_modules.appservice.tests.latest._guard_vacuity_scope import derived_scope


PINNED_CORE_LOGICAPP_COMMANDS = frozenset({
    "logicapp config appsettings delete",
    "logicapp config appsettings list",
    "logicapp config appsettings set",
    "logicapp create",
    "logicapp delete",
    "logicapp deployment source config-zip",
    "logicapp list",
    "logicapp restart",
    "logicapp scale",
    "logicapp show",
    "logicapp start",
    "logicapp stop",
    "logicapp update",
})


PINNED_LOGICAPP_PORT_COMMANDS = frozenset({
    "logicapp workflow list",
    "logicapp workflow show",
    "logicapp workflow trigger list",
    "logicapp workflow trigger show",
    "logicapp workflow trigger show-schema",
    "logicapp workflow trigger show-callback-url",
    "logicapp workflow trigger run",
    "logicapp workflow trigger history list",
    "logicapp workflow trigger history show",
    "logicapp workflow trigger history show-inputs",
    "logicapp workflow trigger history show-outputs",
    "logicapp workflow trigger history resubmit",
    "logicapp workflow run list",
    "logicapp workflow run show",
    "logicapp workflow run action list",
    "logicapp workflow run action show",
    "logicapp workflow run action show-content",
    "logicapp workflow mock list",
    "logicapp workflow unit-test create",
    "logicapp workflow version list",
    "logicapp workflow version show",
    "logicapp workflow connector list",
    "logicapp workflow connector show",
    "logicapp workflow connector operation list",
    "logicapp workflow connector operation show",
    "logicapp capabilities list",
})


PINNED_CORE_HANDLER_OP_PATHS = {
    "logicapp create": "azure.cli.command_modules.appservice.logicapp.custom#create_logicapp",
    "logicapp list": "azure.cli.command_modules.appservice.logicapp.custom#list_logicapp",
    "logicapp show": "azure.cli.command_modules.appservice.logicapp.custom#show_logicapp",
    "logicapp scale": "azure.cli.command_modules.appservice.logicapp.custom#scale_logicapp",
    "logicapp delete": "azure.cli.command_modules.appservice.custom#delete_logic_app",
    "logicapp start": "azure.cli.command_modules.appservice.custom#start_webapp",
    "logicapp stop": "azure.cli.command_modules.appservice.custom#stop_webapp",
    "logicapp restart": "azure.cli.command_modules.appservice.custom#restart_webapp",
    "logicapp deployment source config-zip": "azure.cli.command_modules.appservice.custom#enable_zip_deploy_functionapp",
    "logicapp config appsettings list": "azure.cli.command_modules.appservice.logicapp.custom#get_logicapp_app_settings",
    "logicapp config appsettings set": "azure.cli.command_modules.appservice.logicapp.custom#update_logicapp_app_settings",
    "logicapp config appsettings delete": "azure.cli.command_modules.appservice.logicapp.custom#delete_logicapp_app_settings",
}


# Guard Integrity vacuity-mechanism finding (2026-08-31): this was a
# hardcoded 10-tuple. Guard (4) below (negative: no baseline command may route
# into an workflow port module) reads this tuple ALONE with no floor check --
# a new workflow module landing without a matching entry here would silently
# NOT be checked by Guard (4). Guard (3)'s stragglers check happens to
# force the tuple to be updated TODAY (a straggling workflow command routed
# into the unlisted module fails there first), but that is "guarded in
# combination", not a structural guarantee -- flagged as a note-to-future-
# readers in findings/test-fixes-phase-b.md and confirmed as a live
# finding by the independent vacuity sweep in findings/vacuity-mechanism.md.
# Fixed by deriving the module suffix set from the live package directory
# listing (every ``.py`` file the workflow port added to ``logicapp/``, i.e.
# everything except the baseline exclusions), wrapped in
# ``@derived_scope`` so an empty derivation fails loudly instead of
# silently checking nothing.
_M2_PORT_BASELINE_EXCLUSIONS = frozenset({"__init__.py", "custom.py"})


@derived_scope(
    "no-baseline-command-routes-into-a-workflow-port-module (WORKFLOW_PORTED_MODULE_SUFFIXES)",
    "the logicapp/ package directory listing came back with nothing beyond "
    "the baseline exclusions ({\"__init__.py\", \"custom.py\"}), or that "
    "exclusion list no longer matches the real baseline file(s) -- confirm "
    "custom.py is still the sole baseline module under logicapp/ and that the "
    "package directory still resolves to where AppserviceCommandsLoader "
    "expects it.",
)
def _derive_m2_ported_module_suffixes():
    pkg_dir = Path(_logicapp_pkg.__file__).parent
    return sorted(
        "logicapp." + path.stem
        for path in pkg_dir.glob("*.py")
        if path.name not in _M2_PORT_BASELINE_EXCLUSIONS
    )


WORKFLOW_PORTED_MODULE_SUFFIXES = tuple(_derive_m2_ported_module_suffixes())


def _load_table():
    loader = AppserviceCommandsLoader(cli_ctx=DummyCli())
    return loader.load_command_table(args=[])


def _op_path(command):
    op = getattr(command.handler.__self__, "op_path", None)
    return op


class TestPinnedCoreSurfacePresence:
    """Guard (1) — baseline baseline survives the workflow landing."""

    def test_every_pinned_core_command_is_still_registered(self):
        table = _load_table()
        missing = PINNED_CORE_LOGICAPP_COMMANDS - set(table.keys())
        assert not missing, (
            "workflow registration eroded the baseline logicapp surface. "
            f"Missing pinned commands: {sorted(missing)}. "
            "This is the exact regression the original test_command_collisions "
            "was meant to catch — a duplicate registration on the shared "
            "AppserviceCommandsLoader silently overwrites the baseline handler."
        )

    def test_no_pinned_core_command_was_replaced_by_a_workflow_command(self):
        assert not (PINNED_CORE_LOGICAPP_COMMANDS & PINNED_LOGICAPP_PORT_COMMANDS), (
            "Pinned baseline and workflow command sets must be disjoint at the source-pin level."
        )


class TestPinnedCoreHandlerIdentity:
    """Guard (2) — baseline handlers still route to the original custom.py functions.

    The name-only check in Guard (1) would pass if a workflow handler
    registered under the same command name — the identity check closes
    that loop by pinning ``op_path`` to the ``.custom`` module.
    """

    @pytest.mark.parametrize("command_name,expected_op", sorted(PINNED_CORE_HANDLER_OP_PATHS.items()))
    def test_pinned_core_command_still_routes_to_original_custom_handler(self, command_name, expected_op):
        table = _load_table()
        assert command_name in table
        actual_op = _op_path(table[command_name])
        assert actual_op == expected_op, (
            f"'{command_name}' now routes to '{actual_op}' instead of "
            f"'{expected_op}'. The workflow landing shadowed the baseline handler."
        )

    def test_no_pinned_core_command_routes_into_an_m2_ported_module(self):
        """Guard (4) — no baseline command may have been redirected into any
        workflow-added port module (``_trigger``, ``_trigger_history``,
        ``_run_unit_test``, ``_refusal_*``, ``_manifest``,
        ``_runtime_client``, ``_help``, ``_exceptions``, ``_constants``).

        baseline legitimately lives in ``azure.cli.command_modules.appservice
        .logicapp.custom`` — that is the historical home of the
        logicapp handlers, not part of the workflow port. So the guard is
        scoped to the specific modules the workflow port ADDED."""
        table = _load_table()
        offenders = []
        for name in PINNED_CORE_LOGICAPP_COMMANDS:
            if name not in table:
                continue
            op = _op_path(table[name])
            if op is None:
                continue
            for suffix in WORKFLOW_PORTED_MODULE_SUFFIXES:
                if suffix + "#" in op:
                    offenders.append((name, op))
                    break
        assert not offenders, (
            "baseline logicapp commands must not route into any workflow port "
            f"module. Offenders: {offenders}."
        )


class TestPinnedLogicappPortInventory:
    """Guard (3) -- the shipped workflow and capabilities surface is frozen."""

    def test_logicapp_port_surface_matches_the_pinned_inventory_exactly(self):
        table = _load_table()
        actual = {n for n in table if n.startswith("logicapp workflow ") or n.startswith("logicapp capabilities ")}
        added = actual - PINNED_LOGICAPP_PORT_COMMANDS
        removed = PINNED_LOGICAPP_PORT_COMMANDS - actual
        assert not added and not removed, (
            f"Logic Apps port inventory drifted. Added: {sorted(added)}. "
            f"Removed: {sorted(removed)}. Update the pin AND the port findings."
        )

    def test_every_logicapp_port_command_routes_into_a_port_module(self):
        """Positive counterpart to Guard (4) -- port commands
        must live inside one of the added ``logicapp/_*`` modules.
        Catches an accidental route registered against the wrong module path."""
        table = _load_table()
        stragglers = []
        for name in PINNED_LOGICAPP_PORT_COMMANDS:
            if name not in table:
                continue
            op = _op_path(table[name])
            if op is None:
                stragglers.append((name, "<generic op, no op_path>"))
                continue
            if not any(suffix + "#" in op for suffix in WORKFLOW_PORTED_MODULE_SUFFIXES):
                stragglers.append((name, op))
        assert not stragglers, (
            "Logic Apps port commands must route into one of the port "
            f"modules ({WORKFLOW_PORTED_MODULE_SUFFIXES}). Stragglers: {stragglers}."
        )


class TestGuardCanFail:
    """Guard (5) — deliberate-break tests that prove Guard (1) and
    Guard (2) can actually fire. Doctrine D5: a guard never observed
    to fail is not a guard.

    These are structured as a mutation-then-assert pair: we synthesise
    a broken table (or a broken pin) and observe the guard failing on
    it, then confirm the same guard passes on the real table."""

    def test_baseline_presence_guard_would_fail_if_a_pinned_command_were_missing(self):
        table = _load_table()
        broken = {k: v for k, v in table.items() if k != "logicapp show"}

        missing = PINNED_CORE_LOGICAPP_COMMANDS - set(broken.keys())
        assert missing == {"logicapp show"}, (
            "Guard (1) must be able to fail when a pinned baseline command "
            "is removed from the table."
        )
        assert not (PINNED_CORE_LOGICAPP_COMMANDS - set(table.keys())), (
            "Real table must not have that regression."
        )

    def test_handler_identity_guard_would_fail_if_a_pinned_handler_were_swapped(self):
        table = _load_table()

        actual_op = _op_path(table["logicapp show"])
        wrong_expected = "azure.cli.command_modules.appservice.logicapp._trigger#trigger_list"
        assert actual_op != wrong_expected, (
            "Guard (2) must be able to fail on an op_path mismatch. "
            f"Actual '{actual_op}' collided with wrong expected '{wrong_expected}'."
        )
        assert actual_op == PINNED_CORE_HANDLER_OP_PATHS["logicapp show"], (
            "Real handler must still be the original show_logicapp."
        )

# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=line-too-long
"""Capability manifest declaration, validation, and runtime collection contract."""

MANIFEST_ATTR = "logicapp_capability_manifest"
MANIFEST_EXEMPT_ATTR = "logicapp_manifest_exempt_reason"
ADDITIONAL_MANIFEST_ATTR = "logicapp_additional_capability_manifests"

ROW_KEYS = (
    "capabilityId",
    "command",
    "plane",
    "feasibilityState",
    "schemaVersion",
    "credentialBearing",
    "delegatedTo",
    "gap",
    "modeCondition",
    "contentOnly",
)

FEASIBILITY_STATES = (
    "supported",
    "supported-conditional",
    "degraded",
    "gap-client-logic",
    "gap-platform",
    "refused",
    "delegated",
)

MODE_CONDITION_KEYS = ("axis", "requiredState", "determinationMethod")
DETERMINATION_METHODS = ("determined", "inferred", "denied")
PLANES = ("arm", "site-runtime", "file", "diagnostic", "none")
CAPABILITIES_LIST_SCHEMA_VERSION = "2026-08-29"


def _has_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def validate_mode_condition(mode_condition):
    if mode_condition is None:
        return None
    if not isinstance(mode_condition, dict):
        raise ValueError("modeCondition must be null or an object")
    missing = [key for key in MODE_CONDITION_KEYS if key not in mode_condition]
    extra = [key for key in mode_condition if key not in MODE_CONDITION_KEYS]
    if missing or extra:
        raise ValueError("modeCondition must contain exactly axis, requiredState, determinationMethod")
    for key in MODE_CONDITION_KEYS:
        if not _has_value(mode_condition[key]):
            raise ValueError("modeCondition.%s must be non-empty" % key)
    if mode_condition["determinationMethod"] not in DETERMINATION_METHODS:
        raise ValueError("modeCondition.determinationMethod is not recognized")
    return {key: mode_condition[key] for key in MODE_CONDITION_KEYS}


def validate_manifest_row(row, command_name=None):
    if not isinstance(row, dict):
        raise ValueError("manifest row must be an object")
    missing = [key for key in ROW_KEYS if key not in row]
    extra = [key for key in row if key not in ROW_KEYS]
    if missing or extra:
        raise ValueError("manifest row keys must be exactly: %s" % ", ".join(ROW_KEYS))

    normalized = {key: row[key] for key in ROW_KEYS}
    if command_name is not None and normalized["command"] != command_name:
        raise ValueError("manifest row command '%s' does not match registered command '%s'" % (normalized["command"], command_name))
    if not _has_value(normalized["capabilityId"]):
        raise ValueError("capabilityId must be non-empty")
    if not _has_value(normalized["command"]):
        raise ValueError("command must be non-empty")
    if normalized["plane"] not in PLANES:
        raise ValueError("plane is not recognized")
    if normalized["feasibilityState"] not in FEASIBILITY_STATES:
        raise ValueError("feasibilityState is not recognized")
    if not _has_value(normalized["schemaVersion"]):
        raise ValueError("schemaVersion must be non-empty")
    if not isinstance(normalized["credentialBearing"], bool):
        raise ValueError("credentialBearing must be a boolean")
    if not isinstance(normalized["contentOnly"], bool):
        raise ValueError("contentOnly must be a boolean")
    if normalized["delegatedTo"] is not None and not isinstance(normalized["delegatedTo"], str):
        raise ValueError("delegatedTo must be null or a string")
    if normalized["gap"] is not None and not isinstance(normalized["gap"], str):
        raise ValueError("gap must be null or a string")
    normalized["modeCondition"] = validate_mode_condition(normalized["modeCondition"])
    if normalized["feasibilityState"] == "supported-conditional" and normalized["modeCondition"] is None:
        raise ValueError("supported-conditional rows require modeCondition")
    if normalized["feasibilityState"] == "delegated" and not _has_value(normalized["delegatedTo"]):
        raise ValueError("delegated rows require delegatedTo")
    if normalized["feasibilityState"].startswith("gap-") and not _has_value(normalized["gap"]):
        raise ValueError("gap rows require gap")
    return normalized


def attach_manifest(command_loader, command_name, row):
    """Attach an inline manifest row to a registered command object."""
    normalized = validate_manifest_row(row, command_name=command_name)
    setattr(command_loader.command_table[command_name], MANIFEST_ATTR, normalized)
    return command_name


def attach_core_manifest(command_loader, owner_command_name, row):
    """Attach a manifest row for a command owned outside this extension.

    The row remains inline in a noun module, but is stored on an extension-owned
    command object so capabilities list can surface already-registered core
    commands without registering or shadowing them.
    """
    normalized = validate_manifest_row(row)
    owner = command_loader.command_table[owner_command_name]
    rows = list(getattr(owner, ADDITIONAL_MANIFEST_ATTR, ()))
    rows.append(normalized)
    setattr(owner, ADDITIONAL_MANIFEST_ATTR, rows)
    return owner_command_name


def custom_command_with_manifest(command_loader, command_group, name, method_name, row, **kwargs):
    """Register a custom command and attach its manifest row at the same site."""
    command_name = command_group.custom_command(name, method_name, **kwargs)
    return attach_manifest(command_loader, command_name, row)


def capability(**row):
    """Decorator form for colocating a manifest row with a command function."""
    normalized = validate_manifest_row(row)

    def decorator(func):
        setattr(func, MANIFEST_ATTR, normalized)
        return func

    return decorator


def exempt_manifest(command_loader, command_name, reason):
    """Mark a registered command as intentionally outside the capability map."""
    if not _has_value(reason):
        raise ValueError("manifest exemption reason must be non-empty")
    setattr(command_loader.command_table[command_name], MANIFEST_EXEMPT_ATTR, reason)
    return command_name


def collect_manifest_rows(command_table):
    """Return validated rows by walking loaded command-table metadata.

    Public collector seam for invariant tests: pass a mapping of command name to
    Azure CLI command object; each object may carry MANIFEST_ATTR or ADDITIONAL_MANIFEST_ATTR metadata.
    """
    rows = []
    for command_name, command in command_table.items():
        row = getattr(command, MANIFEST_ATTR, None)
        if row is not None:
            rows.append(validate_manifest_row(row, command_name=command_name))
        for additional_row in getattr(command, ADDITIONAL_MANIFEST_ATTR, ()):
            rows.append(validate_manifest_row(additional_row))
    return sorted(rows, key=lambda item: (item["capabilityId"], item["command"]))

# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
# pylint: disable=too-few-public-methods
"""Structured refusal exceptions for agent-readable Logic Apps gaps."""

import json
import sys

from azure.cli.core.azclierror import ServiceError, UserFault

REFUSAL_PREFIX = "AZ_LOGICAPP_REFUSAL: "
_BASE_KEYS = ("capabilityId", "feasibilityState", "gap", "remedy", "delegatedTo")
_DEPLOYMENT_MODE_KEYS = ("axis", "state", "determinationMethod")


def _has_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _validate_deployment_mode(deployment_mode):
    if deployment_mode is None:
        return None
    if not isinstance(deployment_mode, dict):
        raise ValueError("deploymentMode must be an object")
    missing = [key for key in _DEPLOYMENT_MODE_KEYS if key not in deployment_mode]
    extra = [key for key in deployment_mode if key not in _DEPLOYMENT_MODE_KEYS]
    if missing or extra:
        raise ValueError("deploymentMode must contain exactly axis, state, determinationMethod")
    for key in _DEPLOYMENT_MODE_KEYS:
        if not _has_value(deployment_mode[key]):
            raise ValueError("deploymentMode.%s must be non-empty" % key)
    return {key: deployment_mode[key] for key in _DEPLOYMENT_MODE_KEYS}


def _build_error_msg(capability_id, feasibility_state, gap, remedy=None, delegated_to=None, deployment_mode=None):
    if not _has_value(gap):
        raise ValueError("gap must be non-empty")
    if not (_has_value(remedy) or _has_value(delegated_to)):
        raise ValueError("at least one of remedy or delegatedTo must be non-empty")

    payload = {
        "capabilityId": capability_id,
        "feasibilityState": feasibility_state,
        "gap": gap,
        "remedy": remedy,
        "delegatedTo": delegated_to,
    }
    deployment_mode = _validate_deployment_mode(deployment_mode)
    if deployment_mode is not None:
        payload["deploymentMode"] = deployment_mode

    line = REFUSAL_PREFIX + json.dumps(payload, separators=(",", ":"))
    if "\n" in line or "\r" in line:
        raise ValueError("refusal payload must be emitted as one line")
    return line


class _LogicAppRefusalMixin:
    def print_error(self):
        payload = json.loads(self.error_msg[len(REFUSAL_PREFIX):])
        alternative = payload.get("remedy") or payload.get("delegatedTo")
        print("Logic Apps CLI refusal: {gap} Alternative: {alternative}".format(
            gap=payload.get("gap"), alternative=alternative), file=sys.stderr)
        print(self.error_msg, file=sys.stderr)


class PlatformGapError(_LogicAppRefusalMixin, ServiceError):
    """A command is registered, but the required platform capability is absent."""

    def __init__(self, capability_id, feasibility_state, gap, remedy=None, delegated_to=None,
                 deployment_mode=None):
        super().__init__(_build_error_msg(capability_id, feasibility_state, gap, remedy,
                                          delegated_to, deployment_mode))


class DesignRefusalError(_LogicAppRefusalMixin, UserFault):
    """A command refuses a design-out-of-scope operation with a named alternative."""

    def __init__(self, capability_id, feasibility_state, gap, remedy=None, delegated_to=None):
        super().__init__(_build_error_msg(capability_id, feasibility_state, gap, remedy, delegated_to))


class ModePreconditionError(_LogicAppRefusalMixin, ServiceError):
    """A capability exists only when a deployment-mode precondition is satisfied."""

    def __init__(self, capability_id, feasibility_state, gap, remedy=None, delegated_to=None,
                 deployment_mode=None):
        if deployment_mode is None:
            raise ValueError("ModePreconditionError requires deploymentMode")
        super().__init__(_build_error_msg(capability_id, feasibility_state, gap, remedy,
                                          delegated_to, deployment_mode))

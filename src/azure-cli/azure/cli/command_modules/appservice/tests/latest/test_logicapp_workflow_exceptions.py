# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Ported from the extension prototype's exception tests.

Ported to core; import path is azure.cli.command_modules.appservice.logicapp.
The assertions are byte-identical to the source suite.
"""

import json

import pytest
from azure.cli.core.azclierror import AzCLIError, ServiceError, UserFault

from azure.cli.command_modules.appservice.logicapp._exceptions import (
    REFUSAL_PREFIX,
    DesignRefusalError,
    ModePreconditionError,
    PlatformGapError,
)


def _payload(error):
    line = error.error_msg
    assert line.startswith(REFUSAL_PREFIX)
    assert "\n" not in line
    assert "\r" not in line
    return json.loads(line[len(REFUSAL_PREFIX):])


def test_exception_bases_match_azure_cli_core():
    assert issubclass(ServiceError, AzCLIError)
    assert issubclass(UserFault, AzCLIError)
    assert issubclass(PlatformGapError, ServiceError)
    assert issubclass(DesignRefusalError, UserFault)
    assert issubclass(ModePreconditionError, ServiceError)


def test_platform_gap_payload_round_trips_with_five_keys():
    payload = _payload(PlatformGapError(
        "CM-040", "gap-platform", "controller route exists but backing class is absent",
        remedy="retry after platform ask 4 ships"))
    assert set(payload) == {"capabilityId", "feasibilityState", "gap", "remedy", "delegatedTo"}
    assert payload["capabilityId"] == "CM-040"
    assert payload["delegatedTo"] is None


def test_design_refusal_payload_round_trips_with_delegate():
    payload = _payload(DesignRefusalError(
        "CM-050", "delegated", "plan operations are outside this extension",
        delegated_to="az appservice plan"))
    assert set(payload) == {"capabilityId", "feasibilityState", "gap", "remedy", "delegatedTo"}
    assert payload["remedy"] is None
    assert payload["delegatedTo"] == "az appservice plan"


def test_mode_precondition_payload_has_optional_deployment_mode():
    payload = _payload(ModePreconditionError(
        "CM-034", "supported-conditional", "RunFromStorage is off",
        remedy="enable RunFromStorage or use whole-site zip deploy",
        deployment_mode={"axis": "runFromStorage", "state": "off", "determinationMethod": "determined"}))
    assert set(payload) == {
        "capabilityId", "feasibilityState", "gap", "remedy", "delegatedTo", "deploymentMode"
    }
    assert payload["deploymentMode"] == {
        "axis": "runFromStorage", "state": "off", "determinationMethod": "determined"
    }


@pytest.mark.parametrize("kwargs", [
    {"gap": "", "remedy": "retry later"},
    {"gap": None, "remedy": "retry later"},
    {"gap": "missing backing API"},
    {"gap": "missing backing API", "remedy": ""},
])
def test_refusal_constructor_rejects_unrecoverable_payloads(kwargs):
    with pytest.raises(ValueError):
        PlatformGapError("CM-X", "gap-platform", **kwargs)


def test_mode_precondition_requires_deployment_mode_object():
    with pytest.raises(ValueError):
        ModePreconditionError("CM-034", "supported-conditional", "RunFromStorage is off", remedy="turn it on")

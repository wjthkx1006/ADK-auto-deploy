"""
validators.py — Input validation for destructive Alibaba Cloud operations.

Used as a before_tool_callback on each sub-agent. Returns a blocking error
string when a destructive tool is called with a suspicious or malformed
resource ID, preventing the model from acting on hallucinated identifiers.
Returns None to allow all other tool calls through unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from google.adk.tools import BaseTool
from google.adk.agents.callback_context import ToolContext


# ---------------------------------------------------------------------------
# Resource ID patterns
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, re.Pattern] = {
    # ECS instance:  i-  + 20 alphanumeric chars
    "instance_id": re.compile(r"^i-[a-z0-9]{20}$"),
    # Security group: sg- + 20 alphanumeric chars
    "security_group_id": re.compile(r"^sg-[a-z0-9]{20}$"),
    # VPC:            vpc- + 20 alphanumeric chars
    "vpc_id": re.compile(r"^vpc-[a-z0-9]{20}$"),
    # VSwitch:        vsw- + 20 alphanumeric chars
    "vswitch_id": re.compile(r"^vsw-[a-z0-9]{20}$"),
    # OSS bucket name: 3-63 lowercase letters, digits, hyphens; no leading/trailing hyphen
    "bucket_name": re.compile(r"^[a-z0-9][a-z0-9\-]{1,61}[a-z0-9]$"),
    # DNS record ID:  pure numeric string (Alibaba Cloud DNS record IDs)
    "record_id": re.compile(r"^\d+$"),
    # EIP allocation: eip- + 20 alphanumeric chars
    "allocation_id": re.compile(r"^eip-[a-z0-9]{20}$"),
}

# Tools that delete/release resources and the argument names that carry the
# resource identifier. All names are lowercase for case-insensitive matching.
_DESTRUCTIVE_TOOLS: dict[str, list[tuple[str, str]]] = {
    # ECS
    "deleteinstance":        [("instanceid", "instance_id")],
    "stopinstance":          [("instanceid", "instance_id")],
    "releaseinstance":       [("instanceid", "instance_id")],
    # Security group
    "deletesecuritygroup":   [("securitygroupid", "security_group_id")],
    "revokesecuritygroup":   [("securitygroupid", "security_group_id")],
    "revokesecuritygroupegress": [("securitygroupid", "security_group_id")],
    # VPC / VSwitch
    "deletevpc":             [("vpcid", "vpc_id")],
    "deletevswitch":         [("vswitchid", "vswitch_id")],
    # OSS
    "deletebucket":          [("bucketname", "bucket_name"), ("bucket", "bucket_name")],
    # DNS
    "deletedomainrecord":    [("recordid", "record_id")],
    "setdomainrecordstatus": [("recordid", "record_id")],
    # EIP
    "releaseelasticaddress": [("allocationid", "allocation_id")],
    "unassociateeipaddress": [("allocationid", "allocation_id")],
}


def _find_arg(args: Dict[str, Any], *candidates: str) -> Optional[str]:
    """Return the first matching argument value (case-insensitive key lookup)."""
    lowered = {k.lower(): v for k, v in args.items()}
    for candidate in candidates:
        if candidate in lowered:
            return str(lowered[candidate])
    return None


def validate_before_tool(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
) -> Optional[Dict]:
    """
    before_tool_callback — validates resource IDs on destructive operations.

    Returns a dict with an 'error' key to block execution, or None to proceed.
    """
    tool_name_lower = tool.name.lower()

    checks = _DESTRUCTIVE_TOOLS.get(tool_name_lower)
    if not checks:
        return None  # not a destructive tool, allow through

    for arg_name, pattern_key in checks:
        value = _find_arg(args, arg_name)
        if value is None:
            continue  # arg not present, let the model/API handle the missing param

        pattern = _PATTERNS[pattern_key]
        if not pattern.match(value):
            return {
                "error": (
                    f"[Validation] Blocked: '{value}' does not look like a valid "
                    f"{pattern_key.replace('_', ' ')} "
                    f"(expected pattern: {pattern.pattern}). "
                    "Please verify the resource ID before retrying."
                )
            }

    return None  # all checks passed

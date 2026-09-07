"""
validators.py — Input validation for Alibaba Cloud tool calls.

Two layers of protection, both enforced in before_tool_callback:

1. Resource ID format check — destructive tools (delete/release) must carry a
   well-formed resource ID. Blocks the model from acting on hallucinated IDs.

2. Security policy check — hard rules that must hold regardless of what the
   model decides based on instruction prompts:
   - Database ports (3306 / 6379 / 27017 / 5432 / 1433) must not be opened
     to 0.0.0.0/0 (the public internet).
   - All security group rules must specify a valid CIDR block.

Returns a dict with an 'error' key to block execution, or None to proceed.
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
    "deleteinstance":            [("instanceid", "instance_id")],
    "stopinstance":              [("instanceid", "instance_id")],
    "releaseinstance":           [("instanceid", "instance_id")],
    # Security group
    "deletesecuritygroup":       [("securitygroupid", "security_group_id")],
    "revokesecuritygroup":       [("securitygroupid", "security_group_id")],
    "revokesecuritygroupegress": [("securitygroupid", "security_group_id")],
    # VPC / VSwitch
    "deletevpc":                 [("vpcid", "vpc_id")],
    "deletevswitch":             [("vswitchid", "vswitch_id")],
    # OSS
    "deletebucket":              [("bucketname", "bucket_name"), ("bucket", "bucket_name")],
    # DNS
    "deletedomainrecord":        [("recordid", "record_id")],
    "setdomainrecordstatus":     [("recordid", "record_id")],
    # EIP
    "releaseelasticaddress":     [("allocationid", "allocation_id")],
    "unassociateeipaddress":     [("allocationid", "allocation_id")],
}

# ---------------------------------------------------------------------------
# Security policy: database ports that must never be opened to 0.0.0.0/0
# ---------------------------------------------------------------------------

# Map of port -> service name for clear error messages
_DB_PORTS: dict[str, str] = {
    "3306":  "MySQL",
    "6379":  "Redis",
    "27017": "MongoDB",
    "5432":  "PostgreSQL",
    "1433":  "SQL Server",
}

# Security group rule tools (ingress)
_SG_RULE_TOOLS = {"authorizesecuritygroup", "authorizesecuritygroupegress"}

# Valid CIDR pattern — basic sanity check (x.x.x.x/n)
_CIDR_PATTERN = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_arg(args: Dict[str, Any], *candidates: str) -> Optional[str]:
    """Return the first matching argument value (case-insensitive key lookup)."""
    lowered = {k.lower(): v for k, v in args.items()}
    for candidate in candidates:
        if candidate in lowered:
            return str(lowered[candidate])
    return None


def _extract_start_port(port_range: str) -> Optional[str]:
    """Extract start port from '3306/3306' or '3306' format."""
    if "/" in port_range:
        return port_range.split("/")[0].strip()
    return port_range.strip()


# ---------------------------------------------------------------------------
# Validation layers
# ---------------------------------------------------------------------------

def _check_resource_id(tool_name_lower: str, args: Dict[str, Any]) -> Optional[Dict]:
    """Layer 1: validate resource ID format on destructive operations."""
    checks = _DESTRUCTIVE_TOOLS.get(tool_name_lower)
    if not checks:
        return None

    for arg_name, pattern_key in checks:
        value = _find_arg(args, arg_name)
        if value is None:
            continue

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

    return None


def _check_security_policy(tool_name_lower: str, args: Dict[str, Any]) -> Optional[Dict]:
    """Layer 2: enforce hard security rules on security group rule operations."""
    if tool_name_lower not in _SG_RULE_TOOLS:
        return None

    # Check CIDR is present and valid
    cidr = _find_arg(args, "cidrip", "sourcecidrir", "destcidrir", "ipv6cidrip")
    if cidr is not None and not _CIDR_PATTERN.match(cidr):
        return {
            "error": (
                f"[Security] Blocked: '{cidr}' is not a valid CIDR block. "
                "Please use a format like 0.0.0.0/0 or 1.2.3.4/32."
            )
        }

    # Block database ports open to the public internet
    port_range = _find_arg(args, "portrange")
    if port_range and cidr == "0.0.0.0/0":
        start_port = _extract_start_port(port_range)
        if start_port in _DB_PORTS:
            service = _DB_PORTS[start_port]
            return {
                "error": (
                    f"[Security] Blocked: {service} port {start_port} must not be opened "
                    f"to 0.0.0.0/0 (the entire internet). "
                    f"Please specify a restricted source IP, e.g. your office IP as 1.2.3.4/32."
                )
            }

    return None


# ---------------------------------------------------------------------------
# Callback entry point
# ---------------------------------------------------------------------------

def validate_before_tool(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
) -> Optional[Dict]:
    """
    before_tool_callback — two-layer validation before any tool executes.

    Layer 1: resource ID format check on destructive operations.
    Layer 2: security policy check on security group rule operations.

    Returns a dict with an 'error' key to block execution, or None to proceed.
    """
    tool_name_lower = tool.name.lower()

    result = _check_resource_id(tool_name_lower, args)
    if result:
        return result

    result = _check_security_policy(tool_name_lower, args)
    if result:
        return result

    return None  # all checks passed

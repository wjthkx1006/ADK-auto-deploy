"""
response_parser.py — Extract and store key fields from Alibaba Cloud API responses.

Used as an after_tool_callback. Parses raw API response dicts and saves
important fields (instance IDs, public IPs, record IDs, bucket endpoints, etc.)
into tool_context.state so the model can reference them without re-querying.

Returns None in all cases — only observes, never overrides the response.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from google.adk.tools import BaseTool
from google.adk.agents.callback_context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_get(data: Any, *keys: str) -> Any:
    """Traverse nested dicts/lists safely. Returns None if any key is missing."""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        elif isinstance(data, list) and key.isdigit():
            idx = int(key)
            data = data[idx] if idx < len(data) else None
        else:
            return None
        if data is None:
            return None
    return data


def _to_dict(response: Any) -> Optional[Dict]:
    """Normalise tool response to a dict regardless of input type."""
    if isinstance(response, dict):
        return response
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ---------------------------------------------------------------------------
# Per-tool parsers
# ---------------------------------------------------------------------------

def _parse_run_instances(data: Dict, state: Dict) -> None:
    """RunInstances / CreateInstance response."""
    instance_ids = _safe_get(data, "InstanceIdSets", "InstanceIdSet")
    if isinstance(instance_ids, list) and instance_ids:
        state["last_instance_id"] = instance_ids[0]
        state.setdefault("all_instance_ids", [])
        state["all_instance_ids"] = list(
            dict.fromkeys(state["all_instance_ids"] + instance_ids)
        )


def _parse_describe_instances(data: Dict, state: Dict) -> None:
    """DescribeInstances — cache instance list with IPs."""
    instances = _safe_get(data, "Instances", "Instance") or []
    summary = []
    for inst in instances:
        ip = (
            _safe_get(inst, "PublicIpAddress", "IpAddress", "0")
            or _safe_get(inst, "EipAddress", "IpAddress")
            or ""
        )
        summary.append({
            "id":     inst.get("InstanceId", ""),
            "name":   inst.get("InstanceName", ""),
            "status": inst.get("Status", ""),
            "ip":     ip,
            "region": inst.get("RegionId", ""),
            "sg_ids": _safe_get(inst, "SecurityGroupIds", "SecurityGroupId") or [],
        })
    if summary:
        state["instance_list"] = summary
        # Keep last known public IP for easy reference
        running = [s for s in summary if s["ip"]]
        if running:
            state["last_public_ip"] = running[-1]["ip"]


def _parse_allocate_public_ip(data: Dict, state: Dict) -> None:
    """AllocatePublicIpAddress response."""
    ip = data.get("IpAddress")
    if ip:
        state["last_public_ip"] = ip


def _parse_allocate_eip(data: Dict, state: Dict) -> None:
    """AllocateEipAddress response."""
    ip = data.get("EipAddress")
    alloc_id = data.get("AllocationId")
    if ip:
        state["last_eip_address"] = ip
    if alloc_id:
        state["last_eip_allocation_id"] = alloc_id


def _parse_create_vpc(data: Dict, state: Dict) -> None:
    vpc_id = data.get("VpcId")
    if vpc_id:
        state["last_vpc_id"] = vpc_id


def _parse_create_vswitch(data: Dict, state: Dict) -> None:
    vsw_id = data.get("VSwitchId")
    if vsw_id:
        state["last_vswitch_id"] = vsw_id


def _parse_create_security_group(data: Dict, state: Dict) -> None:
    sg_id = data.get("SecurityGroupId")
    if sg_id:
        state["last_security_group_id"] = sg_id


def _parse_put_bucket(data: Dict, state: Dict) -> None:
    """CreateBucket / PutBucket — derive endpoint from request context if present."""
    bucket = data.get("BucketName") or data.get("bucket_name")
    location = data.get("Location") or data.get("location", "cn-hangzhou")
    if bucket:
        state["last_bucket_name"] = bucket
        state["last_bucket_endpoint"] = (
            f"https://{bucket}.oss-{location}.aliyuncs.com"
        )


def _parse_describe_metric_last(data: Dict, state: Dict) -> None:
    """DescribeMetricLast — cache latest metric datapoints."""
    datapoints_raw = data.get("Datapoints", "[]")
    try:
        import json as _json
        points = _json.loads(datapoints_raw) if isinstance(datapoints_raw, str) else datapoints_raw
    except Exception:
        points = []
    if isinstance(points, list) and points:
        state["last_metric_datapoints"] = points


def _parse_put_resource_metric_rule(data: Dict, state: Dict) -> None:
    """PutResourceMetricRule — save created rule ID."""
    rule_id = data.get("RuleId") or data.get("Code")
    if rule_id and data.get("Success") is not False:
        state["last_alert_rule_id"] = str(rule_id)


def _parse_describe_metric_rule_list(data: Dict, state: Dict) -> None:
    """DescribeMetricRuleList — cache alert rule list."""
    rules = _safe_get(data, "Alarms", "Alarm") or []
    if rules:
        state["alert_rule_list"] = [
            {
                "id":        r.get("RuleId", ""),
                "name":      r.get("RuleName", ""),
                "metric":    r.get("MetricName", ""),
                "threshold": r.get("Escalations", {}).get("Warn", {}).get("Threshold", ""),
                "status":    r.get("AlertState", ""),
                "enabled":   r.get("EnableState", ""),
            }
            for r in rules
        ]


def _parse_describe_system_event_count(data: Dict, state: Dict) -> None:
    """DescribeSystemEventCount — cache recent event count."""
    events = _safe_get(data, "SystemEventCounts", "SystemEventCount") or []
    if events:
        state["system_event_summary"] = [
            {
                "name":  e.get("Name", ""),
                "count": e.get("Num", 0),
            }
            for e in events if int(e.get("Num", 0)) > 0
        ]
    record_id = data.get("RecordId")
    if record_id:
        state["last_dns_record_id"] = str(record_id)


def _parse_describe_domain_records(data: Dict, state: Dict) -> None:
    records = _safe_get(data, "DomainRecords", "Record") or []
    if records:
        state["dns_record_list"] = [
            {
                "id":   r.get("RecordId", ""),
                "rr":   r.get("RR", ""),
                "type": r.get("Type", ""),
                "value": r.get("Value", ""),
                "ttl":  r.get("TTL", ""),
                "status": r.get("Status", ""),
            }
            for r in records
        ]


# ---------------------------------------------------------------------------
# Dispatch table  (lowercase tool name -> parser function)
# ---------------------------------------------------------------------------

_PARSERS = {
    "runinstances":               _parse_run_instances,
    "createinstance":             _parse_run_instances,
    "describeinstances":          _parse_describe_instances,
    "allocatepublicipaddress":    _parse_allocate_public_ip,
    "allocateeipaddress":         _parse_allocate_eip,
    "createvpc":                  _parse_create_vpc,
    "createvswitch":              _parse_create_vswitch,
    "createsecuritygroup":        _parse_create_security_group,
    "putbucket":                  _parse_put_bucket,
    "createbucket":               _parse_put_bucket,
    "adddomainrecord":            _parse_add_domain_record,
    "describedomainrecords":      _parse_describe_domain_records,
    # CloudMonitor
    "describemetriclast":         _parse_describe_metric_last,
    "describemetriclist":         _parse_describe_metric_last,
    "putresourcemetricrule":      _parse_put_resource_metric_rule,
    "describemetricrulelist":     _parse_describe_metric_rule_list,
    "describesystemeventcount":   _parse_describe_system_event_count,
}


# ---------------------------------------------------------------------------
# Callback entry point
# ---------------------------------------------------------------------------

def parse_after_tool(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> None:
    """
    after_tool_callback — parse API responses and save key fields to session state.

    Always returns None (observe-only, never overrides the response).
    """
    parser = _PARSERS.get(tool.name.lower())
    if parser is None:
        return None

    data = _to_dict(tool_response)
    if data is None:
        return None

    parser(data, tool_context.state)
    return None

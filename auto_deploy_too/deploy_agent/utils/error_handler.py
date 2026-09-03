"""
error_handler.py — Map Alibaba Cloud API error codes to clean user-facing messages.

Used as an after_tool_callback. When a tool response contains an error code,
replaces the raw JSON blob with a concise, actionable Chinese message.

Returns the cleaned message dict to override the response, or None to leave
successful responses untouched.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from google.adk.tools import BaseTool
from google.adk.agents.callback_context import ToolContext


# ---------------------------------------------------------------------------
# Error code -> user-friendly message
# ---------------------------------------------------------------------------

_ERROR_MESSAGES: dict[str, str] = {
    # ── Auth / credentials ──────────────────────────────────────────────────
    "InvalidAccessKeyId.NotFound":        "Access Key ID 不存在，请检查 ALIBABA_CLOUD_ACCESS_KEY_ID 配置是否正确。",
    "InvalidAccessKeyId.Inactive":        "Access Key 已被禁用，请在 RAM 控制台重新启用或更换。",
    "SignatureDoesNotMatch":              "签名验证失败，Access Key Secret 可能有误，请重新确认。",
    "Forbidden.Action":                   "当前账号没有执行该操作的权限，请检查 RAM 策略是否包含所需权限。",
    "Forbidden.SubUser":                  "子账号缺少权限，请联系主账号在 RAM 控制台授权。",
    "NoPermission":                       "操作被拒绝，当前账号缺少对应资源的权限。",

    # ── Resource not found ───────────────────────────────────────────────────
    "InvalidInstanceId.NotFound":         "实例不存在或已被释放，请确认实例 ID 是否正确。",
    "InvalidSecurityGroupId.NotFound":    "安全组不存在，请确认安全组 ID 是否正确。",
    "InvalidVpcId.NotFound":              "VPC 不存在，请确认 VPC ID 是否正确。",
    "InvalidVSwitchId.NotFound":          "交换机不存在，请确认 VSwitch ID 是否正确。",
    "NoSuchBucket":                       "OSS Bucket 不存在，请确认 Bucket 名称和地域是否正确。",
    "InvalidDomainName.NoExist":          "域名不存在，请确认域名是否已在阿里云 DNS 解析中添加。",
    "DomainRecordNotBelongToUser":        "解析记录不属于当前账号，无法操作。",

    # ── Resource already exists ─────────────────────────────────────────────
    "BucketAlreadyExists":                "Bucket 名称已被占用（全局唯一），请换一个名称重试。",
    "InvalidInstanceName.Duplicated":     "实例名称重复，请换一个名称。",
    "RecordAlreadyExist":                 "该解析记录已存在，如需修改请使用更新操作。",

    # ── Quota / limits ───────────────────────────────────────────────────────
    "InstanceLimitExceeded":              "ECS 实例数量已达上限，请释放闲置实例后再试，或申请提升配额。",
    "QuotaExceeded.Eip":                  "弹性公网 IP 数量已达上限，请释放闲置 EIP 后再试。",
    "QuotaExceeded.SecurityGroupRule":    "安全组规则数量已达上限（默认 200 条），请删除不用的规则。",
    "BucketNumberExceeded":               "OSS Bucket 数量已达上限（默认 100 个），请删除闲置 Bucket。",

    # ── Invalid parameters ───────────────────────────────────────────────────
    "InvalidParameter":                   "请求参数有误，请检查参数格式是否符合 API 要求。",
    "MissingParameter":                   "缺少必要参数，请补充后重试。",
    "InvalidInstanceType.ValueNotSupported": "该实例规格在当前地域不可用，请换一个规格或地域。",
    "InvalidImageId.NotFound":            "镜像不存在，请确认镜像 ID 是否正确。",
    "InvalidRegionId.NotFound":           "地域 ID 无效，请使用正确的地域代码，如 cn-hangzhou。",
    "InvalidZoneId.NotFound":             "可用区 ID 无效，请确认该可用区是否存在。",
    "InvalidIpProtocol.Malformed":        "IP 协议格式有误，请使用 tcp / udp / icmp / all。",
    "InvalidPortRange.Malformed":         "端口范围格式有误，请使用 开始端口/结束端口 的格式，如 80/80。",
    "InvalidCidrBlock.Malformed":         "IP 地址段格式有误，请使用 CIDR 格式，如 0.0.0.0/0 或 1.2.3.4/32。",

    # ── Instance state conflicts ─────────────────────────────────────────────
    "IncorrectInstanceStatus":            "当前实例状态不允许此操作，请等待实例状态变为 Running 后重试。",
    "InvalidInstanceStatus.NotStopped":   "该操作需要实例处于已停止状态，请先停止实例。",
    "InvalidInstanceStatus.NotRunning":   "该操作需要实例处于运行中状态，请先启动实例。",

    # ── Network / VPC ────────────────────────────────────────────────────────
    "InvalidVpc.Mismatch":                "实例与 VPC 不在同一地域，请检查地域配置。",
    "VpcQuotaExceeded":                   "VPC 数量已达上限，请删除闲置 VPC 后再试。",
    "VSwitchQuotaExceeded":               "交换机数量已达上限，请删除闲置 VSwitch 后再试。",

    # ── ECS cloud assistant ──────────────────────────────────────────────────
    "InstanceNotRunning":                 "实例未运行，无法执行云助手命令，请先启动实例。",
    "CloudAssistantNotRunning":           "云助手 Agent 未运行，请等待实例完全启动后重试。",

    # ── CloudMonitor ─────────────────────────────────────────────────────────
    "ResourceNotExists":                  "监控资源不存在，请确认实例 ID 和地域是否正确。",
    "ContactGroupNotExists":              "告警联系组不存在，请先在云监控控制台创建联系组。",
    "RuleNotExists":                      "告警规则不存在，请确认规则 ID 是否正确。",
    "ExceedingQuota":                     "告警规则数量已达上限，请删除不用的规则后重试。",
    "InvalidMetricName":                  "指标名称无效，请使用标准指标名，如 cpu_total / memory_usedutilization。",
    "InvalidTimeRange":                   "查询时间范围无效，开始时间不能晚于结束时间，且不超过 31 天。",
    "MetricNotFound":                     "未找到该实例的监控数据，实例可能刚创建或云监控 Agent 未安装。",

    # ── Throttling ───────────────────────────────────────────────────────────
    "Throttling":                         "请求过于频繁，已被限速，请稍等片刻后重试。",
    "ServiceUnavailable":                 "阿里云服务暂时不可用，请稍后重试。",
}

_FALLBACK_MESSAGE = (
    "操作失败（错误码：{code}）：{message}。如需帮助，请提供错误码到阿里云文档查询。"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_error(response: Any) -> Optional[tuple[str, str]]:
    """
    Return (error_code, raw_message) if the response looks like an API error,
    otherwise return None.
    """
    data: Optional[Dict] = None

    if isinstance(response, dict):
        data = response
    elif isinstance(response, str):
        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict):
                data = parsed
        except (json.JSONDecodeError, ValueError):
            return None

    if data is None:
        return None

    # Alibaba Cloud error responses always contain a 'Code' or 'code' field
    code = data.get("Code") or data.get("code")
    message = data.get("Message") or data.get("message") or ""

    if code and isinstance(code, str) and code not in ("", "200", "OK"):
        return (code, message)

    return None


# ---------------------------------------------------------------------------
# Callback entry point
# ---------------------------------------------------------------------------

def handle_error_after_tool(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> Optional[Dict]:
    """
    after_tool_callback — replace raw API error JSON with clean user messages.

    Returns a clean error dict to override the response, or None to leave
    successful responses untouched.
    """
    error = _extract_error(tool_response)
    if error is None:
        return None  # success, pass through

    code, raw_message = error

    friendly = _ERROR_MESSAGES.get(code)
    if friendly:
        clean_message = f"[错误] {friendly}"
    else:
        clean_message = f"[错误] {_FALLBACK_MESSAGE.format(code=code, message=raw_message)}"

    return {"error": clean_message, "code": code}

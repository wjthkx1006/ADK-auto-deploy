"""ADK Web UI 多 Agent 阿里云部署助手。"""

from __future__ import annotations

import shutil
from os import getenv
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from deploy_agent.sub_agents import create_ecs_agent, create_oss_agent

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

MODEL = getenv("MODEL_NAME", "deepseek/deepseek-v4-flash")
ALIYUN_ACCESS_KEY_ID = getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
ALIYUN_ACCESS_KEY_SECRET = getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")


def resolveUvxPath() -> str:
    """兼容 Windows 和 Linux 容器环境。"""
    try:
      detectedPath = shutil.which("uvx")
      if detectedPath:
        return detectedPath
    except Exception:
      pass

    homeDir = Path.home()
    candidatePaths = [
      homeDir / ".local" / "bin" / "uvx",
      homeDir / ".local" / "bin" / "uvx.exe",
    ]

    for candidatePath in candidatePaths:
      if candidatePath.exists():
        return str(candidatePath)

    return "uvx"


serverParams = StdioServerParameters(
    command=resolveUvxPath(),
    args=[
        "alibabacloud.mcp-proxy@latest",
        "--site-type",
        "CN",
        "--read-timeout",
        "300",
    ],
    env={
        "ALIBABA_CLOUD_ACCESS_KEY_ID": ALIYUN_ACCESS_KEY_ID,
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET": ALIYUN_ACCESS_KEY_SECRET,
    },
)

toolset = McpToolset(
    connection_params=StdioConnectionParams(server_params=serverParams, timeout=300),
)

ecsAgent = create_ecs_agent(MODEL, toolset)
ossAgent = create_oss_agent(MODEL, toolset)

ROOT_INSTRUCTION = """
你是阿里云部署助手。根据用户意图路由到对应子代理：

- ecs_agent -> ECS/服务器/虚拟机/部署/安装软件/释放实例 等
- oss_agent -> OSS/存储桶/Bucket/对象存储/上传文件 等
- 其他常规问答直接回复，不路由到子代理

注意：如果用户意图不明确，先询问再路由。
"""

root_agent = LlmAgent(
    name="deploy_agent",
    model=MODEL,
    instruction=ROOT_INSTRUCTION,
    sub_agents=[ecsAgent, ossAgent],
)

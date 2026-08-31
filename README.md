# DevOps 一键部署：ADK 多 Agent 阿里云部署助手

本项目 = Google ADK 多 Agent 阿里云部署助手（ECS / OSS 子代理）+ 阿里云 DevOps 自动化流水线。
推送 `main` 分支后，流水线自动创建服务器并部署一个可对话的部署助手 Web UI。

## 项目结构

```text
.
├── auto_deploy_too/            # ADK 多 Agent 部署助手（构建镜像的源码）
│   ├── deploy_agent/
│   │   ├── agent.py            # 根路由代理：按意图分发到子代理
│   │   └── sub_agents/
│   │       ├── ecs_agent.py    # ECS 部署/管理（创建、装软件、释放）
│   │       └── oss_agent.py    # OSS 对象存储管理
│   ├── requirements.txt        # 依赖：google-adk[a2a,mcp]==2.4.0
│   └── .env.example            # 环境变量模板（本地复制为 .env 使用）
├── aliyun-terraform/           # Terraform：VPC/安全组/RAM 角色/ECS
├── scripts/                    # 流水线辅助脚本（等待服务器就绪）
├── .github/workflows/          # deploy.yml / destroy.yml
└── Dockerfile                  # 构建 ADK 部署助手镜像（端口 8000）
```

## ADK 部署助手介绍

这是一个基于 **Google ADK（Agent Development Kit）** 构建的多 Agent 应用，部署后是一个浏览器聊天界面，
用自然语言就能操作阿里云 ECS / OSS 等资源，全程无需手写 CLI 命令。

### 架构

```text
用户（浏览器聊天界面）
   │
   ▼
deploy_agent（根路由代理）        ← 判断意图，分发任务
   ├── ecs_agent                 ← ECS 部署/管理
   └── oss_agent                 ← OSS 对象存储
        │
        ▼  （子代理共用同一套 MCP 工具）
alibabacloud.mcp-proxy           ← 阿里云官方 MCP 代理（uvx 运行）
   └── 阿里云 OpenAPI（ECS / VPC / OSS / RAM ...）
```

### 工作方式

1. 用户在聊天界面输入自然语言，例如「在杭州创建一台 2 核 4G Ubuntu 服务器」
2. 根路由代理（`deploy_agent`）按意图路由到对应子代理：
   - ECS / 服务器 / 虚拟机 / 部署 / 安装软件 / 释放实例 → `ecs_agent`
   - OSS / Bucket / 对象存储 / 上传文件 → `oss_agent`
   - 其他常规问答直接回复，不路由
3. 子代理通过 MCP 工具实时查询 / 调用阿里云 API，关键参数先询问用户（最多 3 轮），确认后再执行
4. 执行完成返回结果（公网 IP、Bucket 地址等）

### 子代理能力

| 子代理 | 主要能力 |
| --- | --- |
| ecs_agent | 创建 ECS（自动建 VPC / VSwitch / 安全组并开端口）、查询实例、绑定弹性公网 IP、云助手执行命令 / 安装软件、释放实例（二次确认后执行） |
| oss_agent | 创建 / 列出 / 删除 Bucket、设置 ACL、查看配置 |

### 技术要点

- **模型**：`deepseek/deepseek-v4-flash`，通过 `DEEPSEEK_API_KEY` 认证
- **工具**：`alibabacloud.mcp-proxy`（阿里云官方 MCP 代理，由 `uvx` 拉起），用 `ALIBABA_CLOUD_ACCESS_KEY_ID / SECRET` 调用阿里云 OpenAPI
- **依赖**：`google-adk[a2a,mcp]==2.4.0`，子代理共享同一套 MCP 工具集，由根代理按意图分发
- **界面**：`adk web` 提供聊天 Web UI，本地调试与生产容器使用同一入口

## 一键部署流程

推送到 `main` 分支后，GitHub Actions 自动执行：

```text
1. OIDC 免密认证阿里云
2. 创建 OSS 状态桶（不存在时）
3. Terraform apply：创建/更新 VPC、安全组、RAM 角色、ECS 服务器
   - 服务器通过 cloud-init 自动安装 Docker
4. 构建 ADK 部署助手镜像（auto_deploy_too/）并推送到 ACR
5. 等待新服务器就绪（实例 Running + Docker 可用）
6. 云助手 RunCommand 登录 ACR 拉取镜像并启动容器（80 端口）
```

访问地址是流水线日志里的 `ECS_PUBLIC_IP`（即 `http://<公网IP>`），打开即是部署助手聊天界面。
支持自然语言操作：创建/查询/释放 ECS、创建 OSS Bucket、在实例上安装软件等。

## 前置条件：RAM 角色权限

OIDC 角色 `GitHubActionsRole`（已在阿里云 RAM 控制台配置信任 GitHub OIDC）需要以下权限，
流水线才能创建服务器和读写状态：

- **ECS**：`RunInstances` / `CreateInstance` / `DescribeInstances` / `DeleteInstance` / `RunCommand` / `DescribeInvocations` / `DescribeInvocationResults` / 安全组相关
- **VPC**：`CreateVpc` / `CreateVSwitch` / `Describe*`（VPC、交换机、安全组）
- **RAM**：`CreateRole` / `AttachPolicyToRole` / `DetachPolicyFromRole` / `PassRole`（给 ECS 绑定拉取 ACR 的角色）
- **OSS**：`CreateBucket` / `PutObject` / `GetObject` / `ListObjects`（存放 Terraform 状态）
- **ACR**：镜像推送权限（已有）

> 说明：Terraform 状态存放在 OSS 桶 `devops-tfstate-1612262844714561`，流水线首次运行时自动创建。

## GitHub Secrets 配置

### ACR 登录凭据（个人版限制）

ACR **个人版不支持 OIDC/STS 临时凭证登录**（官方限制），推镜像必须使用固定密码。
请在 GitHub 仓库 Settings → Secrets and variables → Actions 中配置：

- `ACR_USERNAME`：阿里云账号登录名（控制台 ACR 个人版 → 访问凭证可查看）
- `ACR_PASSWORD`：ACR 固定密码（控制台 ACR 个人版 → 访问凭证 → 设置固定密码）

### 部署助手运行所需 Secrets

容器内的 ADK 多 Agent 启动后需要以下 Secrets，流水线通过 `docker run -e` 注入容器：

- `DEEPSEEK_API_KEY`：DeepSeek 模型 API Key（与本地 `.env` 中一致）
- `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`：
  MCP 代理调用阿里云 API 用的 AK/SK（需具备 ECS/OSS/VPC 等权限）
- `SSH_PRIVATE_KEY`（可选）：Agent 执行云助手外的 SSH 操作时使用

当前实例（华东 1 杭州，2024-09 后开通的新个人版）使用**独立域名**：

```text
crpi-2xt8naw5x975swse.cn-hangzhou.personal.cr.aliyuncs.com
```

注意新个人版实例不再使用 `registry.cn-hangzhou.aliyuncs.com` 旧域名，且**不支持
ECS 用 RAM 角色免密拉取**（`docker-credential-acr` 对 `crpi-` 域名无效），
流水线会在 ECS 上用固定密码 `docker login` 后再拉取镜像。

### 角色信任策略（重要坑点）

角色名实际为 `githubactionsrole`（全小写），其信任策略必须放行 GitHub OIDC 担任该角色。
**GitHub 的 `sub` 声明格式包含账号 ID 和仓库 ID**（形如
`repo:owner@ownerId/repo@repoId:...`），通配符必须放在仓库 ID 之后，否则 AssumeRole 会报
`AuthenticationFail.NoPermission`。当前仓库对应的完整信任策略：

```json
{
  "Statement": [
    {
      "Action": "sts:AssumeRole",
      "Effect": "Allow",
      "Principal": {
        "Federated": "acs:ram::1612262844714561:oidc-provider/GitHub"
      },
      "Condition": {
        "StringEquals": {
          "oidc:aud": "sigstore",
          "oidc:iss": "https://token.actions.githubusercontent.com"
        },
        "StringLike": {
          "oidc:sub": "repo:1027364768abc-bot@268580485/DevOps@1321269390:*"
        }
      }
    }
  ],
  "Version": "1"
}
```

## 本地运行部署助手（可选）

```bash
cd auto_deploy_too
cp .env.example .env        # 填入 DEEPSEEK_API_KEY / 阿里云 AK/SK
pip install -r requirements.txt
adk web --host 0.0.0.0 --port 8000 ./deploy_agent
# 浏览器打开 http://localhost:8000
```

> 提示：`adk web` 用于开发和调试；生产环境由流水线构建镜像部署。

## 注意事项

- **测试阶段每次推送都会重建服务器**：cloud-init 的 `runcmd` 只在实例首次启动执行，
  因此流水线用 `terraform apply -replace` 强制重建 ECS，保证初始化脚本一定生效，
  代价是公网 IP 每次都会变化。流程稳定后可去掉 `-replace` 复用同一台实例。
- 若需要固定公网 IP，可给 ECS 增加弹性公网 IP（EIP），避免重建后地址变化。
- 容器内 `adk web` 监听 8000，流水线映射到宿主机 80 端口，安全组只开放 80。

## Terraform 本地操作（可选）

```bash
cd aliyun-terraform
export ALICLOUD_ACCESS_KEY_ID=xxx
export ALICLOUD_ACCESS_KEY_SECRET=xxx
terraform init
terraform plan     # 预览变更
terraform apply    # 应用
terraform destroy  # 销毁全部资源
```

`ansible/` 目录保留为手工初始化服务器的可选方案，自动流水线已改用 cloud-init，不再依赖 SSH。

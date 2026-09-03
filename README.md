# Aliyun DevOps ADK Deploy — 阿里云自动化部署与多 Agent 云资源管理平台手

`git push main` → **GitHub Actions** OIDC 认证 → **Terraform** 创建 VPC / 安全组 / ECS → **cloud-init** 安装 Docker → 构建镜像推送 **ACR** → 云助手拉取镜像启动容器 → `http://<公网IP>`

`http://<公网IP>` → 自然语言输入 → **deploy_agent** 路由 → `ecs_agent` / `oss_agent` / `dns_agent` / `monitor_agent` → **alibabacloud.mcp-proxy** → 阿里云 OpenAPI

---

## 目录

- [项目结构](#项目结构)
- [架构](#架构)
- [子代理能力](#子代理能力)
- [utils 工具层](#utils-工具层)
- [一键部署流程](#一键部署流程)
- [基础设施](#基础设施)
- [前置配置](#前置配置)
- [本地运行](#本地运行)
- [销毁环境](#销毁环境)
- [注意事项](#注意事项)

---

## 项目结构

```text
.
├── auto_deploy_too/
│   ├── deploy_agent/
│   │   ├── agent.py                  # 根路由代理
│   │   ├── sub_agents/
│   │   │   ├── ecs_agent.py          # ECS 部署/管理/安全组
│   │   │   ├── oss_agent.py          # OSS 对象存储
│   │   │   ├── dns_agent.py          # DNS 域名解析
│   │   │   └── monitor_agent.py      # 云监控
│   │   └── utils/
│   │       ├── validators.py         # 销毁类操作资源 ID 格式校验
│   │       ├── response_parser.py    # 提取关键字段存入 session state
│   │       └── error_handler.py      # API 错误码 → 可读中文提示
│   ├── requirements.txt
│   └── .env.example
├── aliyun-terraform/                 # VPC / 安全组 / RAM 角色 / ECS
├── ansible/                          # 可选：手工初始化服务器
├── scripts/                          # 流水线辅助脚本
├── .github/workflows/
│   ├── deploy.yml                    # 推送 main 触发：创建服务器 + 部署
│   └── destroy.yml                   # 手动触发：销毁全部资源
└── Dockerfile                        # 构建部署助手镜像（端口 8000）
```

---

## 架构

```text
用户（浏览器聊天界面）
        │
        ▼
  deploy_agent（根路由代理）
  ├── ecs_agent       ECS / 虚拟机 / 安全组 / 安装软件
  ├── oss_agent       OSS / Bucket / 对象存储
  ├── dns_agent       DNS / 域名解析
  └── monitor_agent   监控指标 / 告警规则 / 系统事件
        │
        │  每次工具调用经过三层处理：
        │
        ├─ before_tool_callback
        │   └── validators.py        校验销毁类操作资源 ID 格式，拦截异常 ID
        │
        ▼  工具执行
        │
        └─ after_tool_callback
            ├── response_parser.py   提取 IP / 实例 ID / Bucket 地址等存入 session state
            └── error_handler.py     API 错误码 → 简洁中文提示

alibabacloud.mcp-proxy（阿里云官方 MCP 代理，uvx 拉起）
└── 阿里云 OpenAPI（ECS / VPC / OSS / DNS / CloudMonitor ...）
```

技术栈：`google-adk[a2a,mcp]==2.4.0` · `DeepSeek v4 Flash` · `alibabacloud.mcp-proxy`

---

## 子代理能力

### ecs_agent
| 功能 | 说明 |
|---|---|
| 创建 ECS | 交互式收集参数，自动创建 VPC / VSwitch / 安全组，获取公网 IP |
| 查询实例 | 列出当前所有实例及状态 |
| 安全组管理 | 开/关端口，查询/添加/删除规则；数据库端口强制询问来源 IP |
| 安装软件 | 云助手执行命令，先问配置再执行（MySQL / Nginx / Docker / Redis 等） |
| 绑定弹性 IP | 分配并绑定 EIP |
| 释放实例 | 二次确认后执行，不可恢复 |

### oss_agent
| 功能 | 说明 |
|---|---|
| 创建 Bucket | 收集名称 / ACL / 存储类型 / 地域 |
| 列出 / 查看 | 所有 Bucket 或指定 Bucket 详情 |
| 设置 ACL | 修改访问权限 |
| 删除 Bucket | 确认后删除 |

### dns_agent
| 功能 | 说明 |
|---|---|
| 添加解析记录 | 支持 A / CNAME / MX / TXT / AAAA，TTL 默认 600s |
| 查询记录 | 列出域名下所有记录，支持筛选 |
| 修改记录 | 常用于 ECS 重建后更新 A 记录 IP |
| 删除 / 暂停 / 恢复 | 确认 RecordId 后操作 |

### monitor_agent（免费功能）
| 功能 | 说明 |
|---|---|
| 查询实时指标 | CPU / 内存 / 磁盘 / 网络入出带宽 / TCP 连接数 |
| 查询历史趋势 | 指定时间段内的指标变化 |
| 创建告警规则 | 阈值告警，触发通知联系组（免费） |
| 查询 / 删除告警规则 | 管理现有规则 |
| 查询系统事件 | 实例重启 / OOM / 磁盘 IO 挂起等异常事件 |

> 不支持（收费）：自定义指标上报、站点监控（HTTP 拨测）。

---

## utils 工具层

每个子代理都注册了相同的 `before_tool_callback` 和 `after_tool_callback`，实现三个功能：

| 文件 | 触发时机 | 作用 |
|---|---|---|
| `validators.py` | 工具调用前 | 对 `DeleteInstance` / `DeleteBucket` / `DeleteDomainRecord` 等约 15 种销毁类工具校验资源 ID 格式（正则匹配），格式异常直接返回错误，工具不执行 |
| `response_parser.py` | 工具返回后 | 从 API 响应中提取公网 IP、实例 ID、安全组 ID、Bucket 地址、DNS 记录 ID、监控数据等，存入 session state，避免重复查询 |
| `error_handler.py` | 工具返回后 | 识别约 50 种阿里云错误码，替换原始 JSON 为简洁中文提示，包含具体修复建议 |

---

## 一键部署流程

推送到 `main` 分支后，`deploy.yml` 自动执行：

```
1. OIDC 免密认证阿里云（无需在 Secrets 存 AK/SK）
2. 创建 OSS 状态桶 devops-tfstate-1612262844714561（幂等）
3. Terraform apply -replace 强制重建 ECS
   └── cloud-init 自动安装 Docker
4. 构建 ADK 部署助手镜像并推送到 ACR
5. 等待服务器就绪（实例 Running + Docker 可用）
6. 云助手 RunCommand 登录 ACR、拉取镜像、启动容器（80 端口）
7. 输出访问地址 http://<ECS_PUBLIC_IP>
```

---

## 基础设施

Terraform 管理以下资源（`aliyun-terraform/main.tf`）：

| 资源 | 规格 / 说明 |
|---|---|
| VPC | `172.16.0.0/12`，杭州 |
| VSwitch | `172.16.1.0/24`，可用区 cn-hangzhou-i |
| 安全组 | 开放 80 端口（HTTP） |
| ECS | `ecs.t6-c1m1.large`（1c1g），Ubuntu 22.04，5Mbps 带宽 |
| RAM 角色 | `ECSRoleForACRAuto`，绑定 ECS，拥有 ACR 只读权限 |

> 状态文件存放在 OSS 桶 `devops-tfstate-1612262844714561`。

---

## 前置配置

### RAM 角色权限

OIDC 角色 `githubactionsrole` 需要以下权限才能运行流水线：

- **ECS**：`RunInstances` / `DescribeInstances` / `DeleteInstance` / `RunCommand` / `DescribeInvocations` / 安全组相关
- **VPC**：`CreateVpc` / `CreateVSwitch` / `Describe*`
- **RAM**：`CreateRole` / `AttachPolicyToRole` / `DetachPolicyFromRole` / `PassRole`
- **OSS**：`CreateBucket` / `PutObject` / `GetObject` / `ListObjects`
- **DNS**：`AddDomainRecord` / `DescribeDomainRecords` / `UpdateDomainRecord` / `DeleteDomainRecord`
- **ACR**：镜像推送权限

角色信任策略（`oidc:sub` 须包含正确的账号 ID 和仓库 ID）：

```json
{
  "Statement": [{
    "Action": "sts:AssumeRole",
    "Effect": "Allow",
    "Principal": { "Federated": "acs:ram::<ALIYUN_ACCOUNT_ID>:oidc-provider/GitHub" },
    "Condition": {
      "StringEquals": {
        "oidc:aud": "sigstore",
        "oidc:iss": "https://token.actions.githubusercontent.com"
      },
      "StringLike": {
        "oidc:sub": "repo:<GITHUB_USER>@<GITHUB_USER_ID>/<REPO_NAME>@<REPO_ID>:*"
      }
    }
  }],
  "Version": "1"
}
```

> `oidc:sub` 格式说明：GitHub 的 `sub` 声明包含账号 ID 和仓库 ID（非用户名和仓库名），通配符必须放在仓库 ID 之后，否则 AssumeRole 会报 `AuthenticationFail.NoPermission`。实际值可在阿里云 RAM 控制台 → 身份提供商 → 查看 OIDC Token 中获取。

### GitHub Secrets

| Secret | 用途 |
|---|---|
| `ACR_USERNAME` | ACR 个人版登录名（控制台 → 访问凭证） |
| `ACR_PASSWORD` | ACR 固定密码（控制台 → 访问凭证 → 设置固定密码） |
| `DEEPSEEK_API_KEY` | DeepSeek 模型 API Key |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` | 容器内 MCP 代理调用阿里云 API 用的 AK |
| `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 对应 SK |
| `SSH_PRIVATE_KEY` | 可选，SSH 操作时使用 |

> ACR 个人版（新 `crpi-` 域名）不支持 RAM 角色免密拉取，流水线在 ECS 上用固定密码 `docker login` 后拉取镜像。

### 密钥安全

```
GitHub Secrets（云端加密）
   ↓ docker run -e 注入
ECS 容器环境变量（仅内存）
   ↓ getenv() 读取
ADK 多 Agent 运行
```

`.gitignore` 排除 `.env`，`.dockerignore` 排除 `auto_deploy_too/.env`，密钥不进仓库、不进镜像。

---

## 本地运行

```bash
cd auto_deploy_too
cp .env.example .env        # 填入 DEEPSEEK_API_KEY 和阿里云 AK/SK
pip install -r requirements.txt
adk web --host 0.0.0.0 --port 8000 ./deploy_agent
# 浏览器打开 http://localhost:8000
```

环境变量说明（`.env.example`）：

| 变量 | 说明 |
|---|---|
| `MODEL_NAME` | 模型标识，默认 `deepseek/deepseek-v4-flash` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET` | 阿里云 AK/SK |
| `DEFAULT_REGION` | 默认地域，`cn-hangzhou` |
| `DEFAULT_KEY_PAIR_NAME` | 默认密钥对名称 |
| `DEFAULT_VPC_NAME` 等 | 创建资源时的默认命名 |

---

## 销毁环境

在 GitHub Actions → `destroy.yml` → **Run workflow** 手动触发，会：

1. `terraform destroy` 销毁 ECS / VPC / 安全组 / RAM 角色
2. 清空并删除 OSS 状态桶
3. 校验确认 ECS 已全部释放

> 销毁不可恢复，执行前确认无需保留的数据。

也可本地操作：

```bash
cd aliyun-terraform
export ALICLOUD_ACCESS_KEY_ID=xxx
export ALICLOUD_ACCESS_KEY_SECRET=xxx
terraform init
terraform destroy
```

---

## 注意事项

- **每次推送重建 ECS**：流水线用 `-replace` 强制重建，公网 IP 每次变化。流程稳定后可去掉 `-replace` 复用实例。
- **固定 IP**：给 ECS 增加弹性公网 IP（EIP）可避免重建后地址变化。
- **数据库端口安全**：助手开放 3306 / 6379 / 27017 等端口时会强制询问来源 IP，避免对公网暴露。
- **DNS 生效时间**：新增解析记录通常几分钟内生效，可用 `nslookup` 验证。
- **监控免费范围**：`monitor_agent` 只使用 CloudMonitor 免费功能（系统指标查询 + 基础阈值告警），不涉及收费的自定义指标和站点监控。

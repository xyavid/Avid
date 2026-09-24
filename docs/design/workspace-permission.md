# 工作区、权限与沙箱模型

本文件是「工作区归属」「三轴权限模型」「四级 deny 阶梯」与「沙箱边界」的**冻结规格**：
命名、默认值、判定顺序、危险与敏感范围、提示文案与不变量。实现与测试以本文件为准；
行为与本文件不一致时，先改这里再改代码。

适用阶段：18（工作区与权限）与 26（安全分层）。出图见
`dev/architecture/phase-18-workspace-permissions.svg`、`dev/architecture/phase-26-security-layers.svg`。

## 0. 一张图与一句话

```
Agent Intent → Policy → Approval → Capability → Sandbox → OS
                 │
                 └── Trust / Admin / Audit
```

**Agent Safety = Policy Engine × Human/Model Review × OS Sandbox × Trust Boundary
× Network Boundary × Audit。**

两个正交的问题，不要混为一谈：

* **三种用户模式**决定「这次运行需要多少人工监督」；
* **sandbox** 决定「即使模型或分类器判断错了，它物理上还能造成多大伤害」。

## 1. 概念与所有权

**工作区（Workspace）**：一个本地目录，承担三件事——干活的地点（文件工具相对路径基准、
`bash` 的 cwd、`.avid/` 与 `.tasks/` 的落点）、权限边界（越过它的操作叫「越界」）、
会话归属。

| 数据 | 谁创建 | 谁是权威 | 存在哪 | 可变性 |
|---|---|---|---|---|
| 工作区根目录 `root` | 用户 | 文件系统本身 | 注册表 | 随目录移动而变，视为新工作区 |
| 工作区 id | 由 `root` 派生 | 派生函数 | 注册表 + 会话 header | 不可变 |
| 工作区默认权限 | 用户 | 注册表 | 注册表 | 可变（**不接受 `full`**） |
| 会话 → 工作区归属 | 创建会话时 | 会话 header | 会话 JSONL 第一行 | 不可变 |
| 运行级三轴 + 阶梯 + 沙箱 + 审计 | 装配期 | `RunSecurity` | 内存（`RunState.security`） | 一次运行内不变 |
| 能力账本 | 审批通过时 | `ApprovalLedger` | 内存 | 只加不减，只活一次运行 |
| 审计记录 | 每次裁决 | `policy/audit.py` | `~/.avid/audit/audit-YYYY-MM-DD.jsonl` | 只追加 |

派生规则、注册表只读语义、跨工作区护栏、新增工作区的界面入口（服务端文件夹选择器）
沿用阶段 18 的结论，见本文件 git 历史与 `docs/status/CURRENT_STATE.md`。

**运行期落地**：`RunRegistry.start` 先定位会话所属工作区，再
`build_run_security(mode, root, full_ack, source)` 装配出 `RunSecurity`（三轴 + 阶梯 +
沙箱规格 + 审计），交给 `RunState`，同一份对象也进 `run_started` 事件与审计。**唯一装配点**
是 `policy/permission.py:build_run_security`——运行里每一处"这次的安全边界是什么"都读它。

## 2. 三个用户模式 = 三轴预设

模式名是给人用的一个词；内核里它是三个**互相独立**的参数：

| 模式 | approval | sandbox | network | 一句话 |
|---|---|---|---|---|
| `manual`（默认） | `user` | `workspace` | `restricted` | 沙箱内免问；危险命令与越界一律问人 |
| `auto` | `classifier` | `workspace` | `restricted` | 沙箱内免问；越界与危险由分类器裁决，判不准即拒（**不问人**） |
| `full` | `none` | `disabled` | `open` | 不问、不套沙箱、不限制网络；**必须显式授权** |

三轴各自回答一个不同的问题，**代码里不许互相推导**：

* `approval` 决定谁来回答 REVIEW；
* `sandbox` 决定物理伤害上限；
* `network` 是一级安全边界，独立于文件系统。

`tests/test_modes.py::test_axes_are_orthogonal` 与 `test_no_code_path_derives_sandbox_from_approval`
（静态扫描 `sandbox=` 的右值里不许出现 `approval`）是这条的机械守护。`manual` 与 `auto`
的沙箱逐字相同——"`auto` = 关沙箱"在这张表上不可能出现。

**一条必然推论**：沙箱可用时，`manual` 对**沙箱能保证的区内常规动作不问**。否则
`approval` 与 `sandbox` 退化成同一件事，正交就成了空话。代价是旧 `strict` 的"每次写入
都问"不再存在，替代品是"沙箱 + 越界/危险问人"（见 §2.2 迁移）。

### 2.1 `full` 三重锁（full ≠ default）

1. **CLI**：`--permission full` 必须同时给 `--allow-full-access`，否则 `parser.error`
   （退出码 2，不回落到别的模式）。
2. **Web**：请求体必须带 `full_access_ack: true`，否则 422（`StartRunIn` 的
   `model_validator`）；`svc` 还有第二道（`InvalidRequest`）。
3. **默认值**：工作区默认权限**不接受 `full`**——`WorkspaceRegistry._default_mode` 报
   `WorkspaceError`，`CreateWorkspaceIn.permission` 的类型里根本没有它，CLI 的
   `workspace permission/add` 的 choices 里也没有。前端 `resolvePermissionMode` 遇到
   "工作区默认值是 full"按不认识的值回落。

每次 `full` 运行都必须在三处留下痕迹：`run_started` 的 `sandbox: "disabled"`、审计记录的
`sandbox.policy = "disabled"`、界面常驻的「沙箱：已禁用」标记。

### 2.2 旧模式名迁移

注册表里可能存过 `strict` / `workspace` / `system`。读回时按「收紧或持平」映射，
**绝不迁到 `full`**，并且把迁移说明**打出来**（不静默改用户存过的安全设置）：

| 旧名 | 新名 | 理由 |
|---|---|---|
| `strict` | `manual` | 旧语义"每个受管动作都问"在新模型里由"沙箱内免问 + 沙箱保证"表达 |
| `workspace` | `manual` | 旧语义是"危险命令**问人**"；`auto` 是"无人在环"，把问人迁成无人是减少监督 |
| `system` | `auto` | 旧语义"默认免问、仅危险命令问"；`auto` 把那一问交给分类器（判不准即拒）是收紧 |

写路径（CLI 参数、REST DTO、注册表 `add`/`set_permission`）**不接受旧名**：`validate_mode`
对它们一律报错，只有 `migrate_mode` 认。这样"新写的配置"不可能是旧语义。

## 3. 判定顺序与决策表

### 3.1 流水线

```
tool_call{name, arguments}
   │
   ├─ brokerize()          Tool Broker（policy/action.py）
   │     normalize  归一化命令原文（折叠空白）
   │     identify   目标路径：文件工具精确解析；bash 扫字面量记号（启发式）
   │     classify   risks{提权,递归删除,权限属主,磁盘,服务,计划任务,包管理,
   │                       网络执行,强推,丢弃改动,清理未跟踪,-delete,远程,容器,敏感路径}
   │                 + network + operations(read/write)
   │     → Action（**不含裁决**）
   │
   ├─ engine.decide()      Policy Engine（policy/engine.py）
   │     1 硬拒绝 ADMIN → deny
   │     2 四级 deny 阶梯 → deny（**包括 full**）；ask 档 → REVIEW
   │     3 越界 → REVIEW
   │     4 危险类别 → REVIEW
   │     5 沙箱降级（要沙箱而不可用，且是受管工具）→ REVIEW
   │     6 成本规则（subagent）→ REVIEW
   │     7 其余 → allow
   │
   ├─ REVIEW 由 approval 回答
   │     user       → 问人；同意则记进能力账本
   │     classifier → 确定性审查（判不准即拒），不打扰人
   │     none       → 直接放行，`answered_by="none"` 进审计
   │
   ├─ tools/shell.py       沙箱：argv 前置 mount/netns/env，按能力账本挂载授予
   └─ policy/audit.py      每条裁决落盘（放行也记）
```

### 3.2 决策表

| 动作类别 | `manual` | `auto` | `full` | 依据 |
|---|---|---|---|---|
| 硬拒绝（`rm -rf /`、`mkfs`、写块设备、关机…） | ⛔ | ⛔ | ⛔ | ADMIN 档；任何回答都不放行 |
| ADMIN 凭据（`~/.ssh`、`/etc/shadow`、shell 配置…） | ⛔ | ⛔ | ⛔ | 同上 |
| PROJECT 默认（`.git/hooks`、`.github/workflows`…） | ⛔ | ⛔ | ⛔ | 仓库策略；本会话的批准不可覆盖 |
| ask 档（`.env`） | ? 问一次 | ⛔ 分类器拒 | + | §4 |
| 危险命令（提权、磁盘、服务、远程、容器…） | ? 写类别 | ⛔ 分类器拒 | + | 与模式无关的**事实**，出口由 approval 定 |
| 越界（文件工具与 bash） | ? 记路径 | ⛔ | + | §5.4 |
| 沙箱降级下的受管工具 | ? 逐个问 | ⛔ | + | §5.5 |
| 成本规则 `subagent` | ? 一次 | + | + | 非安全规则 |
| 区内只读（`read_file` / `glob`） | + | + | + | 与现状一致：区内读取从不审批 |
| 区内常规命令（沙箱保证） | + | + | + | §2 的推论 |

`verdict` 只有 `allow` / `deny` 两个终局；REVIEW 是中间态，`Decision.kind` 记录
**因为什么被审**（`hard|credential|rule|outside|danger|degraded|cost`），
`Decision.answered_by` 记录**谁答的**（`policy|ledger|user|classifier|none`）。
两个字段都进事件与审计——这是"界面上分不出'危险'和'这次不行'"那类问题的修复。

## 4. 四级 deny 阶梯

原则：**deny 高于 ask，ask 高于 allow；下层的 allow 永不抵消上层的 deny**。

| 层 | 谁写 | 能否被放松 | 覆盖什么 |
|---|---|---|---|
| ADMIN DENY | 代码内置（`policy/rules.py: ADMIN_PATH_RULES`） | 不能 | 凭据、shell 配置、`/etc/shadow`、`/etc/sudoers`、`/root`、`~/.avid` |
| SYSTEM DENY | `~/.avid/policy.toml`（宿主用户自己写） | 不能 | 这台机器上额外不许碰的东西 |
| PROJECT DENY | `<ws>/.avid/policy.toml`（**随仓库分发，不可信**） | 不能被本会话的批准放松 | 这个仓库里额外不许碰的东西 |
| USER ALLOW | 本次运行的能力账本 | — | 只加不减 |

外加一个**唯一的放松点**：`~/.avid/policy.toml` 的 `[allow]` 段。它只能放松
**可放松的内置默认**（`relaxable=True`，见下），放不开 ADMIN 与各级 DENY。

> 原则④：**repo 不能削弱宿主机安全策略。** 因此 `<ws>/.avid/policy.toml` 里的 `[allow]`
> 段会被**忽略并记一条 note** 出现在运行摘要里；仓库文件只能加严。

### 4.1 配置文件格式

```toml
# ~/.avid/policy.toml（SYSTEM 档；[allow] 只有这一级有效）
[deny]
read  = ["~/work/secret/**"]
write = ["/srv/**"]

[allow]
write = [".github/workflows"]   # 放松内置的项目默认（逐条、逐仓库都可写具体路径）

# <工作区>/.avid/policy.toml（PROJECT 档；[allow] 被忽略）
[deny]
read  = ["**/private/**"]
```

* 模式先展开 `~` / `$HOME` / 环境变量；相对模式相对**工作区根**（所以 `[allow]` 能写成
  `.github/workflows` 精确到某个仓库）。
* 匹配口径：`**/` = 零层或多层目录，`**` / `*` = 任意（跨 `/`），`?` = 一个字符；
  没有通配符时按目录语义（命中目录本身或它下面任何东西）。`*` 跨 `/` 比 gitignore 更严，
  而 `**/` 支持"零层"是为了让 gitignore 习惯的写法不会静默失效。
* **失败模型**：SYSTEM 文件读不出来或不合 schema → 抛 `PolicyConfigError`，运行**不启动**
  （安全配置坏了不能静默变宽）；PROJECT 文件同样坏掉 → 忽略 + note（它只能加严，忽略不会
  变宽）。未知段、未知口径、非字符串列表都算不合 schema。

### 4.2 内置清单（实现与文档逐条对应）

**ADMIN（读+写都拒，不可放松）**：`~/.ssh`、`~/.aws`、`~/.gnupg`、`~/.docker/config.json`、
`~/.netrc`、`~/.git-credentials`、`~/.config/gh`、`~/.kube`、`~/.config/gcloud`、
`/etc/shadow`、`/etc/gshadow`、`/etc/sudoers`、`/root`、`~/.bashrc`、`~/.bash_profile`、
`~/.profile`、`~/.zshrc`、`~/.bash_history`、`~/.zsh_history`、`~/.avid`。

**PROJECT 内置默认（可被 SYSTEM `[allow]` 逐条放松）**：

| 目标 | 口径 | 判定 | 理由 |
|---|---|---|---|
| `.git/hooks` | 写 | deny | hook 会在后续 git 操作里执行任意代码 |
| `.git/config` | 写 | deny | 可改 URL / 凭据助手 |
| `.github/workflows` | 写 | deny | CI 定义会在带机密的环境里执行 |
| `.avid`（工作区内） | 写 | deny | agent 的配置与审计不接受自改 |
| `.env` / `.env.*` | 读+写 | **ask** | 通常含凭据，但日常也要读；deny 会训练用户习惯性点同意 |

**硬拒绝（`policy/action.py: DENY_PATTERNS`，7 条）**：`rm -rf /` 或 `~`、
`mkfs*`、`dd of=/dev/*`、`> /dev/sd*`、fork 炸弹、关机/重启、`chmod -R … /`。

**危险类别（`DANGER_PATTERNS` + 敏感路径，15 类）**：提权、递归/强制删除、权限或属主、
磁盘与文件系统、系统服务与进程、计划任务、系统级包管理、网络取回即执行、强制推送、
丢弃工作区改动、删除未跟踪文件、批量删除、远程访问或传输、容器或编排、敏感路径。
"危险"是**事实**，"危险之后怎么办"由 approval 决定——这是与阶段 18 的实质差别。

## 5. 沙箱（Sandbox Manager）

**边界在 LLM 之外**：模型、提示词、规则表、分类器都不参与"能不能"这件事的最终决定，
能生效的是子进程的 mount 与 network namespace。

### 5.1 后端与探测

* 后端只有 **bubblewrap（bwrap）**：它同时给"挂载控制"与"netns"，是唯一能一条命令满足
  §5.2 全部要求的实现。
* **Landlock 只上报不参与**：本机实测 ABI 3 只能管文件系统，**网络规则要 ABI 4**，而三个
  预设里凡有沙箱的组合都是 `network=restricted`。只做一半的沙箱比"说清没有沙箱"更危险。
  探测结果（`landlock_abi`）如实出现在 `capabilities.sandbox` 里。
* **可用性是实测的**：探测会真的跑一次 `--ro-bind / / --unshare-net`（"命令存在"不等于
  "能用"，容器里 bwrap 常装上却因权限失败），结果缓存；`AVID_SANDBOX_BIN` 可指向另一个实现。

### 5.2 `sandbox=workspace` 的 argv（顺序即语义）

| 顺序 | 挂什么 | 为什么是这个顺序 |
|---|---|---|
| 1 | `--ro-bind / /` | 整个系统只读：区外**写得动**才算边界 |
| 2 | `--dev /dev` `--proc /proc` `--tmpfs /tmp` | 空 `/tmp` 必须**先**挂：工作区常在 `/tmp` 下，后面的 bind 才盖得住它 |
| 3 | 能力授予（`--ro-bind` / `--bind`，逐条来自账本） | 用户/分类器批准过的区外路径 |
| 4 | 掩蔽（`--tmpfs` 目录、`--ro-bind <空文件>` 文件） | **必须晚于授予**：批准一个父目录（例如 `$HOME`）不能连带把它的 `.ssh` 掀开。掩蔽是宿主策略，授予是本次运行的能力，冲突时掩蔽赢 |
| 5 | `--bind <工作区> <工作区>` | 工作区可写 |
| 6 | `--unshare-net`（restricted 时）`--unshare-pid/-uts/-ipc`、`--new-session`、`--die-with-parent` | 网络与命名空间隔离 |
| 7 | `--clearenv` + 逐条 `--setenv`（白名单） | 环境：只有跑命令需要的，凭据类变量名一律剥离 |
| 8 | `--chdir <工作区>` + `--` + 命令 | |

掩蔽清单：目录 `~/.avid`、`~/.ssh`、`~/.aws`、`~/.gnupg`、`~/.config/gh`、`~/.kube`、
`~/.config/gcloud`；文件 `~/.netrc`、`~/.git-credentials`、`~/.docker/config.json`、
`~/.bashrc`、`~/.bash_profile`、`~/.profile`、`~/.zshrc`、`~/.bash_history`、`~/.zsh_history`。
**掩蔽的是解析后的真实路径**：WSL 里 `~/.aws` 是符号链接，而 bwrap 不能在符号链接上挂
tmpfs（实测报 `Can't mount tmpfs on …: No such file or directory`）。掩蔽真实路径反而更严：
不管从哪条路径过去，看到的都是空的。

环境白名单（`ENV_ALLOW_EXACT` + `LC_*` 前缀）：`PATH` / `HOME` / `LANG` / `TERM` / `TZ` /
`USER` / `SHELL` / `EDITOR` / `TMPDIR` 与语言工具链的路径类变量等；即使命中白名单，名字里
含 `TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL|AUTH|COOKIE|SESSION|DSN|DATABASE_URL|PRIVATE`
的也一律剥离。刻意**不**透传 `DISPLAY` / `WAYLAND_DISPLAY`（能操作宿主桌面）、
`XDG_RUNTIME_DIR` / `SSH_AUTH_SOCK` / `DBUS_SESSION_BUS_ADDRESS`（同用户 socket）。
`HOME` 固定为"算掩蔽清单时用的那个家"——两者各算各的会变成"掩蔽 A 家、却把 B 家设成 HOME"。

### 5.3 `sandbox=disabled`（full）

不套 argv、不改环境（`child_env` 返回 `None` 表示继承），但**三件事实照样记录**：
`run_started.sandbox="disabled"`、审计的 `sandbox.policy="disabled"`、界面上的「沙箱：已禁用」。
`policy=disabled` 不是"降级"（`degraded=False`）：它是一开始就不要这条边界。

### 5.4 能力授予（升级 = 授予能力，不是关沙箱）

账本的键就是能力的类型：`("command", 归一化命令原文)` / `("path", 绝对路径, ro|rw)` /
`("tool", 工具名)`。批准一次越界后：

* 文件工具的行为由 `RunState.outside_allowed` 放行（只读账本，不做决定）；
* `bash` 的下一次执行把该路径作为 `--ro-bind` / `--bind` 挂进来——**只挂这条路径**，
  它的父目录仍然只读（所以"批准了文件"≠"批准了它所在的目录"）；
* 掩蔽路径上的授予被**拒绝挂载**（并记一条 warning）：掩蔽晚于授予这条顺序再加一道保险。

"`full` 不等于无所不能"：ADMIN 与阶梯中的 deny 在 `full` 下同样成立
（`rm -rf /`、读 `~/.ssh`、写 `.git/hooks` 都被拒）。

### 5.5 降级：永不静默

`build_spec` 在四种情况下给出 `available=False`：找不到 bwrap / 探测失败 / 没有运行级
工作区根 / 后端不能强制网络边界（`network=restricted` 时）。此时：

| 模式 | 行为 |
|---|---|
| `manual` | 受管工具（`bash` / `write_file` / `edit_file`）**逐个问人**（= 阶段 18 的 `strict`）；区内只读仍然不问 |
| `auto` | **失败关闭**：区外/危险一律拒，并给出可执行的下一步（装 bwrap 或切 manual） |
| `full` | 不受影响（它本来就不要沙箱） |

降级是**可见**的：CLI 打一行「⚠ 沙箱不可用…」，`run_started` 的 `sandbox_state.degraded=true`
带原因，界面上的标记变成「沙箱：不可用」。调用方完全没给沙箱规格时（直调 hook、老路径）
按 `sandbox.UNMANAGED` 处理——同样是降级，不是"没有边界"。

## 6. 审计（Audit）

落点 `~/.avid/audit/audit-YYYY-MM-DD.jsonl`（`AVID_AUDIT_DIR` 可覆盖；`AVID_HOME` 生效时
落在 `<AVID_HOME>/audit`），只追加、按天分文件。它在掩蔽清单里、也被 ADMIN 规则禁读写，
所以模型改不了它。

每条 `kind="decision"` 的记录带：时间、运行标识、模式与三轴快照、工具、归一化命令、目标
（含区外目标）、风险类别、是否出网、裁决（`verdict`）、`decision_kind`、`tier`、
`answered_by`、账本键、沙箱状态。另有 `kind="run_start"`（含会话与工作区）。

两条取舍：**审计要留下命令本身**（抹掉命令原文等于抹掉审计的用途），只把内联凭据
（`Authorization: Bearer …`、`token=…`）抹成 `***`；**写失败不改结论**——磁盘满只加
`failures` 计数并记日志，绝不打断运行，但计数会随 `AuditLog.summary()` 带进运行摘要，
所以"这次没留下记录"看得见。

## 7. 提示文案与事件

### 7.1 CLI

危险、越界、ask 档、成本规则、降级**共用同一条提示**（`policy.permission.ask_user`），
差别只在 `<原因>` 那一行：

```
⚠ 需要确认：<原因>
  工具 <工具名> <参数 JSON>
  允许执行？[y/N]
```

`<原因>` 的形态由引擎组装（审批回调三参数签名不变）：`提权`、
`目标 /etc/hostname 在工作区之外`、`安全策略要求逐次批准（.env 通常含凭据）`、
`并行派发 subagent（会额外消耗多次模型调用）`、`沙箱不可用（找不到 bubblewrap（bwrap））`。

硬拒绝与阶梯 deny 不需要回答者：

```
⛔ 已拒绝：<原因>
```

每次运行开始打一行安全宣告（stderr，不污染 stdout）：
`[安全] manual（approval=user，network=restricted）｜沙箱：工作区（bwrap，无出网）`，
降级时追加一行 ⚠。

### 7.2 回传给模型的文案（按 `kind` 分档）

| `kind` | 文案要点 |
|---|---|
| `hard` | 硬拒绝（`<原因>`）…**永久禁止**，不要重试、不要改写绕过 |
| `credential` | 目标是受保护的宿主资源…**任何模式与任何批准都无效** |
| `rule` | 命中安全策略…**本会话的批准无法覆盖** |
| `danger` | 危险命令未获批准（`<类别>`）…改用非破坏性做法 |
| `outside` | 目标在工作区之外且未获批准（`<绝对路径>`）…在工作区内完成 |
| `degraded` | 沙箱不可用…让用户在 manual 下逐条批准，或先装 bubblewrap |
| `cost` | 本次未获用户批准（成本类） |
| 分类器拒绝 | 自动审查判定风险过高…auto 下无人可以批准它 |

模型对"永远不许"与"这次不行"的反应完全不同，混为一谈会让它反复重试——这是文案分档的
唯一理由。

### 7.3 事件

`run_started` 增加 `approval` / `sandbox` / `network` / `sandbox_state` / `sandbox_notes`
（`permission` 仍是模式名）。`tool_call_denied.kind` 从 `hard|user` 扩为
`hard|credential|rule|danger|outside|degraded|cost`。**事件类型集合不变**。
`GET /api/meta` 的 `capabilities.sandbox` 上报后端探测结果（backend/available/network/
reason/landlock_abi）。

## 8. 接口与前端落地

* **CLI**：`--permission {manual,auto,full}`、`--allow-full-access`；`--yes` 仍然只是
  "回答者"（替所有审批答是，**不关沙箱、不越过 deny**）；`avid workspace permission` 的
  choices 没有 `full`。
* **REST**：`StartRunIn.permission: Literal["manual","auto","full"] | None` +
  `full_access_ack: bool`；`CreateWorkspaceIn.permission: Literal["manual","auto"] | None`。
  两个 DTO 都是 `extra="forbid"`，所以前后端字段名必须逐字一致（`test_wire_contract.py`）。
* **前端**：选择器三个预设；选 `full` **先弹二次确认**（Dialog，确认按钮写"我明白，关闭沙箱"），
  取消则什么都不发生；确认后 `buildStartRunInput` 统一填 `full_access_ack`
  （唯一判断点是 `needsFullAck`）。沙箱状态常驻的只有一枚图标（四档四种形状，颜色另分），
  `沙箱：工作区（无出网）` / `沙箱：已禁用` / `沙箱：不可用（原因）` 这句事实在**悬停或
  聚焦的气泡**里——`full` 仍然是第一眼看得见的（图标与色调），但不再常驻占掉输入条一行
  （窄窗口里那格文字曾被挤成三行）。
* **模式不持久化在前端**（`uiStore` 只存纯界面偏好）；工作区默认权限是唯一可持久化的位置，
  且它不接受 `full`。

## 9. 不变量与失败模型

| ID | 不变量 | 守护者 | 绕过路径检查 |
|---|---|---|---|
| I-P1 | 硬拒绝清单里的命令在任何模式、任何回答下都不执行 | `engine.decide` 第 1 步 | 唯一入口是 `decide`；`auto_approve` 也走它 |
| I-P2 | 危险命令在任何模式下都至少经过一次 REVIEW | `engine.decide` 第 4 步 | 新增工具若自带执行路径会绕过 → 契约测试枚举工具注册表 |
| I-P3 | 四级 deny 不可被下层 allow 抵消 | `Ladder.check`（deny 全局优先） + `verdict_for` | 放松点只有 SYSTEM `[allow]`，且只对 `relaxable` 规则 |
| I-P4 | 文件工具越界**失败关闭**：没有账本记录就不放行 | 账本只由 `decide` 写、工具只读 | `tools/workspace.py` 的 `outside_ok` 缺省为假；`tools/files.py` 另有一道 deny/ask 兜底 |
| I-P5 | 运行级安全规格不漏传给子 agent | `tools/subagent.py` 逐字段前传（含 `security`） | 漏传 → 子 agent 自己重算一份规格（沙箱可能不同、审计分家） |
| I-P6 | 三轴互相独立，没有任何一处从 approval 推导 sandbox | `tests/test_modes.py` 的两条守卫 | 静态扫描 `sandbox=` 的右值 |
| I-P7 | `full` 必须显式授权，且不能成为默认值 | `full_grant_error` 的三个调用点 + DTO 类型 + 注册表 | CLI 双开关 / Web ack / `workspace_default` 一律拒 |
| I-P8 | 沙箱不可用时**不静默降级** | `SandboxSpec.degraded` → 决策层；`UNMANAGED` 兜底 | 每个模式都有明确的降级行为，且三处可见 |
| I-P9 | 掩蔽晚于能力授予（批准父目录掀不开子目录） | `argv_prefix` 的挂载顺序 | `tests/test_sandbox.py` 的顺序断言 + E2E 的 `masks_survive_approval_grants` |
| I-P10 | `full` 不放行 ADMIN 与阶梯 deny | `decide` 的第 1、2 步 | E2E 四个臂逐条对照（`rm -rf /`、`~/.ssh`、`.git/hooks`） |
| I-P11 | 审计失败不改裁决 | `AuditLog.write` 的 try/except + `failures` 计数 | 计数进运行摘要，所以"静默失效"看得见 |
| I-P12 | 运行级工作区根在每个落点都一致 | `RunState.workspace_root` + `build_run_security` 的 `_default_root` | 漏一处就是"策略按 A 判、沙箱按 B 挂" |
| I-P13 | 跨工作区不会静默读到别人的会话 | `JsonlSessionRepo.open` 的归属校验 | 阶段 18 的护栏，未改动 |

**失败模型汇总**：问不到人（EOF / 无回答者 / 审批超时）→ 拒绝；`decide` 抛异常 →
`trigger_hooks` 的失败关闭约定按拒绝处理；SYSTEM 策略损坏 → 不启动；PROJECT 策略损坏 →
忽略 + note；`bwrap` 不可用 → 按 §5.5 降级且可见；审计写不进去 → 计数 + 日志，不打断。
`full` 缺少授权凭据 → 拒绝启动（CLI 退出码 2 / Web 422），**不回落到别的模式**。

## 10. E2E 证据

`benchmarks/sandbox_boundary/`：真 `svc` + 真会话 + 真工具 + 真沙箱 + 真审批表 + 真审计，
四个臂（`manual` / `manual_yes` / `auto` / `full`）跑同一批攻击性探针，产物 `artifact.json`
里 17 条布尔结论。最值得看的两条：

* `invisible_outside_write_is_blocked_by_the_sandbox`：命令里的路径由解释器拼出来，
  broker 扫不到目标，**三种带沙箱的模式都放行执行**，然后被只读挂载挡住——
  边界在 LLM 之外的实测版；
* `full_arm_really_touched_the_host`：同一个探针在 `full` 下真的在宿主上留下了文件。

## 11. 未验证假设与「重新考虑」的信号

| 假设 | 条件 | 出现什么信号时重新考虑 |
|---|---|---|
| 「同意一次」只需活一次运行 | 本轮不做持久授权 | 每次运行都要重复点同一路径/命令，明显碍事 |
| bash 的目标识别靠启发式 | 只用来触发 REVIEW；拦住它的是沙箱 | 出现"以为被拦、实际没拦"且**没有**沙箱兜底的真实事故 |
| 危险清单覆盖够用 | 15 类均可测 | 评测/真实使用中抓到一个造成不可逆损失却未被问的命令 |
| `network=restricted` = 零出网 | 联网需求走宿主 `web_search` | 出现"命令内必须访问某个域名"的真实任务 → 域名级能力授予（本机代理 + 白名单） |
| 只有 bwrap 一个后端 | Landlock ABI 3 强制不了网络 | 目标机器没有 userns 且必须离线工作 → Landlock ABI 4 的 TCP 规则 |
| SYSTEM `[allow]` 是唯一放松点 | 宿主用户手写一行 | 放松点被误用成"图省事的全放开" → 要求每次放松都带到期条件 |
| 审计只落本地文件 | 单人本地运行时 | 需要防篡改/集中收集时（独立文件系统、追加预算、外部 sink） |
| 旧名迁移一次性 | 注册表读回时映射并打日志 | 迁移说明被忽略、用户以为自己还在旧档 → 首次运行要求确认 |

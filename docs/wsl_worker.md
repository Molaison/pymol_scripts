# 配置可选 WSL worker

需要 PLIP 或已获许可的 PyRosetta 时，在 WSL 启动 worker，让 Windows PyMOL 通过 HTTP 使用后端。普通结构审阅、SSH 数据源和本地命令不需要 worker。需要 Windows PyMOL、可用 WSL、独立 Python 环境及所需后端；准备完成后健康检查会显示服务状态和每个后端的可用性。

## 准备环境和后端

在 WSL 的仓库根目录创建隔离环境。下面的路径和 Python 版本是占位符，请替换；`uv` 是可选环境管理工具：

```bash
cd /path/to/PymolPlugins
uv venv .venv-worker --python <backend-supported-python>
. .venv-worker/bin/activate
python scripts/probe_worker_env.py --pretty
```

按 PLIP/OpenBabel 官方说明或 PyRosetta 许可方提供的材料，将需要的后端安装到该环境；创建环境不会安装后端。PyRosetta 单独授权，仓库不下载或分发它。用同一解释器检查导入：

```bash
python scripts/probe_worker_env.py --import-optional --pretty
```

可导入只证明发现和导入成功，不证明每个指标与当前 Rosetta API 兼容。历史 Debian WSL 曾使用社区 `openbabel-wheel` 和 PLIP 固定版本作为隔离环境回退；其依赖、版本及失败原因见 [历史参考](07_reference_worker_2026-11-06.md#tested-plip-fallback-on-this-wsl-host)，不应当成通用安装来源。

## 配置认证并启动

在已激活的 WSL 环境生成 token，并在仓库根目录启动：

```bash
export PYMOLPLUGINS_WORKER_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
scripts/start_worker.sh
```

将同一 token 和 URL 提供给**启动 PyMOL 的 Windows 进程**；WSL 环境变量不会自动传给 Windows。在临时 PowerShell 会话设置后，从该会话启动 PyMOL：

```powershell
$env:PYMOLPLUGINS_WORKER_TOKEN = '<同一个 token>'
$env:PYMOLPLUGINS_WORKER_URL = 'http://127.0.0.1:8765'
```

不要把 token 写入仓库、PML、会话文件或 issue，也不要放在命令行 `--token` 参数中。服务前台运行，用 Ctrl+C 停止。启动脚本依次选择 `PYMOLPLUGINS_WORKER_PYTHON`、`.venv-worker/bin/python`、`python3`；可用 `--python`、`--port`、`--max-body-mb` 指定非秘密选项。默认要求 token；`--allow-no-token` 只适合隔离的本机诊断。

## 验证并提交分析

在保有 token 的 WSL 终端执行 `python scripts/probe_worker_env.py --check-worker --pretty`。在 Windows PyMOL 执行：

```pymol
pp_load
pp_worker_health
pp_worker_capabilities
```

服务健康时 `status` 为 `ok`；PLIP 和 PyRosetta 分别报告 `available`。缺少后端是能力状态，不意味着服务必然崩溃。Windows 还可用带 Bearer token 的 PowerShell `Invoke-RestMethod` 请求 `/v1/health`。

加载自己的复合物并明确链或配体后，才提交示例：

```pymol
pp_rosetta_metrics_async selection=complex, target_chains=A, binder_chains=B, metrics="cms,bunsat_bound_heavy,bunsat_interface_delta"
pp_interact_run plip_overview, source=complex, partner2="resn LIG and chain X and resi 501"
pp_worker_jobs
```

替换 `complex`、链及配体选择，且仅在对应后端可用时运行。GUI 推荐异步；headless 场景可用 `async_mode=0` 阻塞等待。取消是抑制结果处理，不承诺中断正在运行的 PLIP/PyRosetta C++ 计算；Host 超时后 worker 仍可能忙碌。

## 配置可选 staging

需要从 WSL 读取结构文件时，明确允许根目录：

```bash
scripts/start_worker.sh --stage-root structures=/home/user/structures --max-stage-mb 256 --stage-timeout 60
```

替换实际目录；`--stage-root` 可重复，完全不指定时 staging 关闭。客户端发送 alias `structures` 和根目录内相对路径，例如 `run_01/model.pdb`，不能请求任意绝对路径。Annotator 的 WSL root/alias 配置按 CSV 保存；CSV 本身仍需 Windows 可访问。直接 SSH 数据源无需该服务。

服务解析符号链接后检查根目录范围，只提供普通文件。默认上限 256 MiB、60 秒；`.part` 下载完成并校验后原子发布到缓存。底层 FUSE 读取本身卡住时 Python 无法强制中断。缓存的完整身份 token 基于文件元数据而非内容摘要；人为保持全部身份字段的原位改写不在其新鲜度保证内。

## 理解接口和限制

| 接口 | 用途 |
| --- | --- |
| `GET /v1/health`、`GET /v1/capabilities` | 服务及后端健康、支持能力 |
| `POST /v1/analyze` | 传入 PDB 文本计算 Rosetta 指标 |
| `POST /v1/interactions/plip` | 传入结构文本和原子映射，返回 PLIP 相互作用 |
| `POST /v1/stage/stat`、`/v1/stage/stat-batch`、`/v1/stage/file` | 允许根目录内的文件身份与只读传输 |

默认请求体上限 10 MiB；Host JSON 客户端响应上限 20 MiB，拒绝重定向。批量 stat 最多 256 项、64 KiB，返回每项结果；只有旧 worker 明确返回 `404/not_found` 才顺序回退。staging 不是通用文件服务器，不提供任意目录列表、写入、删除、shell 或 Python 执行。

## 排查连接与后端

| 现象 | 操作 |
| --- | --- |
| 连接被拒绝 | 检查 WSL 服务进程、端口、WSL 本地 health、Windows localhost forwarding 和 URL |
| HTTP 401 | 确认 WSL 与启动 PyMOL 的 Windows 进程使用相同 token，必要时重启 PyMOL |
| HTTP 413 | 缩小选择，或明确调整 `--max-body-mb` |
| PLIP/PyRosetta 不可用 | 用实际 worker 解释器运行导入 probe，检查依赖、许可和 Python/CPU 兼容性 |
| Host 超时但服务忙碌 | 等待或停止 worker；缩小输入，勿把超时当作计算取消 |
| Windows 无法访问 WSL | 先检查本地转发；必要时按 [详细参考](07_reference_worker_2026-11-06.md#fallback-exact-wsl-interface-address) 使用当前准确 WSL IP、token 和防火墙 |

默认绑定 `127.0.0.1`。非 loopback 明确启用时需要 token；不要向局域网公开明文服务。记录后端版本、参数、输入摘要和科学定义；CMS 与两种 bunsat 指标不可混同。PLIP、OpenBabel、ANVIL、MSMS 各有独立许可；详细协议、历史安装尝试和限制见 [worker 参考](07_reference_worker_2026-11-06.md)。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="启动并检查服务"></a>
- [启动并检查服务](07_reference_worker_2026-11-06.md#%E5%90%AF%E5%8A%A8%E5%B9%B6%E6%A3%80%E6%9F%A5%E6%9C%8D%E5%8A%A1)
<a id="current-scope-and-limitations"></a>
- [Current scope and limitations](07_reference_worker_2026-11-06.md#current-scope-and-limitations)
<a id="1-prepare-an-isolated-environment"></a>
- [1. Prepare an isolated environment](07_reference_worker_2026-11-06.md#1-prepare-an-isolated-environment)
<a id="tested-plip-fallback-on-this-wsl-host"></a>
- [Tested PLIP fallback on this WSL host](07_reference_worker_2026-11-06.md#tested-plip-fallback-on-this-wsl-host)
<a id="2-configure-authentication"></a>
- [2. Configure authentication](07_reference_worker_2026-11-06.md#2-configure-authentication)
<a id="3-start-the-worker"></a>
- [3. Start the worker](07_reference_worker_2026-11-06.md#3-start-the-worker)
<a id="4-verify-inside-wsl"></a>
- [4. Verify inside WSL](07_reference_worker_2026-11-06.md#4-verify-inside-wsl)
<a id="5-verify-from-windows-and-pymol"></a>
- [5. Verify from Windows and PyMOL](07_reference_worker_2026-11-06.md#5-verify-from-windows-and-pymol)
<a id="6-endpoints-and-limits"></a>
- [6. Endpoints and limits](07_reference_worker_2026-11-06.md#6-endpoints-and-limits)
<a id="7-networking-modes"></a>
- [7. Networking modes](07_reference_worker_2026-11-06.md#7-networking-modes)
<a id="preferred-localhost-forwarding"></a>
- [Preferred: localhost forwarding](07_reference_worker_2026-11-06.md#preferred-localhost-forwarding)
<a id="fallback-exact-wsl-interface-address"></a>
- [Fallback: exact WSL interface address](07_reference_worker_2026-11-06.md#fallback-exact-wsl-interface-address)
<a id="8-troubleshooting"></a>
- [8. Troubleshooting](07_reference_worker_2026-11-06.md#8-troubleshooting)
<a id="9-license-and-provenance-requirements"></a>
- [9. License and provenance requirements](07_reference_worker_2026-11-06.md#9-license-and-provenance-requirements)

</details>

#!/usr/bin/env python3
"""Build a directly installable PyMOL Plugin Manager package."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_ROOT = ROOT / "pymolplugins"
PLUGIN_NAME = "pymol_plugins"
INSTALL_TEXT = """# 安装 PymolPlugins（{version}）

本包版本为 {version}。需要带 Qt 图形界面和 Plugin Manager 的 PyMOL。

## 第一次使用 Structure Annotator

1. 在 PyMOL 打开 **Plugin > Plugin Manager > Install New Plugin**。
2. 选择下载的原始 ZIP；不要解压。安装完成后完全退出并重启 PyMOL。
3. 打开 **Plugin > PymolPlugins: Structure Annotator**。右上角 **?** 随时可查看帮助。无需输入 PyMOL 命令。
4. 在 **Open from** 选择 **This computer**，或选择已有的 SSH **Host** 别名。
5. 在 **Path** 粘贴真实的结构文件夹、CSV 或 TSV 路径；也可以用 **Browse…**。
6. 点 **Open**。一般不用改 **Import options**。如需 **Structure column**，
   选择包含 PDB/CIF 文件路径的列，不是分数或名称列。
7. 在 **Review** 查看结构和分数，选择标签，再点 **Save and next**。

保存后进入下一条。普通点选只修改草稿，不会立即保存。快捷键、Alt+数字和 Ctrl+click 是可选的加速方式，不是开始审阅的前提。相对结构路径默认相对于表格目录；CSV 和结构源文件不会被标注流程改写。

已有 `sum_scores.csv` 和同目录 `review.json` 时，打开 CSV 即可读取配置；`display_columns` 必须使用表中实际存在的字段，`label` 是描述性名称，人工标签另存。修改配置写入 `review.override.json`，不会覆盖生成的 `review.json`。

## SSH 开始前

没有服务器可选时，点 **Set up SSH… > First SSH connection…**。
远端 Path 用 /data/review/scores.tsv 或 ~/review/structures，不是 Windows 盘符。

SSH profile 是 `~/.ssh/config` 中保存的服务器 `Host` 名字，不是密码。PyMOL 在 Windows 运行时，默认读取该电脑的 `%USERPROFILE%/.ssh/config`，不会默认读取 WSL 的 SSH 配置。

**Connections…** 可以选择已有的 SSH config 或登录 program；通常不需要修改。请先在同一台 Windows 电脑上完成首次主机信任和私钥或密码等已有 SSH 设置，并确认 `ssh my-lab` 能连接。插件不创建服务器账号、不管理密码，也不会修改全局 SSH 配置。

### 使用内置 Automatic OTP

1. 在 **Connections… > SSH server** 选择服务器，例如 `f101`，启用 **Automatic OTP**。
2. 在 **OTP secret** 填写认证器的 Base32 设置密钥，不是当前六位验证码。在 **Advanced** 中留空 **SSH program**，然后点 **OK**。若之前保存了 `.py` wrapper，先清空该路径；插件使用系统 OpenSSH，无需安装或修改外部 wrapper。
3. 回到 **Open from** 选择同一 Host，输入远端路径并打开。原 SSH config、跳板/代理与 known_hosts 继续生效；每组 Host 和 SSH config 独立配置。

默认密钥仅在当前 PyMOL 进程使用；在 Windows 勾选 **Remember encrypted key on this computer** 后，由当前 Windows 用户的 DPAPI 加密保存。取消对话框不更改已保存值；取消 **Automatic OTP** 并点 **OK** 会删除该主机保存的密钥。密钥不写入 SSH URL、review.json 或评分表。

默认识别 Verification code、OTP、One-time password 等提示；服务器提示不同时，可在 **Advanced > OTP prompt** 填写匹配提示的正则表达式。OTP 使用 SHA1、六位、30 秒；每次连接最多提交一次，被拒绝后不自动重试。同进程同密钥避免复用时间窗口，临近过期时等待下一窗口；不同进程和外部工具不共享窗口锁。它不填写普通密码、私钥口令，也不自动接受新主机。未启用内置 OTP 的旧 wrapper 方式仍可使用。

远端需要 `python3`。不需要 SSHFS 或 WSL worker。远端表格和结构只读；标注、状态和 `review.override.json` 保存在本地，原 CSV、TSV、PDB 和 CIF 文件不变。

连续审阅可用 **Data > Continue last review** 恢复最近的位置和筛选。暂时无法判断的候选可在 **More > Mark current for later** 标记，之后用 **Next unreviewed** 继续；**Next marked for later** 可回看这些候选。分数卡片会区分当前审阅集中的相对排名与绝对通过规则。

无真实凭据的示例：

```sshconfig
Host my-lab
  HostName server.example.org
  User your-username
```

| 错误 | 下一步 |
| --- | --- |
| 找不到 SSH profile | 检查 Windows SSH config 中是否有相同的 `Host`；自定义配置可在 **Connections…** 选择。 |
| `Permission denied`、密钥或 OTP 失败 | 先让 Windows 终端中的 `ssh my-lab` 成功；账号和服务器权限由现有 SSH 环境或管理员提供。 |
| 远端没有 Python 3 | 确认 `ssh my-lab python3 --version` 可用，必要时请管理员安装。 |
| 表格打开但没有结构 | 选择实际包含 `.pdb`、`.cif` 或其 `.gz` 路径的 **Structure column**。 |
| 长时间等待 | 查看连接/认证、队列、读取或下载阶段；可取消，修复连接、认证或路径后重试。 |

## 插件更新

打开 **Plugin > PymolPlugins: Updates**，点击 **Check now**；发现更新后点击 **Download and install**。**Check for updates when PyMOL starts** 默认开启；**Automatically install stable updates (restart required)** 默认关闭，可在同一窗口启用。安装后完全退出并重新打开 PyMOL，当前会话不会热重载。

0.17.1 起，GitHub API 匿名限流时更新器会尝试从公开 Release 页面获取稳定版本和 SHA256SUMS，无需 GitHub token；其他网络错误仍需处理。下载包继续校验 SHA256、版本和 ZIP 路径，同卷替换时保留备份，失败则恢复。若旧版更新器因 API 限流无法检查，可在 https://github.com/Molaison/pymol_scripts/releases/latest 提供 0.17.1 或更新版本后下载插件 ZIP，使用 Plugin Manager 安装并重启。首次从 0.14.x 升级仍需手动安装本 ZIP。

## 可配置审阅步骤

review.json 的 steps 列表定义命名对象和命令的执行顺序。固定对象只加载一次，同一路径连续使用保持启用；对齐、配色和显示均由命令决定。Steps 编辑器可添加、编辑及上下移动动作；候选浏览顺序在 Filter 中设置。未配置 steps 的普通数据继续使用原 step_cmd。

## 筛选分组

review.json可选filter_presets。Filter旁下拉箭头可一键切组；Filter窗口可改条件和排序，用Save…保存同名或新组，Delete删除。编辑写入review.override.json，不覆盖生成配置、不改人工标签。分组允许重叠。

## 其他入口

- `pp_review_groups candidates, review_path=scene-review.json` 直接标注已加载父组的各子group；重开前先加载原PSE，标注另存JSONL。
- 文件候选与命令产生的配体/接触线/标签默认整组切换；More > Review loaded scene groups… 提供现有组审阅入口。
- `pp_group`、`pp_group_by_name` 提供统一分组入口；interaction默认不新增partner selection；命令参数支持Tab补全。
- `pp_style_apply clean_outline` 等6个新增样式使用现有 `pp_style_revert` 撤销。
- `pp_nav groups, "candidate_*"` 浏览匹配的 group；默认 Left/Right 前后切换、Ctrl+Left/Right 翻页，`pp_nav stop` 恢复原快捷键。启动时加 `keys=0` 禁用绑定；Annotator 使用 Alt+Left/Right。

- PyMOL 控制台运行 `pp_load` 和 `pp_help` 可查看命令入口。
- **Plugin > PymolPlugins: Batch Processing**（或 `pp_batch`）用于批量结构处理。
- **Settings > Automation > Viewing presets…** 提供PPI、PLI、寡聚体、膜蛋白、DNA/RNA、cartoon和surface草稿；不需要API。
- **AI command drafts… > AI settings…** 可选择原Codex或自定义API地址/Key/模型及Responses/Chat Completions格式。Remember key使用Windows账户加密，否则Key仅保留到PyMOL退出。
- 预设和AI都先预览、编辑、确认插入，再点 **Apply commands**；生成不执行。
- 可选 PLIP/PyRosetta workers 和 ANVIL 不包含在 ZIP 中。
"""


def _version() -> str:
    source = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if match is None:
        raise RuntimeError("pymolplugins/__init__.py has no __version__")
    return match.group(1)


def _bootstrap(version: str) -> str:
    return f'''# Version: {version}
"""PymolPlugins toolkit and Structure Annotator for PyMOL."""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins import __version__  # noqa: E402,F401
from pymolplugins.host.plugin import __init_plugin__  # noqa: E402,F401
'''


def _copy_tree(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(ROOT)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def _build(output_dir: Path = DIST) -> Path:
    version = _version()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"PymolPlugins-{version}.zip"
    with tempfile.TemporaryDirectory(prefix="pymolplugins-package-") as temporary:
        staging = Path(temporary)
        (staging / "__init__.py").write_bytes(_bootstrap(version).encode("utf-8"))
        _copy_tree(PACKAGE_ROOT, staging)
        for entry in (ROOT / "plugins").glob("*.py"):
            destination_entry = staging / "plugins" / entry.name
            destination_entry.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, destination_entry)
        (staging / "INSTALL.md").write_bytes(INSTALL_TEXT.replace("{version}", version).encode("utf-8"))

        files = sorted(
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        )
        with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for name in files:
                archive.write(staging / name, f"{PLUGIN_NAME}/{name}")

    required = {
        f"{PLUGIN_NAME}/{name}"
        for name in ("__init__.py", "pymolplugins/__init__.py", "plugins/pymol_plugins.py")
    }
    with ZipFile(destination) as archive:
        names = set(archive.namelist())
    missing = sorted(required - names)
    if missing:
        raise RuntimeError("package is missing required files: " + ", ".join(missing))
    print(f"created {destination} ({destination.stat().st_size} bytes)")
    print("Install this ZIP in PyMOL: Plugin > Plugin Manager > Install New Plugin")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=DIST,
        help="directory for the installable ZIP (default: repository dist/)",
    )
    args = parser.parse_args()
    _build(args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

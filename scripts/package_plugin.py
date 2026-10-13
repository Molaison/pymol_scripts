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
INSTALL_TEXT = """# 安装 PymolPlugins（0.14.1）

需要带 Qt 图形界面的 PyMOL 和 Python 3。

## 第一次使用 Structure Annotator

1. 在 PyMOL 打开 **Plugin > Plugin Manager > Install New Plugin**。
2. 选择下载的原始 ZIP；不要解压。安装完成后重启 PyMOL。
3. 打开 **Plugin > PymolPlugins: Structure Annotator**。右上角 **?** 随时可查看帮助。无需输入 PyMOL 命令。
4. 在 **Open from** 选择 **This computer**，或选择已有的 SSH **Host** 别名。
5. 在 **Path** 粘贴真实的结构文件夹、CSV 或 TSV 路径；也可以用 **Browse…**。
6. 点 **Open**。一般不用改 **Import options**。如需 **Structure column**，
   选择包含 PDB/CIF 文件路径的列，不是分数或名称列。
7. 在 **Review** 查看结构和分数，选择标签，再点 **Save and next**。

普通点选只修改草稿，不会立即保存。快捷键、Alt+数字和 Ctrl+click 是可选的加速方式，不是开始审阅的前提。

## SSH 开始前

没有服务器可选时，点 **Set up SSH… > First SSH connection…**。
远端 Path 用 /data/review/scores.tsv 或 ~/review/structures，不是 Windows 盘符。

SSH profile 是 `~/.ssh/config` 中保存的服务器 `Host` 名字，不是密码。PyMOL 在 Windows 运行时，默认读取该电脑的 `%USERPROFILE%/.ssh/config`，不会默认读取 WSL 的 SSH 配置。

**Connections…** 可以选择已有的 SSH config 或登录 program；通常不需要修改。请先在同一台 Windows 电脑上确认 `ssh my-lab` 能连接，并准备好主机信任、密钥或 OTP。插件不创建服务器账号、不管理密码，也不会修改全局 SSH 配置。

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

## 自动参考对照

review.json 设置 reference_column 后，打开结构及切换 step 会自动加载对应参考，显示为橙色并对齐到候选。Hide reference 可隐藏当前步的参考；下一步恢复自动加载。加载失败后可点 Compare reference 重试。

## 筛选分组

review.json可选filter_presets。Filter旁下拉箭头可一键切组；Filter窗口可改条件和排序，用Save…保存同名或新组，Delete删除。编辑写入review.override.json，不覆盖生成配置、不改人工标签。分组允许重叠。

## 其他入口

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
        (staging / "__init__.py").write_text(_bootstrap(version), encoding="utf-8")
        _copy_tree(PACKAGE_ROOT, staging)
        for entry in (ROOT / "plugins").glob("*.py"):
            destination_entry = staging / "plugins" / entry.name
            destination_entry.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, destination_entry)
        (staging / "INSTALL.md").write_text(INSTALL_TEXT, encoding="utf-8")

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

"""Measure real Windows PyMOL group navigation and verify external edits.

Run with bundled Python: --stage measure --variant before|after --isolated
Before loads navigation/commands.py from baseline 127c2bf in the test process.
Results and real OpenGL captures are stored under output/16_group_navigation/.
"""

import argparse
import base64
import cProfile
import hashlib
import json
import os
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from workspace_helpers import isolate_workspace_settings

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--stage", required=True, choices=["measure", "qualify", "api", "edits", "compare"])
parser.add_argument("--variant", required=True, choices=["before", "after"])
parser.add_argument("--isolated", action="store_true")
args = parser.parse_args()
output = ROOT / "output" / "16_group_navigation"
output.mkdir(parents=True, exist_ok=True)
result_name = f"{args.variant}_edits" if args.stage == "edits" else args.variant
if args.stage == "compare":
    before = json.loads((output / "before.json").read_text(encoding="utf-8"))
    after = json.loads((output / "after.json").read_text(encoding="utf-8"))
    assert after["status"] == "passed", "Complete the optimized Host acceptance first."
    assert before["source_sha256"] != after["source_sha256"]
    assert before["pymol"] == after["pymol"]
    assert [row["groups"] for row in before["scenarios"]] == [60, 180]
    assert [row["groups"] for row in after["scenarios"]] == [60, 180]
    comparison = []
    rows, pictures = [], []
    for old, new in zip(before["scenarios"], after["scenarios"]):
        assert (old["objects"], old["atoms"]) == (new["objects"], new["atoms"])
        item = {"groups": old["groups"], "objects": old["objects"], "atoms": old["atoms"]}
        for metric, title in (("navigation", "导航调用"), ("total", "导航 + Qt 重绘")):
            a, b = old[metric]["median_ms"], new[metric]["median_ms"]
            item[metric] = {"before_ms": a, "after_ms": b, "speedup": a / b,
                            "reduction_percent": (1 - b / a) * 100}
            rows.append(
                f"<tr><td>{old['groups']} 组</td><td>{title}</td><td>{a:.1f} ms</td>"
                f"<td>{b:.1f} ms</td><td>{a / b:.1f}×</td><td>{(1-b/a)*100:.1f}%</td></tr>"
            )
        comparison.append(item)
    for label, variant in (("优化前", "before"), ("优化后", "after")):
        image = base64.b64encode((output / f"{variant}_180.png").read_bytes()).decode("ascii")
        pictures.append(
            f'<figure><figcaption>{label} · 180 组 · 同一视角</figcaption>'
            f'<img alt="{label}的真实 PyMOL 视口" src="data:image/png;base64,{image}"></figure>'
        )
    check_names = {
        "native_right_arrow": "原生右方向键切组",
        "add_cgo": "新增 CGO 成员",
        "remove_measurement": "移出接触线",
        "move_molecule_same_names": "跨组移动结构（名称集合不变）",
        "move_nested_group_same_names": "跨组移动嵌套组（名称集合不变）",
        "delete_empty_group": "清空并删除候选组",
        "repair_then_resume": "恢复分组后继续导航",
        "pymol_export_setting_preserved": "PyMOL 导出设置保持不变",
    }
    checks = []
    for name, label in check_names.items():
        value = after["checks"][name]
        assert value == "passed" or value.get("status") == "passed", name
        detail = f" · {value['detection_ms']:.1f} ms" if isinstance(value, dict) else ""
        checks.append(f"<li>通过：{label}{detail}</li>")
    html = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PyMOL 大场景切组性能对比</title>
<style>
body{font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif;color:#172c37;background:#f6f8fa;margin:0}
main{max-width:1100px;margin:32px auto;padding:28px;background:white;border-radius:12px}
h1{font-size:28px;margin:0}h2{font-size:20px;margin-top:30px}.muted{color:#546774}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:10px;border-bottom:1px solid #dce3e8}th{background:#edf4f7}
.images{display:flex;gap:16px}figure{margin:0;flex:1;min-width:0}img{width:100%;height:auto}
figcaption{font-weight:600;margin:8px 0}code{font-size:14px}li{margin:5px 0}
@media(max-width:700px){main{margin:0;padding:16px}.images{display:block}table{font-size:13px}td,th{padding:6px}}
</style><main><h1>PyMOL 大场景切组性能对比</h1>
<p class="muted">2026-11-09 · Windows PyMOL 3.1.3.1 · 独立隐藏桌面 · 真实 OpenGL 视口</p>
<p>每次切换仍读取最新分组关系；改用 PyMOL 的二进制会话载荷，减少逐原子 Python 数据转换。没有使用分组缓存，也没有修改用户导出设置。</p>
<h2>相同场景，15 次切换的中位数</h2>
<table><thead><tr><th>规模</th><th>测量边界</th><th>优化前</th><th>优化后</th><th>加速</th><th>耗时减少</th></tr></thead>
<tbody>""" + "".join(rows) + """</tbody></table>
<p class="muted">60 组：301 对象 / 57,096 原子；180 组：901 对象 / 169,416 原子。每组含真实 936 原子蛋白、嵌套组、CGO 和接触线。3 次预热，15 次无 profile 计时；另做 3 次 profile 定位热点。重绘计时不含固定等待，不等同于真人键盘到屏幕延迟；未包含远端下载或文件读取。</p>
<h2>真实画面</h2><div class="images">""" + "".join(pictures) + """</div>
<p class="muted">以上为 PyMOL 原生 OpenGL framebuffer 截图，保留本机许可证文字。两版结构、视角与可见内容应保持一致。</p>
<h2>手动改组后的行为</h2><ul>""" + "".join(checks) + """</ul>
<p>分组变化在下一次切换时返回 stale，当前游标和场景保持不变；修改恢复后可继续。检查包含未显示的候选组。</p>
<h2>证据范围</h2>
<p class="muted">优化前的完整性能测量与截图有效；其后的恢复测试因本机 group …, remove 未移出成员而终止。改用原生 ungroup 后，小场景和优化后的完整大场景检查通过。未将失败项标为通过。前后测试均串行运行，输入桌面保持 Default。此前重复运行的数据不参与比较。</p>
<p class="muted">原始数据：before.json、after.json、before_measure_desktop.json、after_measure_desktop.json；复现脚本：tests/host/16_group_navigation_perf.py。基线：127c2bf；未发布或替换日常安装。</p>
</main></html>"""
    (output / "comparison.html").write_text(html, encoding="utf-8")
    (output / "comparison.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    print(json.dumps(comparison), flush=True)
    sys.exit(0)
if args.stage == "api":
    import pymol
    from pymol import cmd
    pymol.finish_launching(["pymol", "-cqk"])
    cmd.load(str(ROOT / "data/helical_bundle.pdb"), "template")
    for index in range(180):
        cmd.create(f"model_{index}", "template")
        cmd.group(f"candidate_{index}", f"model_{index}")
    probes = {"pymol": cmd.get_version()[0], "atoms": cmd.count_atoms("all"),
              "pse_binary_dump": cmd.get("pse_binary_dump"), "variants": {}}
    expected = None
    for name, options in (("default", {}), ("binary", {"binary": 1}),
                          ("binary_no_cache", {"binary": 1, "cache": 0})):
        times = []
        for _ in range(6):
            began = time.perf_counter()
            data = cmd.get_session("", partial=1, quiet=1, **options)
            times.append((time.perf_counter() - began) * 1000)
            parents = {row[0]: row[6] for row in data["names"] if row}
            if expected is None:
                expected = parents
            assert parents == expected
        probes["variants"][name] = {"median_ms": statistics.median(times), "samples_ms": times}
    (output / "api_probe.json").write_text(json.dumps(probes, indent=2), encoding="utf-8")
    print(json.dumps(probes), flush=True)
    cmd.quit()
    sys.exit(0)
if args.isolated:
    import ctypes
    from ctypes import wintypes
    import win32event
    import win32process

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    run_mutex = kernel32.CreateMutexW(None, False, "Local\\PyMOLPluginsNavigationBenchmark")
    if not run_mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(run_mutex)
        raise SystemExit("A PyMOL navigation benchmark is already running.")
    user32.CreateDesktopW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]

    def input_desktop():
        handle = user32.OpenInputDesktop(0, False, 1)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            if not user32.GetUserObjectInformationW(
                handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return name.value
        finally:
            user32.CloseDesktop(handle)

    before_desktop = input_desktop()
    desktop_name = f"PyMOLPerf_{os.getpid()}"
    desktop = user32.CreateDesktopW(desktop_name, None, None, 0, 0x10000000, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    launch = {"desktop": desktop_name, "input_before": before_desktop,
              "stage": args.stage, "variant": args.variant}
    try:
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = "winsta0\\" + desktop_name
        command = [sys.executable, "-I", "-u", str(Path(__file__).resolve()),
                   "--stage", args.stage, "--variant", args.variant]
        process, thread, pid, _ = win32process.CreateProcess(
            sys.executable, subprocess.list2cmdline(command), None, None,
            False, 0x08000000, None, str(ROOT), startup,
        )
        launch["pid"] = pid
        try:
            timeout = 45000 if args.stage == "qualify" else 600000
            wait_result = win32event.WaitForSingleObject(process, timeout)
            if wait_result == 258:
                win32process.TerminateProcess(process, 124)
                win32event.WaitForSingleObject(process, 5000)
            launch["exit_code"] = win32process.GetExitCodeProcess(process)
            launch["timed_out"] = wait_result == 258
        finally:
            thread.Close()
            process.Close()
        launch["input_after"] = input_desktop()
        assert launch["input_after"] == before_desktop, launch
    finally:
        user32.CloseDesktop(desktop)
        kernel32.CloseHandle(run_mutex)
        (output / f"{args.variant}_{args.stage}_desktop.json").write_text(
            json.dumps(launch, indent=2), encoding="utf-8"
        )
    print(json.dumps(launch), flush=True)
    sys.exit(launch["exit_code"])
report = {"variant": args.variant, "status": "running", "scenarios": [], "checks": {}}
source = ROOT / "pymolplugins/navigation/commands.py"
baseline_source = None
if args.variant == "before" and args.stage in {"measure", "edits"}:
    baseline_source = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", "127c2bf:pymolplugins/navigation/commands.py"]
    )
report["source_sha256"] = hashlib.sha256(
    baseline_source if baseline_source is not None else source.read_bytes()
).hexdigest()
report["git_head"] = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
).strip()

from pymol import cmd, invocation, gui
from pymol.cgo import BEGIN, LINES, COLOR, VERTEX, END
from pymol.Qt import QtCore, QtWidgets
from PyQt5.QtTest import QTest
from pmg_qt.pymol_qt_gui import PyMOLQtGUI

application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
application.setQuitOnLastWindowClosed(False)
invocation.parse_args(["pymol", "-k", "-q"])
window = PyMOLQtGUI()
gui.createlegacypmgapp = window.createlegacypmgapp
window.add_lineedit()
window.resize(1120, 780)
window.move(80, 70)
window.show()
window.activateWindow()
application.processEvents()
temporary = tempfile.TemporaryDirectory(prefix="pymol-group-performance-")
isolate_workspace_settings(Path(temporary.name), window)
os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(Path(temporary.name) / "history.json")


def repaint():
    application.processEvents()
    window.pymolwidget._pymolProcess()
    window.pymolwidget.repaint()
    application.processEvents()


def capture(name):
    window.activateWindow()
    window.raise_()
    window.repaint()
    repaint()
    QTest.qWait(200)
    path = output / f"{args.variant}_{name}.png"
    assert window.pymolwidget.grabFrameBuffer().save(str(path))
    return str(path)


def scene_state():
    records = cmd.get_session("", partial=1, quiet=1)["names"]
    return {
        row[0]: {"enabled": bool(row[2]), "parent": row[6]}
        for row in records if row
    }


def summary(values):
    return {
        "median_ms": statistics.median(values),
        "max_ms": max(values),
        "samples_ms": values,
    }


try:
    if args.stage == "qualify":
        cmd.load(str(ROOT / "data/helical_bundle.pdb"), "qualification")
        cmd.hide("everything")
        cmd.show("cartoon", "qualification")
        cmd.color("marine", "qualification")
        cmd.bg_color("white")
        cmd.orient("qualification")
        cmd.zoom("qualification", buffer=4)
        frames = []
        for index in range(2):
            if index:
                cmd.turn("y", 55)
                cmd.color("orange", "qualification")
            repaint()
            QTest.qWait(200)
            image = window.pymolwidget.grabFrameBuffer()
            assert not image.isNull(), "OpenGL framebuffer is empty"
            image_path = output / f"isolated_frame_{index}.png"
            assert image.save(str(image_path))
            colors = {
                image.pixel(x, y) for x in range(0, image.width(), 3)
                for y in range(0, image.height(), 3)
            }
            assert len(colors) > 100, ("uniform framebuffer", len(colors))
            frames.append({"path": str(image_path), "sampled_colors": len(colors),
                           "width": image.width(), "height": image.height(),
                           "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()})
        assert frames[0]["sha256"] != frames[1]["sha256"], "framebuffer did not update"
        report.update(status="passed", frames=frames, pymol=cmd.get_version()[0],
                      renderer=cmd.get_renderer(), atoms=cmd.count_atoms("qualification"),
                      capture="native PyMOL QGLWidget OpenGL framebuffer",
                      user=os.environ.get("USERNAME"))
        raise SystemExit(0)
    from pymolplugins.navigation import commands as nav
    if baseline_source is not None:
        exec(compile(baseline_source, str(source), "exec"), nav.__dict__)
    from pymolplugins.host.plugin import pp_load
    assert pp_load()["status"] == "ok"
    report["pymol"] = cmd.get_version()[0]
    report["python"] = sys.version
    report["platform"] = sys.platform
    report["binary_setting_before"] = cmd.get_setting_int("pse_binary_dump")
    report["timing_boundary"] = "public pp_nav_next through synchronous Qt repaint; no fixed waits"
    cmd.set("auto_zoom", 0)
    cmd.set("suspend_updates", 1)
    cmd.bg_color("white")
    cmd.load(str(ROOT / "data/helical_bundle.pdb"), "template")
    cmd.hide("everything", "template")
    cmd.show("cartoon", "template")
    cmd.color("marine", "template")
    cmd.disable("template")
    start_view = None

    # %% Two scene sizes, identical inputs and uninstrumented timing in each version.
    for count in ((5,) if args.stage == "edits" else (60, 180)):
        report["progress"] = f"building_{count}_groups"
        (output / f"{result_name}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        indices = range(175, 180) if args.stage == "edits" else range(0 if count == 60 else 60, count)
        for index in indices:
            suffix = f"{index:03d}"
            model = "model_" + suffix
            cmd.create(model, "template")
            cmd.color(("marine", "orange", "teal")[index % 3], model)
            cmd.load_cgo(
                [BEGIN, LINES, COLOR, 1., 0.2, 0.1,
                 VERTEX, 0., 0., 0., VERTEX, 8., 8., 8., END],
                "overlay_" + suffix,
            )
            cmd.distance("contact_" + suffix, f"{model} and index 1", f"{model} and index 20")
            cmd.hide("labels", "contact_" + suffix)
            cmd.group("detail_" + suffix, "overlay_" + suffix + " contact_" + suffix)
            cmd.group("candidate_" + suffix, model + " detail_" + suffix)
            cmd.disable("candidate_" + suffix)
        entries = ",".join(
            f"candidate_{index:03d}" for index in (range(175, 180) if args.stage == "edits" else range(count))
        )
        started = time.perf_counter()
        result = nav.pp_nav_start(
            source="groups", entries=entries, zoom_policy="none", bind_keys=1
        )
        start_ms = (time.perf_counter() - started) * 1000
        assert result.get("status") != "error", result
        if start_view is None:
            cmd.zoom("model_175" if args.stage == "edits" else "model_000", buffer=4)
            start_view = cmd.get_view()
        cmd.set_view(start_view)
        cmd.set("suspend_updates", 0)
        repaint()
        for _ in range(3):
            assert nav.pp_nav_next().get("status") != "error"
            repaint()
        navigation_ms, paint_ms, total_ms = [], [], []
        for _ in range(15):
            began = time.perf_counter()
            result = nav.pp_nav_next()
            applied = time.perf_counter()
            assert result.get("status") != "error", result
            repaint()
            painted = time.perf_counter()
            navigation_ms.append((applied - began) * 1000)
            paint_ms.append((painted - applied) * 1000)
            total_ms.append((painted - began) * 1000)
        scenario = {
            "groups": count, "objects": len(cmd.get_names("objects")),
            "atoms": cmd.count_atoms("all"), "start_ms": start_ms,
            "navigation": summary(navigation_ms), "qt_repaint": summary(paint_ms),
            "total": summary(total_ms),
        }
        profile = cProfile.Profile()
        profile.enable()
        for _ in range(3):
            nav.pp_nav_next()
        profile.disable()
        stats = pstats.Stats(profile)
        scenario["profile_top"] = [
            {"file": Path(key[0]).name, "line": key[1], "function": key[2],
             "calls": value[1], "self_ms": value[2] * 1000, "cumulative_ms": value[3] * 1000}
            for key, value in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:22]
        ]
        report["scenarios"].append(scenario)
        (output / f"{result_name}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        window.setWindowTitle(
            f"PyMOL {args.variant.upper()} | {count} groups | "
            f"navigation {statistics.median(navigation_ms):.1f} ms | "
            f"with Qt repaint {statistics.median(total_ms):.1f} ms"
        )
        scenario["screenshot"] = capture(str(count))
        if count == 60:
            assert nav.pp_nav_stop().get("status") != "error"
            cmd.set("suspend_updates", 1)

    # %% Native arrow dispatch uses the same controller and real viewer.
    window.lineedit.clear()
    window.lineedit.setFocus()
    previous = nav._CONTROLLER.session.cursor
    QTest.keyClick(window.lineedit, QtCore.Qt.Key_Right)
    repaint()
    assert nav._CONTROLLER.session.cursor == (previous + 1) % nav._CONTROLLER.session.count
    report["checks"]["native_right_arrow"] = "passed"

    # %% External PyMOL commands between transitions: no cached membership assumptions.
    checks = [
        ("add_cgo", "group detail_179, manual_overlay", "ungroup manual_overlay",
         {"candidate_179": {"added": ["manual_overlay"]}}),
        ("remove_measurement", "ungroup contact_178", "group detail_178, contact_178",
         {"candidate_178": {"removed": ["contact_178"]}}),
        ("move_molecule_same_names", "group candidate_177, model_179", "group candidate_179, model_179",
         {"candidate_177": {"added": ["model_179"]}, "candidate_179": {"removed": ["model_179"]}}),
        ("move_nested_group_same_names", "group candidate_176, detail_179", "group candidate_179, detail_179",
         {"candidate_176": {"added": ["contact_179", "overlay_179"]},
          "candidate_179": {"removed": ["contact_179", "overlay_179"]}}),
        ("delete_empty_group", "group candidate_175, action=empty; delete candidate_175",
         "group candidate_175, model_175 detail_175",
         {"candidate_175": {"group_missing": True, "removed": ["contact_175", "model_175", "overlay_175"]}}),
    ]
    cmd.load_cgo([BEGIN, LINES, VERTEX, 1., 1., 1., VERTEX, 3., 3., 3., END], "manual_overlay")
    cmd.disable("manual_overlay")
    for name, change, restore, expected in checks:
        cmd.do(change)
        repaint()
        cmd.sync()
        before = scene_state()
        cursor = nav._CONTROLLER.session.cursor
        began = time.perf_counter()
        result = nav.pp_nav_next()
        elapsed = (time.perf_counter() - began) * 1000
        assert result.get("status") == "stale", (name, result)
        assert nav._CONTROLLER.session.cursor == cursor, name
        assert scene_state() == before, name
        changes = nav._CONTROLLER.membership_changes
        assert set(changes) == set(expected), (name, changes)
        for group, fields in expected.items():
            for field, value in fields.items():
                assert changes[group][field] == value, (name, changes)
        report["checks"][name] = {
            "status": "passed", "detection_ms": elapsed,
            "changes": changes, "cursor_and_scene_preserved": True,
        }
        if name == "move_molecule_same_names":
            window.setWindowTitle(f"PyMOL {args.variant.upper()} | Manual regroup detected | Cursor preserved")
            report["manual_edit_screenshot"] = capture("manual_edit")
        cmd.do(restore)
        repaint()
        cmd.sync()
        recovered = nav.pp_nav_next()
        assert recovered.get("status") not in {"error", "stale"}, (name, recovered)
    assert nav.pp_nav_stop().get("status") != "error"
    report["checks"]["repair_then_resume"] = "passed"
    report["binary_setting_after"] = cmd.get_setting_int("pse_binary_dump")
    assert report["binary_setting_before"] == report["binary_setting_after"]
    report["checks"]["pymol_export_setting_preserved"] = "passed"
    report["status"] = "passed"
except Exception:
    report["status"] = "failed"
    report["traceback"] = traceback.format_exc()
    traceback.print_exc(file=sys.__stderr__)
finally:
    report_path = output / ("isolated_qualification.json" if args.stage == "qualify" else f"{result_name}.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "variant": args.variant,
                      "output": str(report_path)}), file=sys.__stdout__, flush=True)
    cmd.quit(0 if report["status"] == "passed" else 1)

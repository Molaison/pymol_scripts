"""All PyMOL calls live on the Qt thread. Display IDs are never row indices."""
import os
import tempfile
import uuid

from pymol import cmd


class DisplayBridge:
    def __init__(self):
        self.objects = {}
        self.signatures = {}
        self.atom_maps = {}
        self.preview_object = None
        self.selection_name = "rfd4_selection_" + uuid.uuid4().hex[:8]

    @staticmethod
    def signature(obj):
        model = cmd.get_model("model " + obj, state=1)
        atoms = sorted((int(a.id), a.name, a.resn, a.resi, a.chain, a.symbol,
                        int(a.formal_charge), tuple(round(float(c), 4) for c in a.coord))
                       for a in model.atom)
        bonds = sorted((tuple(sorted(int(model.atom[i].id) for i in bond.index)),
                        int(bond.order)) for bond in model.bond)
        return atoms, bonds

    def load(self, view):
        sid = view["session_id"]
        previous = self.objects.get(sid)
        fresh = previous is None
        # A fresh native identity avoids stale same-name selection entries in
        # PyMOL 3.1 after repeated object replacement. The session label and
        # canonical atom IDs remain stable in the plugin.
        obj = cmd.get_unused_name("rfd4_" + uuid.uuid4().hex[:10])
        atom_ids = view["atom_ids"]
        if len(set(atom_ids)) != len(atom_ids):
            raise ValueError("服务返回重复原子 ID，无法安全映射选择。")
        descriptor, path = tempfile.mkstemp(suffix=".cif", prefix="rfd4-view-")
        camera = cmd.get_view()
        # Load to a temporary object first; a bad response cannot destroy input.
        staging = cmd.get_unused_name("rfd4_loading_" + uuid.uuid4().hex[:8])
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(view["cif"])
            cmd.load(path, staging, state=1, format="cif", quiet=1, zoom=0)
            actual = [int(atom.id) for atom in cmd.get_model("model " + staging, state=1).atom]
            if len(actual) != len(atom_ids) or set(actual) != set(atom_ids):
                raise ValueError("PyMOL 显示原子与服务端映射不一致，已保留原输入。")
            cmd.create(obj, "model " + staging, source_state=1, target_state=1, zoom=0, quiet=1)
            copied = [int(atom.id) for atom in cmd.get_model("model " + obj, state=1).atom]
            if copied != actual:
                cmd.delete(obj)
                raise ValueError("PyMOL 复制显示对象时改变了原子 ID，请重新打开输入。")
            if previous is not None and previous in cmd.get_names("objects"):
                cmd.delete(previous)
            cmd.hide("everything", "model " + obj)
            cmd.show("cartoon", "model " + obj)
            cmd.show("sticks", "(model %s) and not polymer" % obj)
            cmd.color("cyan", "(model %s) and elem C" % obj)
            # Undefined atoms remain selectable but are display placeholders only.
            placeholders = view.get("placeholder_ids", [])
            if placeholders:
                cmd.select_list(self.selection_name, obj, placeholders, mode="id", quiet=1)
                cmd.show("spheres", self.selection_name)
                cmd.set("sphere_scale", 0.18, self.selection_name)
                cmd.color("gray70", self.selection_name)
                cmd.disable(self.selection_name)
            if fresh:
                cmd.zoom("model " + obj, animate=0)
            else:
                cmd.set_view(camera)
        finally:
            os.unlink(path)
            if staging in cmd.get_names("objects"):
                cmd.delete(staging)
        self.objects[sid] = obj
        self.atom_maps[sid] = {value: i for i, value in enumerate(atom_ids)}
        self.signatures[sid] = self.signature(obj)
        return obj

    def assert_current(self, sid):
        obj = self.objects.get(sid)
        if not obj or obj not in cmd.get_names("objects"):
            raise ValueError("当前 PyMOL 对象已删除，请重新打开输入。")
        if self.signature(obj) != self.signatures[sid]:
            raise ValueError("PyMOL 中的原子、键或坐标已改变。请恢复服务端显示，或将该对象"
                             "作为新输入导入后重新设置条件；不能把显示改动当作已同步的注释输入。")
        return obj

    def selected_indices(self, sid, selection):
        obj = self.assert_current(sid)
        visible = self.preview_object[1] if self.preview_object and self.preview_object[0] == sid else obj
        atoms = cmd.get_model(f"({selection or 'none'}) and (%{obj} or %{visible})", state=1).atom
        mapping = self.atom_maps[sid]
        if any(int(atom.id) not in mapping for atom in atoms):
            raise ValueError("选择包含未映射原子，请刷新输入。")
        return sorted({mapping[int(atom.id)] for atom in atoms})

    def select_indices(self, sid, indices):
        obj = self.assert_current(sid)
        if self.preview_object and self.preview_object[0] == sid:
            obj = self.preview_object[1]
        reverse = {index: atom_id for atom_id, index in self.atom_maps[sid].items()}
        cmd.select_list(self.selection_name, obj, [reverse[i] for i in indices], mode="id", quiet=1)
        cmd.enable(self.selection_name)
        return self.selection_name

    def activate(self, sid):
        for key, obj in self.objects.items():
            if obj in cmd.get_names("objects"):
                (cmd.enable if key == sid else cmd.disable)(obj)

    def compare(self, result_sid, input_sid):
        mobile = self.assert_current(result_sid)
        target = self.assert_current(input_sid)
        copy = cmd.get_unused_name("rfd4_compare")
        cmd.create(copy, "model " + mobile, source_state=1, target_state=1)
        cmd.enable(target)
        try:
            result = cmd.align("(%s) and name CA" % copy, "(%s) and name CA" % target)
        except Exception:
            cmd.delete(copy)
            raise
        return copy, result

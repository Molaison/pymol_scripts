from PyQt5 import QtWidgets, QtCore
from pymol import cmd
import math
from pymol.cgo import BEGIN, COLOR, TRIANGLES, VERTEX, NORMAL, END


# 椭球体生成函数
def makeEllipsoid(x, y, z, a1, a2, a3):
    def sqEllipsoid(x, y, z, a1, a2, a3, u, v, n, e):
        x = a1 * math.cos(u) * math.cos(v) + x
        y = a2 * math.cos(u) * math.sin(v) + y
        z = a3 * math.sin(u) + z
        nx = math.cos(u) * math.cos(v) / a1
        ny = math.cos(u) * math.sin(v) / a2
        nz = math.sin(u) / a3
        return x, y, z, nx, ny, nz

    u_segs, v_segs = 10, 10
    u1, u2 = -math.pi / 2, math.pi / 2
    v1, v2 = -math.pi, math.pi
    dU = (u2 - u1) / u_segs
    dV = (v2 - v1) / v_segs

    o = [BEGIN, TRIANGLES]
    U = u1
    for Y in range(u_segs):
        V = v1
        for X in range(v_segs):
            x1, y1, z1, n1x, n1y, n1z = sqEllipsoid(x, y, z, a1, a2, a3, U, V, 1.0, 1.0)
            x2, y2, z2, n2x, n2y, n2z = sqEllipsoid(x, y, z, a1, a2, a3, U + dU, V, 1.0, 1.0)
            x3, y3, z3, n3x, n3y, n3z = sqEllipsoid(x, y, z, a1, a2, a3, U + dU, V + dV, 1.0, 1.0)
            x4, y4, z4, n4x, n4y, n4z = sqEllipsoid(x, y, z, a1, a2, a3, U, V + dV, 1.0, 1.0)

            o.extend([COLOR, 0.5, 0.5, 0.5, NORMAL, n1x, n1y, n1z, VERTEX, x1, y1, z1])
            o.extend([COLOR, 0.5, 0.5, 0.5, NORMAL, n2x, n2y, n2z, VERTEX, x2, y2, z2])
            o.extend([COLOR, 0.5, 0.5, 0.5, NORMAL, n4x, n4y, n4z, VERTEX, x4, y4, z4])
            o.extend([COLOR, 0.5, 0.5, 0.5, NORMAL, n2x, n2y, n2z, VERTEX, x2, y2, z2])
            o.extend([COLOR, 0.5, 0.5, 0.5, NORMAL, n3x, n3y, n3z, VERTEX, x3, y3, z3])
            o.extend([COLOR, 0.5, 0.5, 0.5, NORMAL, n4x, n4y, n4z, VERTEX, x4, y4, z4])

            V += dV
        U += dU
    o.append(END)
    return o

# PyQt 界面类
class EllipsoidWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        layout = QtWidgets.QVBoxLayout(self)

        # 滑块和标签
        self.sliders = {}
        for label in ['x', 'y', 'z', 'rx', 'ry', 'rz']:
            hlayout = QtWidgets.QHBoxLayout()
            lbl = QtWidgets.QLabel(f"{label}:")
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(-100, 100)
            slider.setValue(10)
            slider.valueChanged.connect(self.updateEllipsoid)
            hlayout.addWidget(lbl)
            hlayout.addWidget(slider)
            layout.addLayout(hlayout)
            self.sliders[label] = slider

        # 按钮布局
        button_layout = QtWidgets.QHBoxLayout()
        
        # 现有的居中按钮
        self.centerButton = QtWidgets.QPushButton("Set Center to Selection")
        self.centerButton.clicked.connect(self.setCenterToSelection)
        button_layout.addWidget(self.centerButton)
        
        # 添加新的范围设置按钮
        self.rangeButton = QtWidgets.QPushButton("Set Range to Models")
        self.rangeButton.clicked.connect(self.setRangeToModels)
        button_layout.addWidget(self.rangeButton)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
        self.setWindowTitle("Ellipsoid Generator")

    def setRangeToModels(self):
        """设置滑块范围为所有模型的坐标范围"""
        try:
            # 初始化坐标范围
            min_x, min_y, min_z = float('inf'), float('inf'), float('inf')
            max_x, max_y, max_z = float('-inf'), float('-inf'), float('-inf')
            
            # 获取所有模型的列表
            model_list = cmd.get_names("selections")
            
            if not model_list:
                QtWidgets.QMessageBox.warning(self, "Warning", "No models loaded in PyMOL!")
                return
                
            # 遍历所有模型获取坐标范围
            for model_name in model_list:
                model = cmd.get_model(model_name)
                for atom in model.atom:
                    x, y, z = atom.coord
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y)
                    min_z = min(min_z, z)
                    max_z = max(max_z, z)
            
            # 在每个方向上增加20单位的间隔
            min_x, max_x = min_x - 20, max_x + 20
            min_y, max_y = min_y - 20, max_y + 20
            min_z, max_z = min_z - 20, max_z + 20
            
            # 更新xyz滑块的范围
            self.sliders['x'].setRange(int(min_x * 10), int(max_x * 10))
            self.sliders['y'].setRange(int(min_y * 10), int(max_y * 10))
            self.sliders['z'].setRange(int(min_z * 10), int(max_z * 10))
            
            # 将滑块设置到范围中间
            self.sliders['x'].setValue(int((min_x + max_x) * 5))
            self.sliders['y'].setValue(int((min_y + max_y) * 5))
            self.sliders['z'].setValue(int((min_z + max_z) * 5))
            
            QtWidgets.QMessageBox.information(self, "Success", 
                "Coordinate ranges updated based on loaded models!")
                
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to set ranges: {str(e)}")

    def updateEllipsoid(self):
        # 保存当前视图状态
        current_view = cmd.get_view()

        # 获取滑块值
        x = self.sliders['x'].value() / 10.0
        y = self.sliders['y'].value() / 10.0
        z = self.sliders['z'].value() / 10.0
        rx = self.sliders['rx'].value() / 10.0
        ry = self.sliders['ry'].value() / 10.0
        rz = self.sliders['rz'].value() / 10.0

        # 生成并加载椭球体
        obj = makeEllipsoid(x, y, z, rx, ry, rz)
        cmd.delete("ellipsoid")
        cmd.load_cgo(obj, "ellipsoid")

        # 恢复视图状态
        cmd.set_view(current_view)

    def setCenterToSelection(self):
        # 获取当前选择的原子重心
        model = cmd.get_model("sele")
        if not model.atom:
            QtWidgets.QMessageBox.warning(self, "Warning", "No atoms selected!")
            return

        x = sum(atom.coord[0] for atom in model.atom) / len(model.atom)
        y = sum(atom.coord[1] for atom in model.atom) / len(model.atom)
        z = sum(atom.coord[2] for atom in model.atom) / len(model.atom)

        # 更新滑块值
        self.sliders['x'].setValue(int(x * 10))
        self.sliders['y'].setValue(int(y * 10))
        self.sliders['z'].setValue(int(z * 10))

# 在 PyMOL 中运行
widget = EllipsoidWidget()
widget.show()
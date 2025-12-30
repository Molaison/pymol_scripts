from pymol.cgo import *
from pymol import cmd, cgo
import numpy as np
model = cmd.get_model(cmd.get_object_list()[0])
coords = np.array([atom.coord for atom in model.atom if atom.name == "CA"])

print("Calculating the principal axis of the protein structure...")

# C_x = np.cov(coords.T)
# eig_vals, eig_vecs = np.linalg.eigh(C_x)
# # variance = np.max(eig_vals)
# max_eig_val_index = np.argmax(eig_vals)
# principal_axis = eig_vecs[:, max_eig_val_index].copy()

# # 计算对称轴的两个端点，以在PyMOL中绘制
# center = coords.mean(axis=0)
# length = 50  # 对称轴的长度，可以根据需要调整
# start_point = center - principal_axis * length / 2
# end_point = center + principal_axis * length / 2

# # 在PyMOL中绘制对称轴
# axis_cgo = [
#     cgo.CYLINDER,
#     start_point[0],
#     start_point[1],
#     start_point[2],
#     end_point[0],
#     end_point[1],
#     end_point[2],
#     0.2,
#     1.0,
#     0.0,
#     0.0,
#     1.0,
#     0.0,
#     0.0,
# ]

# # 加载并显示CGO对象
# cmd.load_cgo(axis_cgo, "symmetry_axis")
sym_axis = [    CYLINDER,    0.0,    0.0,    0.0,-92.93735062853098,32.98019958980255,16.581775935233235,     0.1,    1.0,    0.0,    0.0,    1.0,    0.0,    0.0,]

# 添加 Z 轴到 PyMOL 对象中
# cmd.load_cgo(sym_axis, "sym_axis")
x_axis = [
    CYLINDER,
    0.0,
    0.0,
    0.0,  # 起点 (0, 0, 0)
    100.0,
    0.0,
    0.0,  # 终点 (0, 0, 30)
    0.1,
    1.0,
    0.0,
    0.0,  # 半径, 颜色 (红色)
    1.0,
    0.0,
    0.0,
]
y_axis = [
    CYLINDER,
    0.0,
    0.0,
    0.0,  # 起点 (0, 0, 0)
    0.0,
    100.0,
    0.0,  # 终点 (0, 0, 30)
    0.1,
    1.0,
    0.0,
    0.0,  # 半径, 颜色 (红色)
    1.0,
    0.0,
    0.0,
]
z_axis = [
    CYLINDER,
    0.0,
    0.0,
    0.0,  # 起点 (0, 0, 0)
    0.0,
    0.0,  # 终点 (0, 0, 30)
    100.0,
    0.1,
    1.0,
    0.0,
    0.0,  # 半径, 颜色 (红色)
    0.0,
    1.0,
    0.0,
]

# 添加 Z 轴到 PyMOL 对象中
cmd.load_cgo(z_axis, "z_axis")
cmd.load_cgo(x_axis, "x_axis")
cmd.load_cgo(y_axis, "y_axis")
cmd.set("cgo_line_radius", 1)

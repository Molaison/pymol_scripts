from pymol import cmd

def centroid_pseudoatom(selection="(sele)", name="centroid"):
    model = cmd.get_model(selection)
    n = len(model.atom)
    if n == 0:
        print("No atoms in selection.")
        return

    # 计算平均坐标（质心）
    x, y, z = 0.0, 0.0, 0.0
    for atom in model.atom:
        x += atom.coord[0]
        y += atom.coord[1]
        z += atom.coord[2]
    x /= n
    y /= n
    z /= n

    # 创建一个伪原子在质心位置
    cmd.pseudoatom(object=name, pos=[x, y, z], color="yellow")
    cmd.show("spheres", name)
    print(f"{name} placed at: ({x:.2f}, {y:.2f}, {z:.2f})")

# 注册到 PyMOL 命令中
cmd.extend("centroid_pseudoatom", centroid_pseudoatom)

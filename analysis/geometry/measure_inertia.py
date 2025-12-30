import numpy as np
from pymol.cgo import *
from pymol import cmd


def moments_of_inertia(vw):

    from numpy import zeros, float64, array, dot, outer, argsort, linalg, identity

    i = zeros((3, 3), float64)
    c = zeros((3,), float64)
    w = 0
    for xyz, weights in vw:
        xyz, weights = array(xyz), array(weights)
        n = len(xyz)
        if n > 0:
            wxyz = weights.reshape((n, 1)) * xyz
            w += weights.sum()
            i += (xyz * wxyz).sum() * identity(3) - dot(xyz.transpose(), wxyz)
            c += wxyz.sum(axis=0)

    if w == 0:
        return None, None, None  # All weights are zero.

    i /= w
    c /= w  # Center of vertices
    i -= dot(c, c) * identity(3) - outer(c, c)

    eval, evect = linalg.eigh(i)

    # Sort by eigenvalue size.
    order = argsort(eval)
    seval = eval[order]
    sevect = evect[:, order]

    axes = sevect.transpose()
    from chimerax.geometry import inner_product, cross_product

    if inner_product(cross_product(axes[0], axes[1]), axes[2]) < 0:
        axes[2, :] = -axes[2, :]  # Make axes a right handed coordinate system

    # Make rows of 3 by 3 matrix the principle axes.
    return axes, seval, c



def compute_inertia_matrix(positions, masses):
    center_of_mass = np.sum(positions * masses[:, None], axis=0) / np.sum(masses)
    positions_centered = positions - center_of_mass
    I = np.zeros((3, 3))

    for i in range(3):
        I[i, i] = np.sum(masses * (np.sum(positions_centered**2, axis=1) - positions_centered[:, i] ** 2))
    for i in range(3):
        for j in range(i + 1, 3):
            I[i, j] = I[j, i] = -np.sum(masses * positions_centered[:, i] * positions_centered[:, j])

    return I, center_of_mass


def compute_chimerax_inertia_matrix(positions):
    """
    按照 ChimeraX 的方法计算惯性矩阵（即坐标协方差矩阵）

    参数：
    positions: (N, 3) ndarray，N 个原子的坐标

    返回：
    eigenvalues: (3,) ndarray，特征值（对应主轴长度）
    eigenvectors: (3,3) ndarray，特征向量（主轴方向）
    center_of_mass: (3,) ndarray，质心坐标
    """
    # 计算质心
    center_of_mass = np.mean(positions, axis=0)

    # 去质心
    positions_centered = positions - center_of_mass

    # 计算坐标协方差矩阵
    covariance_matrix = np.cov(positions_centered, rowvar=False)

    # 计算特征值和特征向量
    eigenvalues, eigenvectors = np.linalg.eigh(covariance_matrix)

    return eigenvalues, eigenvectors, center_of_mass

def calculate_inertia(selection="all"):
    """
    计算 PyMOL 中选定原子的惯性矩阵和主惯性轴。
    仅考虑 CA 原子。

    参数：
    selection: PyMOL 选择字符串，默认为 "all"
    """
    # 获取选定原子中的 CA 原子的坐标和质量
    selection_ca = f"({selection}) and name CA"
    model = cmd.get_model(selection_ca)
    positions = np.array([atom.coord for atom in model.atom])
    masses = np.ones(len(positions))

    if len(positions) == 0:
        print("未选择任何 CA 原子！")
        return

    # 计算惯性矩阵
    inertia_matrix = compute_inertia_matrix(positions, masses)
    print("惯性矩阵 I：\n", inertia_matrix)

    # 计算主惯性轴和惯性矩
    eigenvalues, eigenvectors = np.linalg.eigh(inertia_matrix)
    print("主惯性矩 (特征值)：", eigenvalues)
    print("主惯性轴 (特征向量)：\n", eigenvectors)

def extract_surface_vertices(selection="all", msms_exec="msms"):
    """
    使用 MSMS 提取 PyMOL 分子的表面顶点坐标。

    参数：
    selection: PyMOL 选择字符串
    msms_exec: MSMS 可执行文件路径（默认假定 "msms" 在系统 PATH 中）

    返回：
    surface_vertices: (N,3) ndarray, 表面顶点坐标
    faces: (M,3) ndarray, 三角形面片索引
    """
    # 1. 导出 PyMOL 选中的 PDB 文件
    pdb_filename = "temp.pdb"
    xyzr_filename = "temp.xyzr"
    vert_filename = "temp.vert"
    face_filename = "temp.face"

    cmd.save(pdb_filename, selection)

    # 2. 生成 .xyzr（原子坐标 + 半径）
    with open(pdb_filename, "r") as pdb_file, open(xyzr_filename, "w") as xyzr_file:
        for line in pdb_file:
            if line.startswith("ATOM") or line.startswith("HETATM"):
                x = float(line[30:38])
                y = float(line[38:46])
                z = float(line[46:54])
                xyzr_file.write(f"{x:.3f} {y:.3f} {z:.3f} 1.5\n")  # 假设所有原子半径 1.5 Å

    # 3. 运行 MSMS 计算分子表面
    cmd_str = f"{msms_exec} -if {xyzr_filename} -of temp -probe 1.5"
    subprocess.run(cmd_str, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 4. 读取 .vert 文件（表面顶点）
    surface_vertices = []
    try:
        with open(vert_filename, "r") as vert_file:
            for line in vert_file:
                if line.startswith("#"):  # 跳过注释行
                    continue
                tokens = line.split()
                if len(tokens) >= 3:
                    surface_vertices.append([float(tokens[0]), float(tokens[1]), float(tokens[2])])
    except FileNotFoundError:
        print("MSMS 计算失败，请检查安装和 PDB 文件格式。")
        return None, None

    # 5. 读取 .face 文件（三角形面）
    faces = []
    try:
        with open(face_filename, "r") as face_file:
            for line in face_file:
                if line.startswith("#"):  # 跳过注释行
                    continue
                tokens = line.split()
                if len(tokens) >= 3:
                    faces.append([int(tokens[0]) - 1, int(tokens[1]) - 1, int(tokens[2]) - 1])  # MSMS 索引从 1 开始
    except FileNotFoundError:
        print("未找到 .face 文件，可能是 MSMS 运行出错。")
        return None, None

    # 6. 清理临时文件
    os.remove(pdb_filename)
    os.remove(xyzr_filename)
    os.remove(vert_filename)
    os.remove(face_filename)

    return np.array(surface_vertices), np.array(faces)



def compute_surface_inertia_matrix(selection="all"):
    """
    计算表面网格顶点的惯性矩矩阵（使用表面三角化方法）
    
    参数：
    selection: PyMOL 选择字符串
    
    返回：
    inertia_matrix: (3,3) ndarray, 惯性矩
    eigenvalues: (3,) ndarray, 惯性主轴特征值
    eigenvectors: (3,3) ndarray, 惯性主轴方向
    center_of_mass: (3,) ndarray, 质心坐标
    """
    surface_vertices = extract_surface_vertices(selection)
    if surface_vertices is None:
        return None
    
    # 计算质心
    center_of_mass = np.mean(surface_vertices, axis=0)
    
    # 计算相对质心的坐标
    positions_centered = surface_vertices - center_of_mass

    # 计算惯性矩
    I = np.zeros((3, 3))
    for i in range(3):
        for j in range(3):
            if i == j:
                I[i, j] = np.sum(positions_centered[:, 0]**2 + 
                                 positions_centered[:, 1]**2 +
                                 positions_centered[:, 2]**2 - 
                                 positions_centered[:, i]**2)
            else:
                I[i, j] = -np.sum(positions_centered[:, i] * positions_centered[:, j])

    # 计算特征值和特征向量
    eigenvalues, eigenvectors = np.linalg.eigh(I)

    return I, eigenvalues, eigenvectors, center_of_mass




# 在 PyMOL 中添加命令
cmd.extend("calculate_inertia", calculate_inertia)


def compute_normal(p1, p2, p3):
    normal = np.cross(p2 - p1, p3 - p1)
    normal /= np.linalg.norm(normal)  # 归一化
    return normal

def draw_inertia_ellipsoid(selection="all", scale=1.0):
    model = cmd.get_model(selection + " and name CA")
    positions = np.array([atom.coord for atom in model.atom])
    masses = np.ones(len(positions))

    if len(positions) == 0:
        print("未选择任何原子！")
        return

    eigenvalues, eigenvectors, center_of_mass = compute_chimerax_inertia_matrix(positions)

    ellipsoid_name = "inertia_ellipsoid"
    cmd.delete(ellipsoid_name)

    radii = scale * np.sqrt(np.abs(eigenvalues)) * 10.0  
    transform = eigenvectors @ np.diag(radii)

    ellipsoid_obj = [BEGIN, TRIANGLES, COLOR, 1.0, 0.5, 0.0]

    u = np.linspace(0, 2 * np.pi, 20)
    v = np.linspace(0, np.pi, 20)
    x = np.outer(np.cos(u), np.sin(v))
    y = np.outer(np.sin(u), np.sin(v))
    z = np.outer(np.ones_like(u), np.cos(v))

    points = np.stack([x.flatten(), y.flatten(), z.flatten()], axis=1)
    points = (transform @ points.T).T + center_of_mass

    for i in range(len(u) - 1):
        for j in range(len(v) - 1):
            p1 = points[i * len(v) + j]
            p2 = points[i * len(v) + (j + 1)]
            p3 = points[(i + 1) * len(v) + j]
            p4 = points[(i + 1) * len(v) + (j + 1)]

            n1 = compute_normal(p1, p2, p3)
            n2 = compute_normal(p2, p4, p3)

            ellipsoid_obj.extend([NORMAL, *n1, VERTEX, *p1, VERTEX, *p2, VERTEX, *p3])
            ellipsoid_obj.extend([NORMAL, *n2, VERTEX, *p2, VERTEX, *p4, VERTEX, *p3])

    ellipsoid_obj.append(END)
    cmd.load_cgo(ellipsoid_obj, ellipsoid_name)

def draw_surface_inertia_ellipsoid(selection="all", scale=1.0):
    """
    在 PyMOL 中基于表面顶点惯性矩绘制椭球。

    参数：
    selection: PyMOL 选择字符串
    scale: 缩放因子
    """
    # 计算表面惯性矩
    result = compute_surface_inertia_matrix(selection)
    if result is None:
        return
    eigenvalues, eigenvectors, center_of_mass = result

    # 计算椭球半轴
    radii = scale * np.sqrt(np.abs(eigenvalues)) * 10.0  # 调整大小

    # 绘制椭球轴
    ellipsoid_obj = [BEGIN, LINES, COLOR, 1.0, 0.5, 0.0]  # 颜色橙色
    for i in range(3):
        axis = eigenvectors[:, i]
        start = center_of_mass - radii[i] * axis
        end = center_of_mass + radii[i] * axis
        ellipsoid_obj.extend([VERTEX, *start, VERTEX, *end])

    ellipsoid_obj.append(END)
    cmd.load_cgo(ellipsoid_obj, "surface_inertia_ellipsoid")


# 在 PyMOL 中添加命令
cmd.extend("draw_inertia_ellipsoid", draw_inertia_ellipsoid)
cmd.extend("draw_surface_inertia_ellipsoid", draw_surface_inertia_ellipsoid)

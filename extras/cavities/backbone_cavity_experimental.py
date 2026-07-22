import numpy as np
import alphashape
import trimesh
from Bio.PDB import PDBParser
from scipy.spatial import KDTree, ConvexHull, Delaunay

def generate_pymol_visualization(pdb_file, output_pml="view_cavity.pml", grid_spacing=1.2):
    """
    1. 读取 PDB (仅骨架)
    2. 计算包络面内部的空隙
    3. 输出 .pml 脚本用于 PyMOL 可视化
    """

    # --- 参数设置 ---
    # 骨架原子半径 (C-alpha): 定义骨架自身占据的空间
    backbone_radius = 3.5
    # 核心定义距离: 离骨架多远才算“深层空腔”
    # 如果螺旋很松散，这个值大一点(如4.0-5.0)，只显示最中间的大洞
    core_void_threshold = 4.0

    # --- 1. 读取结构 ---
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure('protein', pdb_file)

    ca_atoms = []
    ca_coords = []

    # 提取 C-alpha 信息
    for atom in structure.get_atoms():
        if atom.name == 'CA':
            ca_atoms.append(atom)
            ca_coords.append(atom.get_coord())

    ca_coords = np.array(ca_coords)
    print(f"Loaded {len(ca_coords)} CA atoms.")

    # --- 2. 构建几何包络 (Hull) ---
    # 针对松散螺旋，直接使用 Convex Hull (凸包) 是最稳健的
    # 它会像保鲜膜一样包裹整个螺旋束
    print("Calculating Convex Hull (Envelope)...")
    hull = ConvexHull(ca_coords)
    hull_delaunay = Delaunay(ca_coords) # 用于判断点是否在凸包内

    # --- 3. 网格扫描 ---
    min_box = np.min(ca_coords, axis=0)
    max_box = np.max(ca_coords, axis=0)

    x = np.arange(min_box[0], max_box[0], grid_spacing)
    y = np.arange(min_box[1], max_box[1], grid_spacing)
    z = np.arange(min_box[2], max_box[2], grid_spacing)
    grid = np.array(np.meshgrid(x, y, z)).T.reshape(-1, 3)

    # 筛选1: 在凸包内部的点 (find_simplex >= 0 表示在单纯形内)
    inside_mask = hull_delaunay.find_simplex(grid) >= 0
    internal_points = grid[inside_mask]

    # 筛选2: 距离最近的 C-alpha 足够远 (是空隙，不是骨架本身)
    tree = KDTree(ca_coords)
    dists, _ = tree.query(internal_points)

    # 获取空腔点 (Void Points)
    void_points = internal_points[dists > backbone_radius]

    print(f"Found {len(void_points)} void points representing the cavity.")

    # --- 4. 识别周围残基 ---
    # 找到距离空腔点一定范围内 (例如 6.0A) 的 C-alpha
    if len(void_points) > 0:
        void_tree = KDTree(void_points)
        nearby_indices = tree.query_ball_tree(void_tree, r=6.0)

        # 使用 Set 去重
        selected_residues = set()
        for i, idx_list in enumerate(nearby_indices):
            if idx_list:
                atom = ca_atoms[i]
                chain_id = atom.get_parent().get_parent().id
                res_id = atom.get_parent().id[1] # 残基编号
                selected_residues.add((chain_id, str(res_id)))
    else:
        selected_residues = set()

    # --- 5. 编写 PyMOL (.pml) 脚本 ---
    print(f"Writing PyMOL script to {output_pml}...")

    with open(output_pml, 'w') as f:
        # 加载 PDB
        f.write(f"load {pdb_file}, structure\n")
        f.write("hide everything\n")
        f.write("show cartoon, structure\n")
        f.write("color gray80, structure\n")
        f.write("set cartoon_transparency, 0.3\n\n")

        # 绘制空腔点 (作为伪原子)
        if len(void_points) > 0:
            f.write("python\n")
            f.write("from pymol import cmd\n")
            f.write("void_coords = [\n")
            # 写入坐标数据
            for p in void_points:
                f.write(f"  [{p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}],\n")
            f.write("]\n")
            f.write("for i, (x,y,z) in enumerate(void_coords):\n")
            f.write("    cmd.pseudoatom('cavity_void', pos=[x,y,z], vdw=0.5)\n")
            f.write("python end\n\n")

            # 设置空腔样式
            f.write("show spheres, cavity_void\n")
            f.write("color cyan, cavity_void\n")
            f.write("set sphere_scale, 0.4, cavity_void\n") # 小球大小
            f.write("set sphere_transparency, 0.2, cavity_void\n")

        # 选择并高亮周围残基
        if selected_residues:
            # 构建选择字符串: (chain A and resi 10) or (chain B and resi 20) ...
            select_parts = []
            for chain, resi in selected_residues:
                select_parts.append(f"(chain {chain} and resi {resi})")

            select_cmd = "select core_residues, " + " or ".join(select_parts)
            f.write(f"{select_cmd}\n")
            f.write("show sticks, core_residues\n")
            f.write("color tv_orange, core_residues\n")
            f.write("util.cnc core_residues\n") # 碳原子橙色，其他按元素着色

        f.write("zoom structure\n")
        f.write("deselect\n")
        print("Done! Run the .pml file in PyMOL.")

# --- 执行 ---
# 将 'test.pdb' 替换为您的文件名
input_pdb = "data/helical_bundle.pdb"

# 如果没有这个文件，请生成一个假的或者报错
import os
if os.path.exists(input_pdb):
    generate_pymol_visualization(input_pdb)
else:
    print(f"Error: {input_pdb} not found. Please edit the script to point to your PDB file.")
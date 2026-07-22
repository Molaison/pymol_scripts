# 在 PyMOL 的 Python 控制台或脚本中运行
from pymol import cmd
from pymol.cgo import BEGIN, TRIANGLES, NORMAL, VERTEX, END, COLOR
import numpy as np

def draw_ellipsoid(name='ellipsoid', center=(0,0,0), radii=(5,3,2),
                   rot_euler=(0,0,0), color=(1.0,0.5,0.2),
                   u_steps=48, v_steps=24):
    """
    name: PyMOL 对象名
    center: (x,y,z)
    radii: (a,b,c) 半轴长度
    rot_euler: (alpha,beta,gamma) 以弧度的 Z-X-Z (或你喜欢的) 欧拉角，用于旋转椭球（这里使用 Z-Y-X 顺序）
    color: RGB 0..1
    u_steps: 经度分段（越大越平滑）
    v_steps: 纬度分段（越大越平滑）
    """
    cx,cy,cz = center
    a,b,c = radii
    # 生成参数网格
    u = np.linspace(0, 2*np.pi, u_steps, endpoint=False)
    v = np.linspace(-0.5*np.pi, 0.5*np.pi, v_steps)
    uu, vv = np.meshgrid(u, v)
    # 参数化曲面（未旋转）
    x = a * np.cos(vv) * np.cos(uu)
    y = b * np.cos(vv) * np.sin(uu)
    z = c * np.sin(vv)
    # 旋转矩阵（ZYX）
    alpha, beta, gamma = rot_euler
    Rx = np.array([[1,0,0],[0,np.cos(alpha),-np.sin(alpha)],[0,np.sin(alpha),np.cos(alpha)]])
    Ry = np.array([[np.cos(beta),0,np.sin(beta)],[0,1,0],[-np.sin(beta),0,np.cos(beta)]])
    Rz = np.array([[np.cos(gamma),-np.sin(gamma),0],[np.sin(gamma),np.cos(gamma),0],[0,0,1]])
    R = Rz.dot(Ry).dot(Rx)
    # 将网格点展平并旋转 + 平移
    pts = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1)
    pts = (R.dot(pts.T)).T + np.array([cx,cy,cz])
    # 计算法向量（用于光照）：在未旋转的情况下，法向量 = (x/a^2, y/b^2, z/c^2) 然后旋转并标准化
    nx = (np.cos(vv) * np.cos(uu)) / (a if a!=0 else 1)
    ny = (np.cos(vv) * np.sin(uu)) / (b if b!=0 else 1)
    nz = (np.sin(vv)) / (c if c!=0 else 1)
    normals = np.stack([nx.ravel(), ny.ravel(), nz.ravel()], axis=1)
    normals = (R.dot(normals.T)).T
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    norms[norms==0] = 1.0
    normals = normals / norms

    # 构造 CGO 三角形（按经纬网格拆三角形）
    cgo = [COLOR, float(color[0]), float(color[1]), float(color[2]),
           BEGIN, TRIANGLES]
    def idx(i,j):
        return i * u_steps + j
    for i in range(v_steps-1):
        for j in range(u_steps):
            j2 = (j+1) % u_steps
            # 两个三角形组成一个网格四边形
            for tri in [(i,j, i+1,j), (i+1,j, i+1,j2)]:
                # get indices for tri vertices
                if tri == (i,j,i+1,j):
                    v_idx = [ idx(i,j), idx(i+1,j), idx(i+1,j2) ]
                else:
                    v_idx = [ idx(i,j), idx(i+1,j2), idx(i,j2) ]

                # first vertex
                for vi in v_idx:
                    nx_v, ny_v, nz_v = normals[vi]
                    x_v, y_v, z_v = pts[vi]
                    cgo += [NORMAL, float(nx_v), float(ny_v), float(nz_v),
                            VERTEX, float(x_v), float(y_v), float(z_v)]
    cgo += [END]
    # 加载到 PyMOL
    cmd.load_cgo(cgo, name)
    cmd.show("surface", name)  # 可选：同时显示 surface 风格

cmd.extend("draw_ellipsoid", draw_ellipsoid)
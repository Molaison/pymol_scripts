import numpy as np
from sklearn.decomposition import PCA
import pymol
from pymol import cmd, cgo

# 初始化PyMOL
# pymol.finish_launching()
# 加载蛋白质结构
cmd.load('E:\\TemporaryFiles\\filter_outputs\\step_AAAAAA+_00056_acptd_single_chain_iter_217793_11_20240709_102721_637488_models.pdb')

# 获取所有原子的坐标
model = cmd.get_model('step_AAAAAA+_00056_acptd_single_chain_iter_217793_11_20240709_102721_637488_models')
coords = np.array([atom.coord for atom in model.atom if atom.name == 'CA'])

# 使用PCA计算主要对称轴
pca = PCA(n_components=3)
pca.fit(coords.T)
# principal_axis = pca.components_[0]  # 第一个主成分即为主要对称轴

C_x = np.cov(coords.T) 
eig_vals, eig_vecs = np.linalg.eigh(C_x)
#variance = np.max(eig_vals)
max_eig_val_index = np.argmax(eig_vals)
principal_axis = eig_vecs[:, max_eig_val_index].copy()

# 计算对称轴的两个端点，以在PyMOL中绘制
center = coords.mean(axis=0)
length = 50  # 对称轴的长度，可以根据需要调整
start_point = center - principal_axis * length / 2
end_point = center + principal_axis * length / 2

# 在PyMOL中绘制对称轴
axis_cgo = [
    cgo.CYLINDER, start_point[0], start_point[1], start_point[2],
                 end_point[0], end_point[1], end_point[2],
                 0.2, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
]

# 加载并显示CGO对象
cmd.load_cgo(axis_cgo, 'symmetry_axis')


# 保存PyMOL会话文件
cmd.save('protein_with_symmetry_axis_3.pse')

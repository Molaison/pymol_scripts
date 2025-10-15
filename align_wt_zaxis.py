import numpy as np
from sklearn.decomposition import PCA
from scipy.spatial.transform import Rotation as R
from pymol import cmd

# 初始化PyMOL
print("Initializing PyMOL...")
# 加载蛋白质结构
cmd.load('E:\\TemporaryFiles\\Oligomer_Hallucination_Results\\single_chain_iter_219680_1_20240714_103843_065125_models\\step_AAAAAA+_00160_acptd.pdb')

print("Rotating the protein structure to align with the z-axis...")
# 获取所有原子的坐标
model = cmd.get_model('step_AAAAAA+_00160_acptd')
coords = np.array([atom.coord for atom in model.atom if atom.name == 'CA'])

# 关闭PyMOL以释放资源

print("Calculating the principal axis of the protein structure...")
# 使用PCA计算主要对称轴
pca = PCA(n_components=3)
pca.fit(coords)
principal_axis = pca.components_[0]  # 第一个主成分即为主要对称轴

C_x = np.cov(coords.T) 
eig_vals, eig_vecs = np.linalg.eigh(C_x)
#variance = np.max(eig_vals)
max_eig_val_index = np.argmax(eig_vals)
principal_axis = eig_vecs[:, max_eig_val_index].copy()


print("Calculating the rotation matrix to align the principal axis with the z-axis...")
# 计算旋转矩阵，使主要对称轴与z轴对齐
z_axis = np.array([0, 0, 1])
rot_vec = np.cross(principal_axis, z_axis)
rot_angle = np.arccos(np.dot(principal_axis, z_axis) / np.linalg.norm(principal_axis))
rotation = R.from_rotvec(rot_angle * rot_vec / np.linalg.norm(rot_vec))
rot_matrix = rotation.as_matrix()
rot_matrix_4x4 = np.eye(4)
rot_matrix_4x4[:3, :3] = rot_matrix

print("Applying the rotation matrix...")
# 应用旋转矩阵
cmd.transform_object('step_AAAAAA+_00160_acptd', rot_matrix_4x4.flatten().tolist())

print("Save the rotated structure to 'rotated_protein.pdb'...")
# 保存旋转后的结构
cmd.save('rotated_protein_1.pdb', 'step_AAAAAA+_00160_acptd')


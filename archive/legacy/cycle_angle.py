from math import e
import os
import numpy as np
from pymol import cmd
from pymol import util
import argparse
import pymol

arg = argparse.ArgumentParser()
arg.add_argument("--flag", type=bool, default=False)
import pandas as pd

pdb_path = "E:\\TemporaryFiles\\beautiful_design\\feasible_design\\initial\\"
pdb_path = "E:\\TemporaryFiles\\beautiful_design\\feasible_design\\final_design\\"
cmd.do("run C:\\Users\\Molaison\\AppData\Roaming\\pymol\\startup\\center_of_mass.py")

design_names = [f"HALF_{i}" for i in range(1, 9)]
design_names = [f"HAL_{i}" for i in range(1, 11)]
flag = False
for design_name in design_names:
    load_path = pdb_path + design_name + ".pdb"
    cmd.load(load_path)
    original_name = cmd.get_names()[0]
    if flag:
        cmd.set_name(original_name, "Hexamer")
        cmd.extract("Pentamer", "chain A,B,C,D,E")
        cmd.align("Pentamer", "Hexamer")
        cmd.split_chains()
        cmd.do("com *")
        cmd.do("com *A")
        cmd.do("com *B")
        cmd.do("com *F")
        cmd.do("com *G")
        cmd.do("com Pentamer")
        cmd.do("com Hexamer")
        cmd.do("create Penta_merged, *_A|*_B|A_*|B_*|Pentamer_COM")
        cmd.save(f"{pdb_path}{original_name}\\{original_name}_Penta_merged.pdb","Penta_merged")
        cmd.do("create Hexa_merged, *_F|*_G|F_*|G_*|Hexamer_COM")
        cmd.do("alter name Hexa_merged and chain F, chain='A'")
        cmd.do("alter name Hexa_merged and chain G, chain='B'")
        cmd.save(f"{pdb_path}{original_name}\\{original_name}_Hexa_merged.pdb","Hexa_merged")
    else:
        cmd.set_name(original_name, "Pentamer")
        cmd.extract("Hexamer", "chain A,B,C,D,E,F")
        cmd.align("Pentamer", "Hexamer")
        cmd.split_chains()
        # cmd.do("com *")
        # cmd.do("com *A")
        # cmd.do("com *B")
        # cmd.do("com *H")
        # cmd.do("com *G")
        # cmd.do("com Pentamer")
        # cmd.do("com Hexamer")
        # cmd.do("create Hexa_merged, *_A|*_B|A_*|B_*|Hexamer_COM")
        # cmd.do("create Penta_merged, *_H|*_G|H_*|G_*|Pentamer_COM")
        # cmd.save(f"{pdb_path}{original_name}\\{original_name}_Penta_merged.pdb","Penta_merged")
        # cmd.save(f"{pdb_path}{original_name}\\{original_name}_Hexa_merged.pdb","Hexa_merged")
    if not os.path.exists(
        f"{pdb_path}{original_name}"
    ):
        os.mkdir(
            f"{pdb_path}{original_name}"
        )
    for chain in "ABCDEFGHIJK":
        cmd.save(
            f"{pdb_path}{original_name}\\{original_name}_{chain}.pdb",
            f"*{chain}",
        )
    for i in cmd.get_names():
        cmd.do(f"com {i}")
    penta_center, *penta_chains = [
        i for i in cmd.get_names() if i.endswith("COM") and i.startswith("Pentamer")
    ]
    hexa_center, *hexa_chains = [
        i for i in cmd.get_names() if i.endswith("COM") and i.startswith("Hexamer")
    ]

    # cross_rmsd = []
    # for i in range(len(penta_chains)):
    #     for j in range(len(hexa_chains)):
    #         rmsd = cmd.align(penta_chains[i],hexa_chains[j])[0]
    #         cross_rmsd.append([rmsd,penta_chains[i],hexa_chains[j]])
    # self_hexa_rmsd = [[cmd.align(hexa_chains[i],hexa_chains[j])[0],hexa_chains[i],hexa_chains[j]] for i in range(len(hexa_chains)) for j in range(i+1,len(hexa_chains))]
    # self_penta_rmsd = [[cmd.align(penta_chains[i],penta_chains[j])[0],penta_chains[i],penta_chains[j]] for i in range(len(penta_chains)) for j in range(i+1,len(penta_chains))]
    total_chains = penta_chains + hexa_chains
    total_chains = [i.replace("_COM", "") for i in total_chains]
    # breakpoint()
    rmsd_table = []
    for chain in total_chains:
        for chain2 in total_chains:
            if chain == chain2:
                rmsd_table.append([0, chain, chain2])
            else:
                rmsd = cmd.align(chain, chain2)[0]
                rmsd_table.append([rmsd, chain, chain2])
    rmsd_table = np.array(rmsd_table)
    rmsd_table = pd.DataFrame(columns=["rmsd", "chain1", "chain2"], data=rmsd_table)
    rmsd_table.to_csv(
        f"{pdb_path}{original_name}\\{original_name}_rmsd.csv",
        index=False,
    )
    angle_penta = [
        cmd.get_angle(penta_chains[i], penta_center, penta_chains[i + 1])
        for i in range(len(penta_chains) - 1)
    ] + [cmd.get_angle(penta_chains[-1], penta_center, penta_chains[0])]
    angle_hexa = [
        cmd.get_angle(hexa_chains[i], hexa_center, hexa_chains[i + 1])
        for i in range(len(hexa_chains) - 1)
    ] + [cmd.get_angle(hexa_chains[-1], hexa_center, hexa_chains[0])]
    dist_penta = [
        cmd.get_distance(penta_chains[i], penta_center)
        for i in range(len(penta_chains))
    ]
    dist_hexa = [
        cmd.get_distance(hexa_chains[i], hexa_center) for i in range(len(hexa_chains))
    ]

    # to pandas
    pent = np.array([["Pentamer"] * len(angle_penta), angle_penta, dist_penta])
    hexa = np.array([["Hexamer"] * len(angle_hexa), angle_hexa, dist_hexa])
    data = np.concatenate([pent, hexa], axis=1).T
    df = pd.DataFrame(columns=["chain", "angle", "distance"], data=data)
    df.to_csv(
        f"{pdb_path}{original_name}\\{original_name}_angle_dist.csv",
        index=False,
    )

    cmd.delete("all")



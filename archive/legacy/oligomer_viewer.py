import os
import numpy as np
from pymol import cmd
from pymol import util
import argparse
import pymol
import pandas as pd
from requests import delete

pdb_path = "E:\\TemporaryFiles\\beautiful_design\\feasible_design\\final_design\\"

design_names = [f"HAL_{i}" for i in range(1, 11)]

for design_name in design_names:
    load_path = pdb_path + design_name + ".pdb"
    cmd.load(load_path)
    original_name = cmd.get_names()[0]
    cmd.set_name(original_name, "Pentamer")
    cmd.extract("Hexamer", "chain A,B,C,D,E,F")
    cmd.align("Pentamer", "Hexamer")
    cmd.split_chains()
    cmd.save(
        f"{pdb_path}{original_name}\\{original_name}_Hexamer.pdb",
        "Hexamer",
    )
    cmd.save(
        f"{pdb_path}{original_name}\\{original_name}_Pentamer.pdb",
        "Pentamer",
    )
    cmd.delete("all")

for design_name in design_names:
    load_path = pdb_path + design_name + "\\"+ design_name + ".pdb"
    penta_path = pdb_path + design_name + "\\"+ design_name + "_Pentamer.pdb"
    hexa_path = pdb_path + design_name + "\\"+ design_name + "_Hexamer.pdb"
    cmd.load(penta_path)
    pdb_path = "E:\\TemporaryFiles\\beautiful_design\\feasible_design\\final_design\\"
    cmd.do("spectrum b, blue_red, minimum=10, maximum=100")
    cmd.orient()
    cmd.rotate("z",90)
    split_names = cmd.get_names()[0].split("_")
    design_name = "HAL_"+split_names[1]
    cmd.png(f"{pdb_path}{design_name}_{split_names[2]}_top.png",width=1000, height=1095,dpi=300, ray=1)
    cmd.rotate("x",90)
    cmd.png(f"{pdb_path}{design_name}_{split_names[2]}_front.png",width=1000, height=1095, dpi=300, ray=1)
    cmd.delete("all")

    pdb_path = "E:\\TemporaryFiles\\beautiful_design\\feasible_design\\final_design\\"
    cmd.do("spectrum b, blue_red, minimum=10, maximum=100")
    cmd.load(hexa_path)
    cmd.orient()
    cmd.png(f"{pdb_path}{design_name}_hexa_top.png",width=3160, height=1095,dpi=300, ray=1)
    cmd.rotate("y",90)
    cmd.png(f"{pdb_path}{design_name}_hexa_front.png",width=3160, height=1095, dpi=300, ray=1)
    cmd.delete("all")
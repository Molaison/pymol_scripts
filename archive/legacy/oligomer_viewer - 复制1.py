import os
import numpy as np
from pymol import cmd
from pymol import util
import argparse
import pymol
import pandas as pd
from requests import delete

pdb_path = "E:\\TemporaryFiles\\beautiful_design\\feasible_design\\initial\\"
design_name = "HALF_4"
load_path = pdb_path + design_name + "\\"+ design_name + ".pdb"
penta_path = pdb_path + design_name + "\\"+ design_name + "_Pentamer.pdb"
hexa_path = pdb_path + design_name + "\\"+ design_name + "_Hexamer.pdb"
cmd.load(penta_path)
cmd.orient()
util.color_chains()
cmd.rotate("y",90)
cmd.png(f"{pdb_path}{design_name}_penta_top.png",width=1000, height=1095,dpi=300, ray=1)
cmd.rotate("x",90)
cmd.png(f"{pdb_path}{design_name}_penta_front.png",width=1000, height=1095, dpi=300, ray=1)
cmd.delete("all")

cmd.load(hexa_path)
cmd.orient()
util.color_chains()
cmd.rotate("y",90)
cmd.png(f"{pdb_path}{design_name}_hexa_top.png",width=1000, height=1095,dpi=300, ray=1)
cmd.rotate("x",90)
cmd.png(f"{pdb_path}{design_name}_hexa_front.png",width=1000, height=1095, dpi=300, ray=1)
cmd.delete("all")
from pymol import cmd
from pymol import util

original_name = cmd.get_names()[0]
import os;os.mkdir(f"E:\\TemporaryFiles\\beautiful_design\\feasible_design\\initial\\{original_name}")
if original_name.starts_with("HALF"):
    cmd.set_name(original_name,"Hexamer")
    cmd.extract("Pentamer","chain A,B,C,D,E")
    cmd.align("Pentamer","Hexamer")
    cmd.orient()
    cmd.translate([100,0,0],"Pentamer")
    util.color_chains("(all and elem C)",_self=cmd)
    cmd.orient()
    cmd.split_chains()
    for chain in "ABCDEFGHIJK":cmd.save(f"E:\\TemporaryFiles\\beautiful_design\\feasible_design\\initial\\{original_name}\\{original_name}_{chain}.pdb",f"*{chain}")
    cmd.com("*A")
    for i in cmd.get_names():cmd.do(f"com {i}")
    
    penta_center,*penta_chains = [i for i in cmd.get_names() if i.endswith("COM") and i.startswith("Pentamer")]
    hexa_center,*hexa_chains = [i for i in cmd.get_names() if i.endswith("COM") and i.startswith("Hexamer")]
    [cmd.get_angle(penta_chains[i],penta_center,penta_chains[i+1]) for i in range(len(penta_chains)-1)] + [cmd.get_angle(penta_chains[-1],penta_center,penta_chains[0])]
    [cmd.get_angle(hexa_chains[i],hexa_center,hexa_chains[i+1]) for i in range(len(hexa_chains)-1)] + [cmd.get_angle(hexa_chains[-1],hexa_center,hexa_chains[0])]

    # cmd.do("com *")
    # cmd.do("com *B")
    # cmd.do("com *F")
    # cmd.do("com *G")
    # cmd.do("com Pentamer")
    # cmd.do("com Hexamer")
    # cmd.do("create Penta_merged, *_A|*_B|A_*|B_*|Pentamer_COM")
    # cmd.save(f"E:\\TemporaryFiles\\beautiful_design\\feasible_design\\initial\\{original_name}\\{original_name}_Penta_merged.pdb","Penta_merged")
    # cmd.do("create Hexa_merged, *_F|*_G|F_*|G_*|Hexamer_COM")
    # cmd.do("alter name Hexa_merged and chain F, chain='A'")
    # cmd.do("alter name Hexa_merged and chain G, chain='B'")
    # cmd.save(f"E:\\TemporaryFiles\\beautiful_design\\feasible_design\\initial\\{original_name}\\{original_name}_Hexa_merged.pdb","Hexa_merged")
else:
    cmd.set_name(original_name,"Pentamer")
    cmd.extract("Hexamer","chain A,B,C,D,E,F")
    cmd.align("Hexamer","Pentamer")
    cmd.translate([100,0,0],"Pentamer")
    util.color_chains("(all and elem C)",_self=cmd)
    cmd.orient()


# cmd.run("E:\\TemporaryFiles\\beautiful_design\\feasible_design\\initial\\color_chains.py")

# cmd.png(f"E:\\TemporaryFiles\\beautiful_design\\feasible_design\\initial\\{original_name}_top.png",width=3160, height=1095,dpi=300, ray=1)
# cmd.rotate("x",90)
# cmd.png(f"E:\\TemporaryFiles\\beautiful_design\\feasible_design\\initial\\{original_name}_front.png",width=3160, height=1095, dpi=300, ray=1)


# ------------------------------


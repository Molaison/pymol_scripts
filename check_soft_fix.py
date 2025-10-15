from pymol import cmd


def check_soft_fix(origin_sele, rslt_sele, softfix_seq):
    cmd.do(f"sbs {origin_sele}, {softfix_seq};select origin1,selected_residues;color red,origin1;create origin1,origin1")
    cmd.do(f"sbs {rslt_sele}, {softfix_seq};select rslt1,selected_residues;color blue,rslt1;create rslt1,rslt1")
    cmd.align(origin_sele, rslt_sele)
    cmd.align("origin1", "rslt1")
    cmd.show_as("ribbon", "origin1 or rslt1")
    
cmd.auto_arg[0]["csf"] = [cmd.object_sc, "object_name", ""]
cmd.auto_arg[1]["csf"] = [cmd.object_sc, "object_name", ""]

cmd.extend("csf", check_soft_fix)
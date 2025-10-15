from pymol import cmd





def select_idx_sep_comma(obj,indices):
    indices = indices.strip("\"").strip("\'").strip()
    print(obj, indices)

    print(obj, indices.split(','))
    indices = [int(i) for i in indices.split(',')]


    cmd.select("selected_residues", f"{obj} and (resi {'+'.join([str(i) for i in indices])})")



cmd.auto_arg[0]["sis"] = [cmd.object_sc, "object_name", ""]
cmd.extend("sis", select_idx_sep_comma)
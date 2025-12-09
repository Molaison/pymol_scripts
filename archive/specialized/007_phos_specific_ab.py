from pymol import cmd


def show_phospho_interactions(phospho_selection, antibody_selection, cdr_selection, output_prefix="interaction", cutoff=4.0):
    """
    Highlight potential interactions (e.g., hydrogen bonds or salt bridges)
    between phosphorylated residues and antibody using distance criteria.
    
    Parameters:
        phospho_selection (str): PyMOL selection string for phosphorylated residues.
        antibody_selection (str): PyMOL selection string for antibody region.
        output_prefix (str): Prefix used for naming output selections/groups.
        cutoff (float): Distance cutoff in Å for interaction detection.
    """
    cmd.select(f"{output_prefix}_phospho_near", f"{antibody_selection} within {cutoff} of {phospho_selection}")
    cmd.show("sticks", f"{output_prefix}_phospho_near or {phospho_selection}")
    
    


    # Show as sticks
    cmd.show("sticks", f"{output_prefix}_contacts or ({output_prefix}_ab_atoms within {cutoff} of {output_prefix}_phospho_atoms)")
    
    # Optionally group results
    cmd.group(f"{output_prefix}_group", 
              f"{output_prefix}_phospho_atoms {output_prefix}_ab_atoms {output_prefix}_contacts")


# 

for obj in cmd.get_object_list():
    if "scfv" in obj or "nanobody" in obj:
        # Assuming the antibody is chain A
        cmd.select("antibody", f"{obj} and chain A")
        # Select phosphorylated residues
        cmd.select("phosphorylated", f"{obj} and resn TPO+SEP+PTR and chain B")
        
        if "scfv" in obj:
            # Select CDR regions for scFv
            CDR_sele = f"{obj} and chain A and (resi 25-31 or resi 51-55 or resi 95-105 or resi 155-167 or resi 183-189 or resi 222-231)"
        elif "nanobody" in obj:
            # Select CDR regions for nanobody
            CDR_sele = f"{obj} and chain A and (resi 26-32 or resi 52-57 or resi 99-115)"
        
        cmd.color("yellow", "phosphorylated")
        cmd.color("byhet", "phosphorylated")
        
        # Show interactions
        show_phospho_interactions("phosphorylated", "antibody",CDR_sele, output_prefix=f"{obj}_interaction")
    


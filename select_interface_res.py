from pymol import cmd, stored

def calculate_interface_residues_per_chain(complex_name, area_diff_cutoff=1.0, distance_cutoff=3.6):
    '''
    DESCRIPTION
    Calculate interface residues for each chain in the given complex.
    
    PARAMETERS
    complex_name: str - The name of the complex in PyMOL.
    area_diff_cutoff: float - The cutoff value for the interface definition (default: 1.0).
    
    RETURNS
    rVal: list - List of interface residues with chain, residue number, name, and difference value.
    stored.j: list - List of residues in the interface by distance.
    '''
    stored.r, stored.j, rVal, seen = [], [], [], []
    cmd.get_area(complex_name, load_b=1)
    cmd.select(f"interface_by_distance_{complex_name}", "none")
    
    for chain in cmd.get_chains(complex_name):
        cmd.create("remain_structure", complex_name)
        cmd.extract("chain_structure", f"remain_structure and chain {chain}")
        cmd.alter("chain_structure", "q=b")
        cmd.alter("remain_structure", "q=b")
        cmd.get_area("remain_structure", load_b=1)
        cmd.get_area("chain_structure", load_b=1)
        cmd.alter("chain_structure or remain_structure", "b=b-q")
        
        cmd.do(
            f"select interface_by_distance_{complex_name}, interface_by_distance_{complex_name} or ((chain_structure within {distance_cutoff} of remain_structure) or (remain_structure within {distance_cutoff} of chain_structure)) "
        )
        
        cmd.iterate("chain_structure", "stored.r.append((chain, resi, name, b));")
        
        for chain, resi, name, diff in stored.r:
            if abs(diff) >= float(area_diff_cutoff):
                key = f"{resi}-{chain}-{name}"
                if key not in seen:
                    seen.append(key)
                    rVal.append((chain, resi, name, diff))
    
    cmd.iterate(f"interface_by_distance_{complex_name}", "stored.j.append((chain, resi, name));")
    
    return rVal, stored.j


def select_interface_residues(complex_name, area_diff_cutoff=1.0, distance_cutoff=3.6):
    '''
    DESCRIPTION
    Select interface residues in the given complex.
    
    PARAMETERS
    complex_name: str - The name of the complex in PyMOL.
    area_diff_cutoff: float - The cutoff value for the interface definition (default: 1.0).
    '''
    interface_atoms, distance_atoms = calculate_interface_residues_per_chain(complex_name, area_diff_cutoff, distance_cutoff)
    interface_residues = []
    
    cmd.select(f"interface_by_area_{complex_name}", "none")
    
    for chain, resi, name, diff in interface_atoms:
        if (chain, resi) not in interface_residues:
            interface_residues.append((chain, resi))
            cmd.select(
                f"interface_by_area_{complex_name}",
                f"interface_by_area_{complex_name} or ({complex_name} and chain {chain} and i. {resi})"
            )
    
    cmd.select(f"interface_by_distance_{complex_name}", "none")
    cmd.delete("remain_structure chain_structure")
    
    for chain, resi, name in distance_atoms:
        cmd.select(
            f"interface_by_distance_{complex_name}",
            f"interface_by_distance_{complex_name} or ({complex_name} and chain {chain} and i. {resi})"
        )
    
    cmd.select(
        f"interface_{complex_name}",
        f"interface_by_area_{complex_name} and interface_by_distance_{complex_name}"
    )


cmd.extend("select_interface", select_interface_residues)

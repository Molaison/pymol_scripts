import os
import re

from pymol import cmd

STRUCTURE_PATTERN = r"^.+\.(?:pdb|cif)(?:\.gz)?$"
reg_pattern = STRUCTURE_PATTERN

def load_states(path=".", obj_name="protein_traces"):
    """

    DESCRIPTION

    Load all PDB files in the specified directory matching the given pattern.

    PARAMETERS

    path: str - The directory path containing PDB files (default: current directory).

    pattern: str - The regex pattern to match PDB files (default: match all PDB files).
    """

    global current_index, current_path, loaded_steps, reg_pattern

    current_path = path


    print("Loading files from", path, "into a single object",obj_name)

    files = os.listdir(path)

    pattern = re.compile(reg_pattern)

    loaded_steps = [file for file in files if pattern.match(file)]
    loaded_steps.sort()
    print(loaded_steps)

    for i,step in enumerate(loaded_steps):
        cmd.load(os.path.join(path, step),state=i+1,object=f"templ_{i+1}")
        cmd.create(obj_name,f"templ_{i+1}",1,i+1)
        cmd.delete(f"templ_{i+1}")

    cmd.disable("all")

    if loaded_steps:

        cmd.enable(loaded_steps[0].replace(".pdb", ""))

        current_index = 0
        
        
cmd.extend("load_states", load_states)

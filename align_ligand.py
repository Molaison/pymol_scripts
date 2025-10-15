from pymol import cmd 
import argparse
import os


def arg_parse():
    parser = argparse.ArgumentParser(description='''This script is used to generate the 2D image of the protein structure''')
    parser.add_argument('--input', help='The input pdb file')
    parser.add_argument('--ref', help='The ref pdb file')
    parser.add_argument('--ligand_name', help='The ligand residue name')
    args = parser.parse_args()
    return args

def main():
    args = arg_parse()

    input_file_name = os.path.basename(args.input).split('.')[0]
    ref_file_name = os.path.basename(args.ref).split('.')[0]

    cmd.load(args.input, 'input')
    cmd.load(args.ref, 'ref')
    cmd.alignto('input')
    cmd.extract('ligand', 'resn {}'.format(args.ligand_name))
    cmd.create("merged", "input or ligand")
    # get number of residues
    res_idx = range(1, len("".join(cmd.get_fastastr("merged").split("\n")[1:])) + 1)
    # renumber the residues
    cmd.alter("merged", "resi=next(res_idx)", space={"res_idx": iter(res_idx), "next": next})
    
    # reindex the residues in the merged object
    cmd.save(f'aligned_{ref_file_name}_to_{input_file_name}.pdb', 'merged')



if __name__ == '__main__':
    main()
    
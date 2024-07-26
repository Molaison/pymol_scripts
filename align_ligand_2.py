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

    if os.path.isdir(args.input):
        args.input = os.listdir(args.input)
    
    if os.path.isdir(args.ref):
        args.ref = os.listdir(args.ref)


    cmd.load(args.input, 'input')
    cmd.load(args.ref, 'ref')
    
    cmd.align('input', 'ref')
    cmd.extract('ligand', 'resn {}'.format(args.ligand_name))
    cmd.create("merged", "input or ligand")
    cmd.save('aligned.pdb', 'merged')



if __name__ == '__main__':
    main()
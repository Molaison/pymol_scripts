# Sequence utility commands

Load the module once with:

```pymol
run D:/SyncPath/GitRepo/PymolPlugins/analysis/alignment_selection/sequence_utils.py
```

The local installation also loads it automatically from `C:\Users\ADMIN\pymolrc_sequence_utils.py`. PyMOL discovers this alongside the existing `pymolrc.pml`, which remains untouched in its original encoding.

## Extended FASTA

```pymol
get_fasta
get_fasta my_object
get_fasta my_object and chain A
get_fasta my_object, include_solvent=1
```

`get_fasta` defaults to `enabled`. Standard amino acids are emitted as one-letter codes. PTMs, non-canonical residues, ligands, ions, and other components are emitted as `(RESN)`; common waters/solvents are omitted unless `include_solvent=1`.

Example:

```text
>model|chain=A
MKT(SEP)AI(ATP)(ZN)
```

Alias: `fasta_ext`.

## Sequence matching and highlighting

```pymol
find_seq MKTSA
find_seq MKTSA, color=cyan
find_seq A(MSE)GT, my_object, magenta, active_site
find_seq MKTSA, my_object, yellow, motif, show=none
```

`find_seq` searches each object/chain independently, defaults to `enabled`, saves every exact match into `seq_match`, colors it yellow, and shows sticks. Use `(RESN)` in the query to match a PTM or other explicit component. The fourth positional argument changes the selection name; `show=none` applies only the requested color.

Alias: `seq_find`.

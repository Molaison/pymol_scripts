"""Explicit ``run`` bridge: use pp_fasta and pp_select_sequence, residue_tokens=1.

No get_fasta/find_seq aliases or independent residue index are installed.
"""

import os
import sys

_SCRIPT = globals().get("__script__", globals().get("__file__", ""))
_ROOT = os.path.abspath(os.path.join(os.path.dirname(_SCRIPT), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymol import cmd
from pymolplugins.alignment.sequence import pp_fasta, pp_select_sequence
from pymolplugins.core.parameters import extend_command

extend_command(cmd, "pp_fasta", pp_fasta)
extend_command(cmd, "pp_select_sequence", pp_select_sequence)

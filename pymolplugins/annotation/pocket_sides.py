"""Choose the fixed and compared sides of a pocket interface."""

from dataclasses import dataclass

from pymol.Qt import QtCore, QtWidgets

from .command_templates import _chain_selection
from .pocket_variants import LIGAND, PROTEIN


@dataclass(frozen=True)
class PocketSides:
    target: str
    partner: str
    protein_partner: bool = False


def choose_pocket_sides(api, parent, scope, config, *, choose_proteins=False):
    target = config.get("protein_selection") or PROTEIN
    ligand = config.get("ligand_selection") or LIGAND
    if not choose_proteins:
        selected = f"{scope} and ({ligand})"
        if api.count_atoms(selected):
            protein_partner = bool(config.get("ligand_selection")) and not api.count_atoms(
                f"({selected}) and not polymer.protein")
            if protein_partner:
                target = f"({target}) and not ({ligand})"
            return PocketSides(target, ligand, protein_partner)

    protein = f"{scope} and ({PROTEIN})"
    chains = api.get_chains(protein)
    if len(chains) < 2:
        raise ValueError("Choose a ligand-bearing candidate or a complex with at least two protein chains.")
    selections = {chain: _chain_selection("object", scope, chain, api).replace("{object}", "all")
                  for chain in chains}
    anchors = {chain: api.count_atoms(f"{scope} and ({selections[chain]}) and name CA") for chain in chains}
    ranked = sorted(chains, key=lambda chain: (-anchors[chain], chain))
    target, partner = (ranked[0],), (ranked[1],)
    if choose_proteins or len(chains) > 2:
        chosen = _choose_chains(parent, chains, target, partner)
        if chosen is None:
            return None
        target, partner = chosen
    return PocketSides(" or ".join(f"({selections[chain]})" for chain in target),
                       " or ".join(f"({selections[chain]})" for chain in partner), True)


def _choose_chains(parent, chains, target, partner):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Protein–protein interface")
    layout = QtWidgets.QVBoxLayout(dialog)
    row = QtWidgets.QHBoxLayout()
    widgets = []
    for title, checked in (("Target chains (Cα fit)", target), ("Partner chains", partner)):
        column = QtWidgets.QVBoxLayout()
        column.addWidget(QtWidgets.QLabel(title))
        widget = QtWidgets.QListWidget(dialog)
        widget.setAccessibleName(title)
        widget.setMaximumHeight(min(165, max(56, len(chains) * 24 + 8)))
        for chain in chains:
            item = QtWidgets.QListWidgetItem(chain or "(blank chain)", widget)
            item.setData(QtCore.Qt.UserRole, chain)
            item.setCheckState(QtCore.Qt.Checked if chain in checked else QtCore.Qt.Unchecked)
        column.addWidget(widget)
        row.addLayout(column)
        widgets.append(widget)
    layout.addLayout(row)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    def selected():
        return tuple(tuple(widget.item(i).data(QtCore.Qt.UserRole) for i in range(widget.count())
                           if widget.item(i).checkState() == QtCore.Qt.Checked) for widget in widgets)

    def validate():
        left, right = selected()
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(bool(left and right and not set(left) & set(right)))

    for widget in widgets:
        widget.itemChanged.connect(validate)
    validate()
    return selected() if dialog.exec() == QtWidgets.QDialog.Accepted else None

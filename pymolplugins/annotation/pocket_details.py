"""Contact and RMSD tables for the pocket comparison details."""
from pymol.Qt import QtCore, QtGui, QtWidgets

from .pocket_analysis import CONTACT_COLORS
from .pocket_variants import site_label


def _table(columns, rows, name, first_wide=False):
    table = QtWidgets.QTableWidget(rows, len(columns))
    table.setObjectName(name)
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    table.verticalHeader().hide()
    header = table.horizontalHeader()
    header.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
    if first_wide:
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
        table.setColumnWidth(0, 205)
    return table


def interactions_page(contacts, radius, focus, redraw):
    page = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(page)
    top = QtWidgets.QHBoxLayout()
    toggle = QtWidgets.QCheckBox("Show lines for selected site")
    toggle.setChecked(True)
    toggle.toggled.connect(redraw)
    top.addWidget(toggle)
    top.addStretch()
    top.addWidget(QtWidgets.QLabel(f"Heavy-atom cutoff {radius:g} Å"))
    layout.addLayout(top)
    table = _table(("Site", "Interaction", "Change", "Candidates"), len(contacts), "pocketInteractions")
    table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
    table.setColumnWidth(0, 88)
    for row, contact in enumerate(contacts):
        values = (site_label(contact.site), contact.kind, contact.change,
                  f"{contact.present}/{contact.evaluated}")
        for column, value in enumerate(values):
            table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        swatch = QtGui.QPixmap(12, 12)
        swatch.fill(QtGui.QColor.fromRgbF(*CONTACT_COLORS[contact.change]))
        table.item(row, 2).setIcon(QtGui.QIcon(swatch))
        origin = "reference" if contact.in_reference else "first aligned candidate showing this interaction"
        table.item(row, 2).setToolTip(f"Line drawn using coordinates from the {origin}.")
        table.item(row, 3).setToolTip("Candidates with the interaction / candidates with a mapped ligand and site")
    table.cellClicked.connect(lambda row, _: focus(contacts[row].site))
    table.cellActivated.connect(lambda row, _: focus(contacts[row].site))
    layout.addWidget(table)
    return page, table, toggle


def _tolerance(label, value):
    spin = QtWidgets.QDoubleSpinBox()
    spin.setAccessibleName(label)
    spin.setDecimals(2)
    spin.setRange(0.05, 50)
    spin.setSingleStep(0.1)
    spin.setSuffix(" Å")
    spin.setValue(value)
    return spin


def _endpoint(atoms):
    first = atoms[0]
    suffix = first.name if len(atoms) == 1 else "ring"
    return f"{site_label(first.site)} {first.residue} {suffix}"


def _contact_label(contact):
    return f"{contact.change} · {_endpoint(contact.protein_atoms)} ↔ {_endpoint(contact.ligand_atoms)}"


def _contact_tip(contact):
    protein = ", ".join(atom.name for atom in contact.protein_atoms)
    ligand = ", ".join(atom.name for atom in contact.ligand_atoms)
    return f"{_contact_label(contact)}\nProtein atoms: {protein}\nLigand atoms: {ligand}"


class CandidateDetails(QtWidgets.QWidget):
    def __init__(self, clear_preview, focus_contact):
        super().__init__()
        self.preview = None
        self.overview = ""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QtWidgets.QHBoxLayout()
        self.summary = QtWidgets.QLabel()
        self.summary.setTextFormat(QtCore.Qt.PlainText)
        self.summary.setWordWrap(True)
        header.addWidget(self.summary, 1)
        self.all_contacts = QtWidgets.QPushButton("All contacts")
        self.all_contacts.setAutoDefault(False)
        self.all_contacts.setProperty("role", "compact")
        self.all_contacts.clicked.connect(lambda: focus_contact(None))
        header.addWidget(self.all_contacts)
        clear = QtWidgets.QPushButton("Clear overlay")
        clear.setAutoDefault(False)
        clear.setProperty("role", "compact")
        clear.clicked.connect(clear_preview)
        header.addWidget(clear)
        layout.addLayout(header)
        self.table = _table(("Site", "Interaction", "Change"), 0, "pocketCandidateContacts")
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setMaximumHeight(134)
        self.table.cellClicked.connect(lambda row, _: focus_contact(self.preview.changes[row]))
        self.table.cellActivated.connect(lambda row, _: focus_contact(self.preview.changes[row]))
        layout.addWidget(self.table)
        self.hide()

    def show_preview(self, preview):
        self.preview = preview
        if preview is None:
            self.hide()
            return
        gained = sum(item.change == "Gained" for item in preview.changes)
        lost = len(preview.changes) - gained
        summary = f"Blue +{gained} · Orange −{lost}" if preview.ligand_matched else "Contact comparison unavailable"
        self.overview = f"Magenta ligand · Violet backbone · {summary}"
        self.table.setRowCount(len(preview.changes))
        for row, contact in enumerate(preview.changes):
            for column, value in enumerate((site_label(contact.site), contact.kind, contact.change)):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(_contact_tip(contact))
                self.table.setItem(row, column, item)
            swatch = QtGui.QPixmap(12, 12)
            swatch.fill(QtGui.QColor.fromRgbF(*CONTACT_COLORS[contact.change]))
            self.table.item(row, 2).setIcon(QtGui.QIcon(swatch))
        self.table.setVisible(bool(preview.changes))
        self.show_contact(None)
        self.show()

    def show_contact(self, contact):
        self.all_contacts.setVisible(contact is not None)
        self.summary.setText(self.overview if contact is None else _contact_label(contact))
        self.summary.setToolTip("" if contact is None else _contact_tip(contact))
        if contact is None:
            self.table.clearSelection()
        else:
            row = next(i for i, value in enumerate(self.preview.changes) if value is contact)
            self.table.selectRow(row)


def rmsd_page(geometry, ligand_limit, backbone_limit, limits_changed, show_preview, clear_preview, focus_contact):
    page = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(page)
    controls = QtWidgets.QHBoxLayout()
    ligand = _tolerance("Ligand tolerance", ligand_limit)
    backbone = _tolerance("Pocket backbone tolerance", backbone_limit)
    controls.addWidget(QtWidgets.QLabel("Ligand tolerance"))
    controls.addWidget(ligand)
    controls.addWidget(QtWidgets.QLabel("Pocket backbone tolerance"))
    controls.addWidget(backbone)
    controls.addStretch()
    layout.addLayout(controls)
    table = _table(("Candidate", "Ligand (Å)", "Backbone (Å)", "Geometry"),
                   len(geometry), "pocketRmsd", first_wide=True)
    table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Interactive)
    table.setColumnWidth(3, 165)
    labels = ("Ligand heavy atoms matched by atom labels after protein Cα fit",
              "N, Cα, C and O in the reference pocket after the same fit")
    for i, description in enumerate(labels, 1):
        table.horizontalHeaderItem(i).setToolTip(description)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    layout.addWidget(table)
    details = CandidateDetails(clear_preview, focus_contact)
    layout.addWidget(details)

    def refresh():
        selected = table.item(table.currentRow(), 0)
        entry_id = selected.data(QtCore.Qt.UserRole) if selected is not None else None
        limits = ligand.value(), backbone.value()
        limits_changed(*limits)
        ordered = sorted(geometry, key=lambda record: (
            record.status(*limits) == "Within tolerances",
            -max(value / limit if value is not None else 0
                 for value, limit in zip((record.ligand, record.backbone), limits)),
            record.candidate))
        table.setSortingEnabled(False)
        for row, record in enumerate(ordered):
            status = record.status(*limits)
            candidate = QtWidgets.QTableWidgetItem(record.candidate)
            candidate.setData(QtCore.Qt.UserRole, record.entry_id)
            hint = "\nClick to overlay this candidate on the reference." if status == "Outside tolerance" else ""
            candidate.setToolTip(record.candidate + hint)
            table.setItem(row, 0, candidate)
            for column, value in enumerate((record.ligand, record.backbone), 1):
                item = QtWidgets.QTableWidgetItem(f"{value:.2f}" if value is not None else "—")
                if value is not None:
                    item.setToolTip(f"{value:.5f} Å")
                table.setItem(row, column, item)
            item = QtWidgets.QTableWidgetItem(status)
            if status != "Within tolerances":
                item.setForeground(QtGui.QBrush(QtGui.QColor("#9a4122")))
            table.setItem(row, 3, item)
            if record.entry_id == entry_id:
                table.setCurrentCell(row, 0)

    def activate(row, _column):
        item = table.item(row, 0)
        if item is not None:
            show_preview(item.data(QtCore.Qt.UserRole))

    ligand.valueChanged.connect(refresh)
    backbone.valueChanged.connect(refresh)
    table.cellClicked.connect(activate)
    table.cellActivated.connect(activate)
    refresh()
    return page, details


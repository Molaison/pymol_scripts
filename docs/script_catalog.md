# Script Catalog

| Category | Path | Purpose / Primary Commands |
| --- | --- | --- |
| Alignment & Selection | `analysis/alignment_selection/select_by_sequence.py` | Implements `sbs`/`select_sequence` to locate sequence motifs, highlight matches, and auto-visualize residues with optional gap-tolerant search. |
| Alignment & Selection | `analysis/alignment_selection/select_interface_res.py` | Provides `select_interface_enhanced` and friends for CB-distance, SASA, contact, and combined interface residue analysis. |
| Alignment & Selection | `analysis/alignment_selection/align_by_ligand.py` | Adds `alignto_ligand`/`super_ligand` for aligning complexes based on ligand selections or formatted ligand object lists. |
| Alignment & Selection | `analysis/alignment_selection/select_idx_sep_comma.py` | Quick helper (`sis`) to select arbitrary residue indices supplied as comma-separated strings. |
| Alignment & Selection | `analysis/alignment_selection/sequence_diff_highlight.py` | Qt + console workflow to color residues that differ between enabled objects using consensus or explicit reference. |
| Binding / Interaction | `analysis/interactions/draw_hydrogen_bonds.py` | `draw_interactions` visualizes inter-chain hydrogen bonds and salt bridges with cyan/magenta dashes. |
| Binding / Interaction | `analysis/interactions/ligand_surrounding.py` | `show_ligand_env`, `preset_binding`, etc., reveal ligand environments (sticks, surfaces, properties, distances). |
| Binding / Interaction | `analysis/interactions/show_contacts.py` | Legacy `contacts` plugin that classifies good/bad polar contacts plus Tk/Qt GUI entry points. |
| Cavities | `analysis/cavities/identify_backbone_cavity.py` | Full-feature cavity finder with alphashape grid search, CGO output, and optional Qt dialog. |
| Cavities | `analysis/cavities/identify_backbone_cavity_gemini.py` | Variant of the backbone cavity finder tuned for Gemini-style workflows. |
| Geometry | `analysis/geometry/measure_inertia.py` | Computes inertia tensors, draws ellipsoids, exports MSMS surfaces, and exposes `calculate_inertia`. |
| Geometry | `analysis/geometry/draw_ellipsoids.py` | Standalone CGO ellipsoid builder that can be called with arbitrary radii and Euler rotations. |
| Visualization Styles | `visualization/styles/set_publication_style.py` | `pbs` command that toggles ray-tracing parameters for publication renders. |
| Visualization Styles | `visualization/styles/macaron_style.py` & `visualization/plugins/macaron_style_plugin.py` | Gradient color spectrum + Qt dialog (`apply_custom_style`, `show_macaron_dialog`) for preset palettes. |
| Visualization Styles | `visualization/styles/ink_wash_style.py` | Ink-wash inspired palette and render tweaks for stylized figures. |
| Visualization Styles | `visualization/styles/high_contrast_ray_style.py` | Minimal ray-trace preset for quick high-contrast outputs. |
| Visualization Overlays | `visualization/overlays/axes_overlay.py` | Callback-driven viewport axes drawn via CGO cylinders/cones. |
| Visualization Overlays | `visualization/overlays/z_axis_overlay.ini` | CGO cylinder definition for showcasing a z-axis alignment cue. |
| Workflows | `workflows/dataset_navigation/screen_boost.py` | Bucket loader with pinning, keyboard shortcuts, and selection export; pairs with `file_management.py`. |
| Workflows | `workflows/dataset_navigation/file_management.py` | Modular loader with pinning, batch navigation, and switch-command registration (`load_structures`). |
| Workflows | `workflows/dataset_navigation/load_into_single_object.py` | Loads a directory of files into a single multi-state object for movie-style review. |
| Workflows | `plugins/structure_annotator.py` | Qt panel for fast structure annotation that writes labels back into PDB `REMARK` or a mmCIF save frame; provides `show_structure_annotator_panel`, `sa_load_dir`, `sa_annotate`. |
| Workflows | `workflows/grouping/load_into_groups.py` | Groups aligned models by parsed prefixes and aligns each group before merging. |
| Examples | `examples/pymol_wiki/color_by_mutation.py` | PyMOL Wiki script for visualizing mutations via BLOSUM90-based coloring. |
| Archive / Specialized | `archive/specialized/align_ligand.py` | CLI helper for transferring ligands between reference/destination structures. |
| Archive / Specialized | `archive/specialized/compute_radius_of_gyration.py` | Standalone radius-of-gyration calculator retained for reference workflows. |
| Archive / Specialized | `archive/specialized/validate_soft_fix.py` | Diagnostic script for checking “soft fix” patching steps. |

This catalog focuses on actively used scripts plus the most relevant archived utilities. If you add a new module, drop a short description here so downstream users can quickly discover it.

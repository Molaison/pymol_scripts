"""Identity-bundle and review-scene helpers for PyMOL acceptance outputs.

This module intentionally does not bind keys on import.  Generated review
scripts call ``acc_bind_review_keys`` so key bindings are local to the produced
session workflow instead of every PyMOL session that happens to load the skill.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from pymol import CmdException, cmd


BUNDLE_SUFFIX = "__bundle"
SIDECHAIN_BACKBONE_NAMES = "N+CA+C+O+OXT+H+H1+H2+H3"
_bundle_index = -1
_scene_index = -1
_stored_scenes: List[str] = []


def _safe_name(value: str, default: str = "acc") -> str:
    text = str(value or default).strip()
    text = re.sub(r"[^0-9A-Za-z_]+", "_", text).strip("_")
    if not text:
        text = default
    if text[0].isdigit():
        text = f"acc_{text}"
    return text


def _truthy(value, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _as_names(value) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[\s,]+", value.strip()) if part]
    return [str(part).strip() for part in value if str(part).strip()]


def _all_names() -> List[str]:
    names: List[str] = []
    for mode in ("all", "objects", "selections"):
        try:
            for name in cmd.get_names(mode):
                if name not in names:
                    names.append(name)
        except Exception:
            pass
    return names


def _set(name: str, value, selection: str = "") -> None:
    try:
        if selection:
            cmd.set(name, value, selection)
        else:
            cmd.set(name, value)
    except Exception:
        # Some PyMOL builds lack newer label/render settings.  Do not turn
        # style polish into a hard blocker when the core evidence is present.
        pass


def _coords(selection: str):
    try:
        return cmd.get_coords(selection, state=1)
    except Exception:
        return None


def _count_atoms(selection: str) -> int:
    try:
        return int(cmd.count_atoms(selection))
    except Exception:
        return 0


def _assert_atoms(selection: str, purpose: str = "selection") -> None:
    if _count_atoms(selection) == 0:
        raise CmdException(f"{purpose} has no atoms: {selection}")


def _centroid(selection: str) -> Tuple[float, float, float]:
    coords = _coords(selection)
    if coords is None or len(coords) == 0:
        raise CmdException(f"selection has no coordinates: {selection}")
    n = float(len(coords))
    return (
        float(sum(row[0] for row in coords) / n),
        float(sum(row[1] for row in coords) / n),
        float(sum(row[2] for row in coords) / n),
    )


def bundle_name(identity: str) -> str:
    return f"{_safe_name(identity)}{BUNDLE_SUFFIX}"


def asset_name(identity: str, role: str) -> str:
    return f"{_safe_name(identity)}__{_safe_name(role)}"


def _identity_from_group(group: str) -> str:
    return group[: -len(BUNDLE_SUFFIX)] if group.endswith(BUNDLE_SUFFIX) else group


def _identity_prefix(identity: str) -> str:
    return f"{_safe_name(identity)}__"


def _bundle_groups() -> List[str]:
    return sorted(name for name in _all_names() if name.endswith(BUNDLE_SUFFIX))


def _names_for_identity(identity: str) -> List[str]:
    prefix = _identity_prefix(identity)
    group = bundle_name(identity)
    names = [name for name in _all_names() if name == group or name.startswith(prefix)]
    return [group] + [name for name in sorted(names) if name != group]


def _enable_names(names: Iterable[str]) -> None:
    for name in names:
        try:
            cmd.enable(name, parents=1)
        except TypeError:
            cmd.enable(name)
        except Exception:
            pass


def _disable_names(names: Iterable[str]) -> None:
    for name in names:
        try:
            cmd.disable(name)
        except Exception:
            pass


def acc_bundle(identity: str, members: str = "", close=1) -> str:
    """Create or update one identity bundle.

    All generated evidence objects should use the ``<identity>__role`` prefix.
    """

    group = bundle_name(identity)
    member_list = _as_names(members)
    if member_list:
        cmd.group(group, " ".join(member_list), "add")
    else:
        try:
            cmd.group(group, "", "open")
        except Exception:
            pass
    try:
        cmd.group(group, "", "close" if _truthy(close, True) else "open")
    except Exception:
        pass
    return group


def acc_add_to_bundle(identity: str, members: str) -> str:
    return acc_bundle(identity, members=members, close=1)


def acc_show_only_bundles(
    identities,
    zoom_selection: str = "",
    padding=6.0,
) -> List[str]:
    """Enable only the requested identity bundles and their same-prefix members."""

    requested = _as_names(identities)
    if not requested:
        raise CmdException("at least one identity is required")

    for existing_group in _bundle_groups():
        existing_identity = _identity_from_group(existing_group)
        _disable_names(_names_for_identity(existing_identity))

    enabled_groups: List[str] = []
    for identity in requested:
        names = _names_for_identity(identity)
        _enable_names(names)
        enabled_groups.append(bundle_name(identity))

    zoom_sel = str(zoom_selection or "enabled").strip()
    coords = _coords(zoom_sel)
    if coords is not None and len(coords) > 0:
        cmd.zoom(zoom_sel, float(padding))
    return enabled_groups


def acc_enable_bundle(identity: str, zoom_selection: str = "", padding=6.0) -> str:
    groups = acc_show_only_bundles([identity], zoom_selection=zoom_selection, padding=padding)
    return groups[0]


def acc_list_bundles() -> List[str]:
    groups = _bundle_groups()
    for index, group in enumerate(groups, 1):
        print(f"{index:02d}. {_identity_from_group(group)}")
    return groups


def acc_next_bundle(step=1) -> Optional[str]:
    global _bundle_index
    groups = _bundle_groups()
    if not groups:
        print("No identity bundles found. Create them with acc_bundle.")
        return None
    _bundle_index = (_bundle_index + int(step)) % len(groups)
    identity = _identity_from_group(groups[_bundle_index])
    acc_enable_bundle(identity)
    print(f"Bundle {_bundle_index + 1}/{len(groups)}: {identity}")
    return identity


def acc_prev_bundle() -> Optional[str]:
    return acc_next_bundle(step=-1)


def _scene_list() -> List[str]:
    try:
        scenes = list(cmd.get_scene_list())
        if scenes:
            return scenes
    except Exception:
        pass
    return list(_stored_scenes)


def acc_next_scene(step=1) -> Optional[str]:
    global _scene_index
    scenes = _scene_list()
    if not scenes:
        print("No scenes found. Store scenes with acc_store_scene.")
        return None
    _scene_index = (_scene_index + int(step)) % len(scenes)
    scene_name = scenes[_scene_index]
    cmd.scene(scene_name, "recall")
    print(f"Scene {_scene_index + 1}/{len(scenes)}: {scene_name}")
    return scene_name


def acc_prev_scene() -> Optional[str]:
    return acc_next_scene(step=-1)


def acc_bind_review_keys(mode: str = "identity", next_key: str = "right", prev_key: str = "left") -> str:
    """Bind review navigation keys for a generated acceptance session."""

    review_mode = str(mode or "identity").strip().lower()
    if review_mode not in {"identity", "scene"}:
        raise CmdException("mode must be identity or scene")
    if review_mode == "scene":
        cmd.set_key(next_key, acc_next_scene)
        cmd.set_key(prev_key, acc_prev_scene)
    else:
        cmd.set_key(next_key, acc_next_bundle)
        cmd.set_key(prev_key, acc_prev_bundle)
    print(f"[review-keys] mode={review_mode} next={next_key} prev={prev_key}")
    return review_mode


def acc_publication_defaults(background: str = "white") -> None:
    cmd.bg_color(background)
    _set("opaque_background", 1)
    _set("ray_trace_mode", 1)
    _set("ray_trace_gain", 0.04)
    _set("ray_shadow", 0)
    _set("ambient", 0.75)
    _set("specular", 0.15)
    _set("reflect", 0.1)
    _set("antialias", 2)
    _set("cartoon_side_chain_helper", 1)
    _set("dash_radius", 0.07)
    _set("dash_gap", 0.25)
    _set("label_shadow_mode", 0)
    _set("label_font_id", 5)
    _set("label_size", 10)
    _set("label_color", "black")
    _set("label_bg_color", "white")


def acc_sidechain_sticks(selection: str, carbon_color: str = "keep", stick_radius=0.22) -> str:
    """Show anchored residue sticks with hetero atoms colored by element.

    The selection is expanded to complete protein residues and includes their
    backbone heavy atoms.  With a visible cartoon, ``cartoon_side_chain_helper``
    suppresses redundant backbone sticks while retaining the CA-to-side-chain
    connection.  Excluding CA before drawing makes side chains look detached.
    """

    sel = str(selection).strip() or "enabled"
    residue_sticks = f"((byres (({sel}) and polymer.protein)) and not elem H)"
    atoms = cmd.get_unused_name("_acc_stick_atoms")
    try:
        cmd.select(atoms, residue_sticks)
        _assert_atoms(atoms, "sidechain residue selection")
        _set("cartoon_side_chain_helper", 1)
        cmd.show("sticks", atoms)
        _set("stick_radius", float(stick_radius), atoms)
        _color_sidechain_atoms(atoms, carbon_color)
    finally:
        cmd.delete(atoms)
    return residue_sticks


def _color_sidechain_atoms(selection: str, carbon_color: str = "keep") -> None:
    if carbon_color and str(carbon_color).lower() not in {"keep", "none"}:
        cmd.color(carbon_color, f"({selection}) and elem C")
    present = set()
    cmd.iterate(selection, "present.add(elem)", space={"present": present})
    for elem, color in {
        "O": "oxygen",
        "N": "nitrogen",
        "S": "sulfur",
        "P": "phosphorus",
        "H": "hydrogen",
    }.items():
        if elem in present:
            cmd.color(color, f"({selection}) and elem {elem}")


def acc_contact_sidechains(
    selection: str,
    partner_selection: str,
    cutoff=4.0,
    name: str = "",
    identity: str = "",
    carbon_color: str = "keep",
    stick_radius=0.20,
    cb_sphere=1,
    cb_sphere_scale=0.24,
    contact_scope: str = "sidechain",
    show_hydrogens=0,
) -> str:
    """Show anchored sticks for residues whose atoms contact a partner selection.

    By default, contact residues are derived from side-chain heavy atoms within
    cutoff of the partner.  This prevents backbone-only neighbors from appearing
    as side-chain evidence.  Once membership is established, the complete
    residue heavy atoms are drawn so a visible cartoon and
    ``cartoon_side_chain_helper`` preserve the CA-to-side-chain connection.
    """

    sel = str(selection).strip() or "enabled"
    partner = str(partner_selection).strip()
    if not partner:
        raise CmdException("contact sidechain partner_selection is required")

    _assert_atoms(sel, "contact sidechain source selection")
    _assert_atoms(partner, "contact sidechain partner selection")

    source_atoms = f"(({sel}) and polymer.protein and not elem H)"
    scope = str(contact_scope or "sidechain").strip().lower()
    if scope == "sidechain":
        source_atoms = f"({source_atoms} and not (name {SIDECHAIN_BACKBONE_NAMES}))"
    elif scope not in {"all", "residue"}:
        raise CmdException("contact_scope must be sidechain, all, or residue")

    partner_atoms = f"(({partner}) and not elem H)"
    _assert_atoms(source_atoms, "contact sidechain atom source")
    _assert_atoms(partner_atoms, "contact sidechain partner heavy atoms")

    selection_name = _safe_name(name) if name else asset_name(identity or "acc", "contact_residues")
    contact_residues = f"byres (({source_atoms}) within {float(cutoff)} of ({partner_atoms}))"
    cmd.select(selection_name, contact_residues)
    _assert_atoms(selection_name, "contact sidechain residues")

    residue_sticks = f"({selection_name} and polymer.protein)"
    if not _truthy(show_hydrogens, False):
        residue_sticks = f"({residue_sticks} and not elem H)"
    _assert_atoms(residue_sticks, "contact residue display atoms")
    _set("cartoon_side_chain_helper", 1)
    cmd.show("sticks", residue_sticks)
    _set("stick_radius", float(stick_radius), residue_sticks)
    _color_sidechain_atoms(residue_sticks, carbon_color)

    if _truthy(cb_sphere, True):
        cb = f"({selection_name} and polymer.protein and name CB)"
        if _count_atoms(cb):
            cmd.show("spheres", cb)
            _set("sphere_scale", float(cb_sphere_scale), cb)
            if carbon_color and str(carbon_color).lower() not in {"keep", "none"}:
                cmd.color(carbon_color, cb)

    if identity:
        acc_add_to_bundle(identity, selection_name)
    return selection_name


def acc_ligand_sticks(
    selection: str = "organic",
    carbon_color: str = "brightorange",
    stick_radius=0.28,
) -> str:
    sel = str(selection).strip() or "organic"
    _assert_atoms(sel, "ligand selection")
    cmd.show("sticks", sel)
    _set("stick_radius", float(stick_radius), sel)
    if carbon_color:
        cmd.color(carbon_color, f"({sel}) and elem C")
    for elem, color in {
        "O": "oxygen",
        "N": "nitrogen",
        "S": "sulfur",
        "P": "phosphorus",
        "H": "hydrogen",
    }.items():
        if _count_atoms(f"({sel}) and elem {elem}"):
            cmd.color(color, f"({sel}) and elem {elem}")
    return sel


def acc_selection_sticks(
    selection: str,
    color: str = "",
    stick_radius=0.22,
) -> str:
    """Show one existing selection as sticks without creating a helper object."""

    sel = str(selection).strip()
    _assert_atoms(sel, "selection sticks selection")
    cmd.show("sticks", sel)
    _set("stick_radius", float(stick_radius), sel)
    if color:
        cmd.color(str(color), sel)
    return sel


def acc_surface(
    selection: str,
    transparency=0.45,
    color: str = "",
) -> str:
    """Show one existing selection as a transparent surface representation."""

    sel = str(selection).strip()
    _assert_atoms(sel, "surface selection")
    cmd.show("surface", sel)
    cmd.set("transparency", float(transparency), sel)
    if color:
        cmd.color(str(color), sel)
    return sel


def acc_polar_contacts(
    name: str,
    selection1: str,
    selection2: str,
    cutoff=3.6,
    color: str = "yelloworange",
    dash_radius=0.07,
    hide_distance_labels=1,
    identity: str = "",
) -> str:
    obj = _safe_name(name, "contacts")
    _assert_atoms(selection1, "polar contact selection1")
    _assert_atoms(selection2, "polar contact selection2")
    cmd.distance(obj, selection1, selection2, float(cutoff), mode=2)
    _set("dash_radius", float(dash_radius), obj)
    _set("dash_gap", 0.25, obj)
    cmd.color(color, obj)
    if _truthy(hide_distance_labels, True):
        cmd.hide("labels", obj)
    if identity:
        acc_add_to_bundle(identity, obj)
    return obj


def acc_residue_label(
    selection: str,
    text: str,
    name: str = "",
    identity: str = "",
    offset: str = "1.2,1.2,1.2",
    size=10,
    color: str = "black",
    bg_color: str = "white",
    connector=1,
) -> str:
    """Place a movable pseudoatom label anchored near a residue selection."""

    sel = str(selection).strip()
    _assert_atoms(sel, "label anchor selection")
    label_text = str(text)
    if "\n" in label_text or "\\n" in label_text:
        raise ValueError("PyMOL label text must be one visible line; create separate labels with staggered label_position offsets")
    base_name = _safe_name(name) if name else asset_name(identity or "acc", f"label_{label_text}")
    try:
        dx, dy, dz = [float(part.strip()) for part in str(offset).split(",")]
    except Exception:
        dx, dy, dz = 1.2, 1.2, 1.2
    cx, cy, cz = _centroid(sel)
    cmd.pseudoatom(base_name, pos=(cx + dx, cy + dy, cz + dz), label=label_text)
    cmd.hide("everything", base_name)
    cmd.show("labels", base_name)
    _set("label_size", float(size), base_name)
    _set("label_color", color, base_name)
    if bg_color:
        _set("label_bg_color", bg_color, base_name)

    members = [base_name]
    if _truthy(connector, True):
        connector_name = f"{base_name}__connector"
        cmd.distance(connector_name, base_name, sel, 999.0, mode=0)
        cmd.hide("labels", connector_name)
        _set("dash_radius", 0.035, connector_name)
        _set("dash_gap", 0.15, connector_name)
        cmd.color(color, connector_name)
        members.append(connector_name)
    if identity:
        acc_add_to_bundle(identity, " ".join(members))
    return base_name


def acc_store_scene(
    scene_name: str,
    zoom_selection: str = "enabled",
    padding=6.0,
    message: str = "",
) -> str:
    sel = str(zoom_selection or "enabled").strip()
    coords = _coords(sel)
    if coords is None or len(coords) == 0:
        raise CmdException(f"scene zoom selection has no coordinates: {sel}")
    cmd.orient(sel)
    cmd.zoom(sel, float(padding))
    cmd.scene(str(scene_name), "store", message=str(message or ""))
    if scene_name not in _stored_scenes:
        _stored_scenes.append(str(scene_name))
    return str(scene_name)


def acc_render_png(filename: str, width=1800, height=1400, ray=1) -> str:
    output = Path(str(filename))
    output.parent.mkdir(parents=True, exist_ok=True)
    if _truthy(ray, True):
        cmd.ray(int(width), int(height))
    cmd.png(str(output), width=int(width), height=int(height), ray=0)
    return str(output)


def acc_cleanup_temp(pattern: str = "_align* _acc_* tmp_* sele") -> None:
    for token in str(pattern).split():
        try:
            cmd.delete(token)
        except Exception:
            pass


cmd.extend("acc_bundle", acc_bundle)
cmd.extend("acc_add_to_bundle", acc_add_to_bundle)
cmd.extend("acc_show_only_bundles", acc_show_only_bundles)
cmd.extend("acc_enable_bundle", acc_enable_bundle)
cmd.extend("acc_list_bundles", acc_list_bundles)
cmd.extend("acc_next_bundle", acc_next_bundle)
cmd.extend("acc_prev_bundle", acc_prev_bundle)
cmd.extend("acc_next_scene", acc_next_scene)
cmd.extend("acc_prev_scene", acc_prev_scene)
cmd.extend("acc_bind_review_keys", acc_bind_review_keys)
cmd.extend("acc_publication_defaults", acc_publication_defaults)
cmd.extend("acc_sidechain_sticks", acc_sidechain_sticks)
cmd.extend("acc_contact_sidechains", acc_contact_sidechains)
cmd.extend("acc_ligand_sticks", acc_ligand_sticks)
cmd.extend("acc_selection_sticks", acc_selection_sticks)
cmd.extend("acc_surface", acc_surface)
cmd.extend("acc_polar_contacts", acc_polar_contacts)
cmd.extend("acc_residue_label", acc_residue_label)
cmd.extend("acc_store_scene", acc_store_scene)
cmd.extend("acc_render_png", acc_render_png)
cmd.extend("acc_cleanup_temp", acc_cleanup_temp)

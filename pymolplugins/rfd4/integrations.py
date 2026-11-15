"""Reuse the style, contact and sequence tools in this PymolPlugins package."""
import uuid


def apply_style(preset, obj):
    from pymolplugins.styles.registry import pp_style_apply
    return pp_style_apply(preset, selection="model " + obj, snapshot=1, render=0)


def revert_style(snapshot):
    from pymolplugins.styles.registry import pp_style_revert
    return pp_style_revert(snapshot_id=snapshot)


def contacts(preset, obj, partner1, partner2):
    from pymolplugins.interactions.controller import pp_interact_run
    group = "rfd4_contacts_" + uuid.uuid4().hex[:10]
    result = pp_interact_run(preset, source=obj,
                             partner1="(%s) and (model %s)" % (partner1, obj),
                             partner2="(%s) and (model %s)" % (partner2, obj),
                             state=1, group=group)
    return group, result


def clear_contacts(group):
    from pymolplugins.interactions.controller import pp_interact_clear
    return pp_interact_clear(group)


def select_sequence(obj, query):
    from pymolplugins.alignment.sequence import pp_select_sequence
    name = "rfd4_sequence_" + uuid.uuid4().hex[:10]
    result = pp_select_sequence(objects=obj, queries=query, selection_name=name,
                                state=1, gap_mode="exact", occurrence="all", visualize=0)
    return name, result

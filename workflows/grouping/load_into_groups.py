import pymol
from pymol import cmd

#%%
def split_prefix_identifier(full_name, methods="split_lastn_sep", **params):
    """
    Splits a prefix identifier into its components.

    Args:
        prefix_identifier (str): The prefix identifier to split.

    Returns:
        tuple: A tuple containing the prefix and the identifier.
    """
    if methods == "split_lastn_sep":
        return split_lastn_sep(full_name, **params)
    elif methods == "split_firstn_sep":
        return split_firstn_sep(full_name, **params)
    
def split_firstn_sep(full_name, sep="_", num=1):
    """
    Splits a string into components based on the first occurrence of a separator.

    Args:
        full_name (str): The string to split.
        sep (str): The separator to use for splitting.
        num (int): The number of components to return.

    Returns:
        tuple: A tuple containing the split components.
    """
    parts = full_name.split(sep, num)
    if len(parts) < num + 1:
        raise ValueError(f"Expected at least {num + 1} parts, got {len(parts)}: {full_name}")
    prefix, *idf = parts
    idf = sep.join(idf)
    return full_name,prefix, idf


def split_lastn_sep(full_name, sep="_", num=1):
    """
    Splits a string into components based on the last occurrence of a separator.

    Args:
        full_name (str): The string to split.
        sep (str): The separator to use for splitting.
        num (int): The number of components to return.

    Returns:
        tuple: A tuple containing the split components.
    """
    parts = full_name.rsplit(sep, num)
    if len(parts) < num + 1:
        raise ValueError(f"Expected at least {num + 1} parts, got {len(parts)}: {full_name}")
    prefix, *idf = parts
    idf = sep.join(idf)
    return full_name,prefix, idf


# %%
full_name = "pcna_scfv_011_run_451269_1_step_123_AB"
num=3


#%%
import itertools

def merge_groups(model_selection, prefix=None, methods="split_lastn_sep", **params):
    """
    Merges groups in PyMOL based on a prefix identifier.

    Args:
        model_selection (str): The selection string for the models to merge.
        group_name (str): The name of the group to create.
        prefix (str): The prefix to use for splitting identifiers.
        methods (str): The method to use for splitting identifiers.
        params: Additional parameters for the splitting method.
    """
    params = {}
    # first, get models to merge_groups
    models = [name for name in pymol.cmd.get_names("objects") if pymol.cmd.get_type(name) == "object:molecule"]
    print(models)
    if not models:
        print(f"No models found for selection: {model_selection}")
        return
    
    split_models = [ split_prefix_identifier(model, methods=methods, **params) for model in models]
    for group_name, group_models in itertools.groupby(split_models, key=lambda x: x[1]):
        full_names, _, _ = zip(*group_models)
        first, *rest = full_names
        try:
            cmd.alignto(target=first, selection=" or ".join(rest))
        except Exception as e:
            print(f"Error aligning group {group}: {e}")
        
        pymol.cmd.create(group_name, " or ".join([model for model in full_names]))
        
        cmd.delete(" or ".join(full_names))
        
def align_in_groups(selection):
    groups = pymol.cmd.get_names(selection)
    groups = [grp for grp in groups if pymol.cmd.get_type(grp) == "object:group"]
    
    for group in groups:
        try:
            first_obj = pymol.cmd.get_object_list(group)[0]
            cmd.alignto(target=first_obj, selection=group)
        except Exception as e:
            print(f"Error aligning group {group}: {e}")


cmd.extend("merge_groups", merge_groups)

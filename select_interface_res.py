from pymol import cmd, stored


def interfaceResidues_per_chain(cmpx, cutoff=1.0):
    stored.r, stored.j, rVal, seen = [], [], [], []
    cmd.get_area(cmpx, load_b=1)
    cmd.select(f"interface_bydist_{cmpx}", "none")
    for chain in cmd.get_chains(cmpx):
        cmd.create("remain_str", cmpx)
        cmd.extract("chain_str", f"remain_str and chain {chain}")
        cmd.alter("chain_str", "q=b")
        cmd.alter("remain_str", "q=b")
        cmd.get_area("remain_str", load_b=1)
        cmd.get_area("chain_str", load_b=1)
        cmd.alter("chain_str or remain_str", "b=b-q")
        cmd.do(
            f"select interface_bydist_{cmpx}, interface_bydist_{cmpx} or ((chain_str within 3.6 of remain_str) or (remain_str within 3.6 of chain_str)) "
        )
        cmd.iterate("chain_str", "stored.r.append((chain,resi,name,b));")
        for chain, resi, name, diff in stored.r:
            if abs(diff) >= float(cutoff):
                key = f"{resi}-{chain}-{name}"
                if key in seen:
                    continue
                else:
                    seen.append(key)
                rVal.append((chain, resi, name, diff))
    cmd.iterate(f"interface_bydist_{cmpx}", "stored.j.append((chain,resi,name));")
    return rVal, stored.j


def select_interface(cmpx, cutoff=1.0):
    itfc_atom, bydist_atom = interfaceResidues_per_chain(cmpx, cutoff)
    itfc_resi = []
    cmd.select(f"interface_byarea_{cmpx}", "none")
    for chain, resi, name, diff in itfc_atom:
        if (chain, resi) not in itfc_resi:
            itfc_resi.append((chain, resi))
            cmd.select(
                f"interface_byarea_{cmpx}",
                f"interface_byarea_{cmpx} or ({cmpx} and chain {chain} and i. {resi})",
            )

    cmd.select(f"interface_bydist_{cmpx}", "none")
    cmd.delete("remain_str chain_str")
    for chain, resi, name in bydist_atom:
        cmd.select(
            f"interface_bydist_{cmpx}",
            f"interface_bydist or ({cmpx} and chain {chain} and i. {resi})",
        )
        cmd.select(
            f"interface_{cmpx}", f"interface_byarea_{cmpx} and interface_bydist_{cmpx}"
        )


cmd.extend("select_interface", select_interface)

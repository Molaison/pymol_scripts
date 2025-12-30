from pymol import cmd

def draw_interchain_interactions(selections="all", distance_cutoff=3.5, salt_cutoff=4.0):
    '''
    DESCRIPTION

        绘制 selections 中不同链之间的氢键与盐桥（含磷酸化修饰）

    USAGE

        draw_interchain_interactions [selections], [distance_cutoff], [salt_cutoff]
    '''
    models = cmd.get_object_list(selections)

    for model in models:
        chains = cmd.get_chains(model)

        for i, chain1 in enumerate(chains):
            for chain2 in chains[i+1:]:
                sel1 = f'({model} and chain {chain1})'
                sel2 = f'({model} and chain {chain2})'

                # --- 氢键 ---
                hbond_name = f'{model}_{chain1}_{chain2}_hbonds'
                cmd.dist(hbond_name, f'{sel1} and donor', f'{sel2} and acceptor', cutoff=distance_cutoff, mode=2)
                cmd.dist(hbond_name, f'{sel2} and donor', f'{sel1} and acceptor', cutoff=distance_cutoff, mode=2)
                cmd.set('dash_color', 'cyan', hbond_name)
                cmd.set('dash_width', 2.0, hbond_name)
                cmd.hide('labels', hbond_name)

                # --- 盐桥 ---
                # 正电原子
                pos1 = f'''{sel1} and ((resn ARG and name NH1+NH2) or (resn LYS and name NZ))'''
                pos2 = f'''{sel2} and ((resn ARG and name NH1+NH2) or (resn LYS and name NZ))'''
                # 负电原子（含磷酸化）
                neg1 = f'{sel1} and ((resn ASP and name OD1+OD2) or (resn GLU and name OE1+OE2) or (resn SEP+TPO+PTR and name O1P+O2P+O3P+OP1+OP2+OP3))'
                neg2 = f'{sel2} and ((resn ASP and name OD1+OD2) or (resn GLU and name OE1+OE2) or (resn SEP+TPO+PTR and name O1P+O2P+O3P+OP1+OP2+OP3))'

                salt_name = f'{model}_{chain1}_{chain2}_saltbridges'
                cmd.dist(salt_name, pos1, neg2, cutoff=salt_cutoff)
                cmd.dist(salt_name, pos2, neg1, cutoff=salt_cutoff)
                cmd.set('dash_color', 'magenta', salt_name)
                cmd.set('dash_width', 2.0, salt_name)
                cmd.hide('labels', salt_name)

                print(f"Interchain: {chain1}-{chain2} in {model}")

# 注册命令与补全（启用参数补全）
cmd.extend("draw_interactions", draw_interchain_interactions)

cmd.auto_arg[0]['draw_interactions'] = [cmd.object_sc, "object_name", ""]

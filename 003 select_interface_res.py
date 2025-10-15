from pymol import cmd, stored
import math


def calculate_cb_distance_interface(complex_name, cb_distance_cutoff=8.0):
    """
    基于Cb原子距离计算界面残基
    对于甘氨酸使用CA原子
    """
    interface_residues = []
    chains = cmd.get_chains(complex_name)

    if len(chains) < 2:
        print("警告: 需要至少两条链来计算界面")
        return []

    print(f"使用Cb距离方法 (cutoff: {cb_distance_cutoff} Å)")

    for i, chain1 in enumerate(chains):
        for chain2 in chains[i + 1 :]:
            # 获取Cb原子位置，甘氨酸使用CA
            chain1_cb = []
            chain2_cb = []

            # 处理链1
            cmd.iterate_state(
                1,
                f"{complex_name} and chain {chain1} and n. CB",
                "chain1_cb.append((resi, x, y, z))",
                space=locals(),
            )
            cmd.iterate_state(
                1,
                f"{complex_name} and chain {chain1} and resn GLY and n. CA",
                "chain1_cb.append((resi, x, y, z))",
                space=locals(),
            )

            # 处理链2
            cmd.iterate_state(
                1,
                f"{complex_name} and chain {chain2} and n. CB",
                "chain2_cb.append((resi, x, y, z))",
                space=locals(),
            )
            cmd.iterate_state(
                1,
                f"{complex_name} and chain {chain2} and resn GLY and n. CA",
                "chain2_cb.append((resi, x, y, z))",
                space=locals(),
            )

            # 计算距离
            for resi1, x1, y1, z1 in chain1_cb:
                for resi2, x2, y2, z2 in chain2_cb:
                    distance = math.sqrt(
                        (x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2
                    )
                    if distance <= cb_distance_cutoff:
                        if (chain1, resi1) not in interface_residues:
                            interface_residues.append((chain1, resi1))
                        if (chain2, resi2) not in interface_residues:
                            interface_residues.append((chain2, resi2))

    print(f"Cb距离方法找到 {len(interface_residues)} 个界面残基")
    return interface_residues


def calculate_sidechain_contact_interface(complex_name, contact_distance_cutoff=4.0):
    """
    基于侧链原子close contact计算界面残基
    """
    interface_residues = []
    chains = cmd.get_chains(complex_name)

    if len(chains) < 2:
        print("警告: 需要至少两条链来计算界面")
        return []

    print(f"使用侧链接触方法 (cutoff: {contact_distance_cutoff} Å)")

    for i, chain1 in enumerate(chains):
        for chain2 in chains[i + 1 :]:
            # 选择侧链原子（排除主链原子）
            sidechain1 = f"{complex_name} and chain {chain1} and not (n. N+CA+C+O+H)"
            sidechain2 = f"{complex_name} and chain {chain2} and not (n. N+CA+C+O+H)"

            # 找到接触的原子
            contact_atoms1 = []
            contact_atoms2 = []

            cmd.iterate_state(
                1,
                f"{sidechain1} within {contact_distance_cutoff} of {sidechain2}",
                "contact_atoms1.append((chain, resi))",
                space=locals(),
            )
            cmd.iterate_state(
                1,
                f"{sidechain2} within {contact_distance_cutoff} of {sidechain1}",
                "contact_atoms2.append((chain, resi))",
                space=locals(),
            )

            # 添加到界面残基列表
            for chain, resi in contact_atoms1 + contact_atoms2:
                if (chain, resi) not in interface_residues:
                    interface_residues.append((chain, resi))

    print(f"侧链接触方法找到 {len(interface_residues)} 个界面残基")
    return interface_residues


def calculate_buried_sasa_interface(complex_name, sasa_cutoff=1.0):
    """
    改进的buried SASA计算方法
    """
    interface_residues = []
    chains = cmd.get_chains(complex_name)

    print(f"使用buried SASA方法 (cutoff: {sasa_cutoff} Å²)")

    # 计算整个复合体的SASA
    cmd.get_area(complex_name, load_b=1)

    # 存储复合体中每个残基的SASA
    complex_sasa = {}
    cmd.iterate(complex_name, "complex_sasa[(chain, resi)] = b", space=locals())

    # 计算每条链单独的SASA
    for chain in chains:
        cmd.create("temp_chain", f"{complex_name} and chain {chain}")
        cmd.get_area("temp_chain", load_b=1)

        chain_sasa = {}
        cmd.iterate("temp_chain", "chain_sasa[resi] = b", space=locals())

        # 计算SASA差值
        for resi in chain_sasa:
            complex_value = complex_sasa.get((chain, resi), 0)
            isolated_value = chain_sasa[resi]
            sasa_diff = isolated_value - complex_value

            if sasa_diff >= sasa_cutoff:
                interface_residues.append((chain, resi, sasa_diff))

        cmd.delete("temp_chain")

    print(f"Buried SASA方法找到 {len(interface_residues)} 个界面残基")
    return interface_residues


def calculate_interface_residues_enhanced(
    complex_name,
    method="combined",
    cb_distance_cutoff=8.0,
    contact_distance_cutoff=4.0,
    sasa_cutoff=1.0,
    distance_cutoff=3.6,
):
    """
    增强的界面残基计算函数，支持多种方法

    参数:
    method: str - 计算方法 ("cb_distance", "sidechain_contact", "buried_sasa", "distance", "combined")
    """
    print(f"\n=== 计算界面残基: {complex_name} ===")
    print(f"方法: {method}")

    interface_residues = set()
    results = {}

    if method in ["cb_distance", "combined"]:
        cb_residues = calculate_cb_distance_interface(complex_name, cb_distance_cutoff)
        results["cb_distance"] = cb_residues
        if method == "cb_distance":
            interface_residues.update(cb_residues)

    if method in ["sidechain_contact", "combined"]:
        contact_residues = calculate_sidechain_contact_interface(
            complex_name, contact_distance_cutoff
        )
        results["sidechain_contact"] = contact_residues
        if method == "sidechain_contact":
            interface_residues.update(contact_residues)

    if method in ["buried_sasa", "combined"]:
        sasa_residues = calculate_buried_sasa_interface(complex_name, sasa_cutoff)
        # buried_sasa返回包含SASA差值的元组，只取前两个元素
        sasa_residues_simple = [(chain, resi) for chain, resi, diff in sasa_residues]
        results["buried_sasa"] = sasa_residues
        if method == "buried_sasa":
            interface_residues.update(sasa_residues_simple)

    if method in ["distance", "combined"]:
        # 使用原始的距离方法
        distance_residues = []
        chains = cmd.get_chains(complex_name)
        for i, chain1 in enumerate(chains):
            for chain2 in chains[i + 1 :]:
                cmd.iterate(
                    f"{complex_name} and chain {chain1} within {distance_cutoff} of chain {chain2}",
                    "distance_residues.append((chain, resi))",
                    space=locals(),
                )
                cmd.iterate(
                    f"{complex_name} and chain {chain2} within {distance_cutoff} of chain {chain1}",
                    "distance_residues.append((chain, resi))",
                    space=locals(),
                )
        distance_residues = list(set(distance_residues))  # 去重
        results["distance"] = distance_residues
        if method == "distance":
            interface_residues.update(distance_residues)

    if method == "combined":
        # 组合方法：至少在两种方法中出现
        all_residues = set()
        method_counts = {}

        for method_name, residues in results.items():
            if method_name == "buried_sasa":
                residues = [(chain, resi) for chain, resi, diff in residues]
            for res in residues:
                all_residues.add(res)
                method_counts[res] = method_counts.get(res, 0) + 1

        # 至少在两种方法中出现的残基
        interface_residues = {res for res, count in method_counts.items() if count >= 2}

        print(f"\n方法统计:")
        for method_name, residues in results.items():
            if method_name == "buried_sasa":
                residues = [(chain, resi) for chain, resi, diff in residues]
            print(f"  {method_name}: {len(residues)} 个残基")
        print(f"  组合结果(≥2种方法): {len(interface_residues)} 个残基")

    return list(interface_residues), results


def select_interface_residues_enhanced(
    complex_name,
    method="combined",
    cb_distance_cutoff=8.0,
    contact_distance_cutoff=4.0,
    sasa_cutoff=1.0,
    distance_cutoff=3.6,
    visualize=True,
    color="red",
):
    """
    增强的界面残基选择函数
    """
    try:
        if not cmd.count_atoms(complex_name):
            print(f"错误: 对象 '{complex_name}' 不存在或为空")
            return

        interface_residues, results = calculate_interface_residues_enhanced(
            complex_name,
            method,
            cb_distance_cutoff,
            contact_distance_cutoff,
            sasa_cutoff,
            distance_cutoff,
        )

        if not interface_residues:
            print("未找到界面残基")
            return

        # 创建选择
        selection_name = f"interface_{method}_{complex_name}"
        cmd.select(selection_name, "none")

        for chain, resi in interface_residues:
            cmd.select(
                selection_name,
                f"{selection_name} or ({complex_name} and chain {chain} and i. {resi})",
            )

        print(f"\n已创建选择: {selection_name} ({len(interface_residues)} 个残基)")

        # 创建各个方法的单独选择
        if method == "combined":
            for method_name, residues in results.items():
                method_selection = f"interface_{method_name}_{complex_name}"
                cmd.select(method_selection, "none")

                if method_name == "buried_sasa":
                    residues = [(chain, resi) for chain, resi, diff in residues]

                for chain, resi in residues:
                    cmd.select(
                        method_selection,
                        f"{method_selection} or ({complex_name} and chain {chain} and i. {resi})",
                    )
                print(f"已创建选择: {method_selection} ({len(residues)} 个残基)")

        # 可视化
        if visualize:
            try:
                cmd.show("sticks", selection_name)
                cmd.color(color, selection_name)
                print(f"已将界面残基显示为 {color} 色棒状模型")
            except:
                print("警告: 可视化设置失败")

    except Exception as e:
        print(f"插件执行出错: {e}")
        import traceback

        traceback.print_exc()


def show_interface_help():
    """显示插件使用帮助"""
    help_text = """
=== PyMOL 界面残基选择插件帮助 ===

命令: select_interface_enhanced complex_name [, method] [, cb_distance_cutoff] [, contact_distance_cutoff] [, sasa_cutoff] [, distance_cutoff] [, visualize] [, color]

参数说明:
- complex_name: 复合体对象名称
- method: 计算方法 (默认: "combined")
  * "cb_distance": 基于Cb原子距离
  * "sidechain_contact": 基于侧链原子接触
  * "buried_sasa": 基于buried SASA
  * "distance": 基于原子距离 
  * "combined": 组合方法(≥2种方法一致)
- cb_distance_cutoff: Cb距离阈值 (默认: 8.0 Å)
- contact_distance_cutoff: 侧链接触距离阈值 (默认: 4.0 Å) 
- sasa_cutoff: SASA差值阈值 (默认: 1.0 Å²)
- distance_cutoff: 原子距离阈值 (默认: 3.6 Å)
- visualize: 是否可视化 (默认: True)
- color: 可视化颜色 (默认: "red")

使用示例:
select_interface_enhanced my_complex
select_interface_enhanced my_complex, cb_distance, 10.0
select_interface_enhanced my_complex, combined, 8.0, 4.5, 1.5, 4.0, True, blue

功能特点:
- 支持多种界面残基计算方法
- 组合方法提高结果可靠性
- 自动可视化界面残基
- 详细的统计信息输出
"""
    print(help_text)


cmd.auto_arg[0]["select_interface_enhanced"] = [cmd.object_sc, "object_name", ""]
cmd.auto_arg[1]["select_interface_enhanced"] = [
    lambda: cmd.Shortcut(
        ["combined", "cb_distance", "sidechain_contact", "buried_sasa", "distance"]
    ),
    "method",
    "",
]

# 添加增强版命令
cmd.extend("select_interface_enhanced", select_interface_residues_enhanced)
cmd.extend("interface_help", show_interface_help)


# 添加别名
cmd.extend("calc_interface", select_interface_residues_enhanced)
cmd.extend("find_interface", select_interface_residues_enhanced)

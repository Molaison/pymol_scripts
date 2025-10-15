from pymol import cmd
import re


def select_by_sequence(
    protein_obj_sele,
    queries,
    selection_name="selected_residues",
    visualize=True,
    case_sensitive=True,
    allow_gaps=False,
    separator="\\",
    color="red",
):
    """
    根据氨基酸序列选择蛋白质残基

    参数:
    protein_obj_sele: 蛋白质对象选择器
    queries: 要查找的序列，用分隔符分隔多个序列
    selection_name: 选择的名称，默认为"selected_residues"
    visualize: 是否可视化选择的残基，默认True
    case_sensitive: 是否区分大小写，默认True
    allow_gaps: 是否允许序列中有间隙，默认False
    separator: 分隔符，默认为"\\")
    color: 可视化颜色，默认为"red"
    """
    try:
        # 参数验证
        if not protein_obj_sele or not queries:
            print("错误: 请提供有效的蛋白质对象和查询序列")
            return

        # 获取蛋白质的残基信息
        selector = []
        protein_objs = cmd.get_names("objects", 0, f"{protein_obj_sele}")

        if not protein_objs:
            print(f"错误: 未找到匹配的蛋白质对象 '{protein_obj_sele}'")
            return

        print(f"找到蛋白质对象: {protein_objs}")

        # 解析查询序列
        if separator in ["\\n", "\\t", "\\r"]:
            separator = separator.replace("\\", "")
        queries_list = [q.strip() for q in queries.split(separator) if q.strip()]

        if not queries_list:
            print("错误: 未找到有效的查询序列")
            return

        print(f"查询序列: {queries_list}")

        total_matches = 0

        for protein_obj in protein_objs:
            print(f"\n处理蛋白质对象: {protein_obj}")

            # 检查对象是否存在
            if not cmd.count_atoms(protein_obj):
                print(f"警告: 对象 '{protein_obj}' 不包含原子，跳过")
                continue

            residues = cmd.get_model(f"{protein_obj} and n. CA").atom

            if not residues:
                print(f"警告: 对象 '{protein_obj}' 不包含CA原子，跳过")
                continue

            # get mapping between res_idx and chain
            res_per_chain = {}
            for res in residues:
                chain = res.chain if res.chain else "A"  # 处理无链名的情况
                if chain not in res_per_chain:
                    res_per_chain[chain] = []
                res_per_chain[chain].append(int(res.resi))

            # 确保残基按顺序排列
            for chain in res_per_chain:
                res_per_chain[chain].sort()

            print(
                f"每条链的残基数: {[(chain, len(residues)) for chain, residues in res_per_chain.items()]}"
            )

            # 获取序列信息
            seqs = []
            sequence = ""
            current_chain = ""

            try:
                fasta_str = cmd.get_fastastr(f"{protein_obj}")
                if not fasta_str:
                    print(f"警告: 无法获取对象 '{protein_obj}' 的序列信息")
                    continue

                for line in fasta_str.split("\n"):
                    line = line.strip()
                    if line.startswith(">"):
                        if sequence and current_chain:
                            seqs.append((current_chain, sequence))
                        sequence = ""
                        # 提取链名，通常在最后一个字符
                        current_chain = line[-1] if line else "A"
                        print(f"链: {current_chain}")
                    elif line:
                        sequence += line

                # 添加最后一个序列
                if sequence and current_chain:
                    seqs.append((current_chain, sequence))

            except Exception as e:
                print(f"错误: 获取序列信息失败 - {e}")
                continue

            if not seqs:
                print(f"警告: 对象 '{protein_obj}' 未找到有效序列")
                continue

            print(f"找到 {len(seqs)} 条链的序列")

            # 搜索每个查询序列
            for query_idx, query in enumerate(queries_list):
                if not case_sensitive:
                    query = query.upper()

                print(f"\n搜索序列 {query_idx + 1}: '{query}'")
                found_matches = False

                for chain, seq in seqs:
                    search_seq = seq if case_sensitive else seq.upper()

                    # 查找匹配
                    if allow_gaps:
                        # 允许间隙的模糊匹配
                        pattern = ".*".join(list(query))
                        matches = list(re.finditer(pattern, search_seq))
                    else:
                        # 精确匹配
                        start_pos = 0
                        matches = []
                        while True:
                            pos = search_seq.find(query, start_pos)
                            if pos == -1:
                                break

                            # 创建一个简单的匹配对象
                            class SimpleMatch:
                                def __init__(self, start_pos, end_pos):
                                    self._start = start_pos
                                    self._end = end_pos

                                def start(self):
                                    return self._start

                                def end(self):
                                    return self._end

                            matches.append(SimpleMatch(pos, pos + len(query)))
                            start_pos = pos + 1

                    for match in matches:
                        match_start = match.start()
                        match_end = match.end()

                        # 验证残基范围
                        if chain not in res_per_chain:
                            print(f"警告: 链 '{chain}' 不在残基映射中")
                            continue

                        if match_start >= len(res_per_chain[chain]) or match_end > len(
                            res_per_chain[chain]
                        ):
                            print(f"警告: 序列位置超出残基范围")
                            continue

                        start_residue = res_per_chain[chain][match_start]
                        end_residue = res_per_chain[chain][match_end - 1]

                        selection_str = f"({protein_obj} and resi {start_residue}-{end_residue} and chain {chain})"
                        selector.append(selection_str)

                        print(
                            f"  ✓ 在链 {chain} 找到匹配: 残基 {start_residue}-{end_residue} (序列位置 {match_start + 1}-{match_end})"
                        )
                        found_matches = True
                        total_matches += 1

                if not found_matches:
                    print(f"  ✗ 序列 '{query}' 未找到匹配")

        # 创建选择
        if selector:
            selection_command = " or ".join(selector)
            print(f"\n创建选择: {selection_name}")
            print(f"选择命令: {selection_command}")

            cmd.select(selection_name, selection_command)

            # 可视化选项
            if visualize:
                try:
                    cmd.show("sticks", selection_name)
                    cmd.color(color, selection_name)
                    print(f"已将选择的残基显示为 {color} 色棒状模型")
                except:
                    print("警告: 可视化设置失败")

            print(
                f"\n总结: 共找到 {total_matches} 个匹配，已保存为选择 '{selection_name}'"
            )
        else:
            print("\n未找到任何匹配的序列")

    except Exception as e:
        print(f"插件执行出错: {e}")
        import traceback

        traceback.print_exc()


def show_help():
    """显示插件使用帮助"""
    help_text = """
=== PyMOL 序列选择插件帮助 ===

命令: sbs protein_obj_sele, queries [, selection_name] [, visualize] [, case_sensitive] [, allow_gaps] [, separator] [, color]

参数说明:
- protein_obj_sele: 蛋白质对象名称或选择器
- queries: 要查找的氨基酸序列，多个序列用分隔符分隔
- selection_name: 选择结果的名称 (默认: "selected_residues")
- visualize: 是否可视化 (默认: True)
- case_sensitive: 是否区分大小写 (默认: True)
- allow_gaps: 是否允许序列间隙 (默认: False)
- separator: 分隔符 (默认: "\\")
- color: 可视化颜色 (默认: "red")

使用示例:
sbs my_protein, LLFGYPVYV
sbs my_protein, LLFGYPVYV\\MGKSL, my_selection, True, False, False, \\, blue
sbs *, LLFGYPVYV, all_matches

功能特点:
- 支持多个蛋白质对象同时搜索
- 支持多个序列同时搜索
- 自动可视化匹配的残基
- 详细的错误处理和日志输出
- 支持模糊匹配(允许间隙)
"""
    print(help_text)


cmd.auto_arg[0]["sbs"] = [cmd.object_sc, "object_name", ""]
cmd.auto_arg[1]["sbs"] = [
    lambda: cmd.Shortcut(["LLFGYPVYV", "MGKSL", "AAAAAA"]),
    "query_sequences",
    "",
]

# 添加帮助命令
cmd.extend("sbs_help", show_help)
cmd.extend("sbs", select_by_sequence)

# 添加别名
cmd.extend("select_sequence", select_by_sequence)
cmd.extend("seq_select", select_by_sequence)

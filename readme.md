## Useful Pymol Scripts

### Screen Boosting (screen_boost.py)

​	该脚本实现了一些快捷键用以加速蛋白质设计的筛选的过程, 其主要功能如下: 

- `loadpath path,regex_pattern`: 该命令用于批量导入蛋白质结构文件至pymol中. 其中`path`为结构文件所在路径; `regex_pattern`用正则表达式指定哪些文件应当被导入, 默认的值为`^.+pdb$`, 即导入所有PDB文件, 不会正则表达式可以询问ChatGPT.
-   `setup_loader path, pattern, group_size`: 该命令用于分组导入指定路径下结构文件. `group_size`用于指定一次导入文件的数量. 可以通过`setup_loader path=<yourpath>`或是`setup_loader pattern=<yourregex>`指定单项参数设置.
  - 使用`next_bucket`命令或`CTRL-right`(control键+→键)查看下一组, 使用`prev_bucket`命令或`CTRL-left`(control键+←键)查看上一组.
- `step_backward, step_forward`(←&→键): 查看上一个或下一个PDB文件.
- `save_selected`(CTRL+SHIFT+S): 保存当前选中的PDB文件到`<current_path>/selected`子目录. 使用`loadpath`或`setup_loader`会将`current_path`设为指定的路径, 否则默认为当前工作目录(即打开pymol的目录)

### Select Interface Residues (select_interface_res.py)

​	该脚本的主要作用是使用PyMOL来计算和选择分子复合物（如蛋白质复合物）中各链之间的界面残基, 其用法如下:

```pymol
# 加载脚本
run select_interface_res.py

# 选择界面残基
select_interface your_complex_name, area_diff_cutoff, distance_cutoff
```

​	其结果为:

- `interface_by_distance_{your_complex_name}`: 根据距离筛选出来的`Interface Residue`, 即在两条不同的链间, 距离(定义为所有原子对距离中最小的值)小于`distance_cutoff`的任意残基.
- `interface_by_area_{your_complex_name}`: 根据面积差筛选出来的`Interface Residue`, 即在两条不同的链间, `SASA`差值大于`area_diff_cutoff`的任意残基.
- `interface_{your_complex_name}`: 以上两种筛选方法的交集, 即最终的`Interface Residue`.
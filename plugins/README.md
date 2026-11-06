# Plugin Manager 入口

在 PyMOL 的 Plugin 菜单打开 **PymolPlugins: Structure Annotator** 或 **PymolPlugins: Batch Processing**；首次安装见 [首页](../readme.md#安装)。本目录的脚本保留 Plugin Manager 入口，并调用 `pymolplugins/` 下的实现。

打开面板不会自动重连历史数据源；选择 **Data → Continue last review** 才恢复最近审阅。状态、标注和历史与源文件分开保存；审阅命令可以执行 PyMOL 操作，只应使用可信数据集的命令配置。完整使用和存储细节见 [Structure Annotator 指南](../docs/structure_annotator_csv_walkthrough.md)。

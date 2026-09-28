# 待导入图标

把 Visio `.vss` 模具中的图元导出为 SVG 后放在这里。项目中的 `tools/ExportVisioMastersToSvg.bas` 已将输出目录配置到本目录。

导入步骤：在 Visio 中打开 `收藏夹.vss` 和一个可编辑的空白绘图，将宏代码导入 VBA 模块后运行 `ExportAllMasters`。导出后把需要的 SVG 登记到 `lib/floor_plan_symbol_catalog.dart` 的 `kFloorPlanSymbols` 中，再运行 Flutter 测试。

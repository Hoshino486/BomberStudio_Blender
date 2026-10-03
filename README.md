# BomberStudio Blender Bridge

**把 BomberStudio 导出的模型、材质和物理缓存接入 Blender。**

中文面板 · ASCII / Binary FBX · 模型整理 · 角色工具 · 贴图预览 · PBR 图集

---

## 下载：所有已支持版本 ZIP

**直接安装这个 ZIP，不用解压。**
Windows x64 已测试：**2.79b、3.2.2、4.0.2、4.1.1、4.5.5 LTS、5.2.2 LTS**。
2.79～5.2 之间未列出的具体版本属于兼容设计，尚未逐版验证；2.78 及更早版本、未来版本和其他操作系统不在当前实测范围内。

📖 [详细使用教程](./docs/使用指南.md) · [安装包验证记录](./docs/兼容验证.md) · [ASCII FBX 说明](./docs/ASCII_FBX.md) · [更新记录](./CHANGELOG.md)

## 功能一览

| 模块 | 主要功能 |
|---|---|
| **模型导入 / 导出** | FBX、OBJ、glTF；多文件导入；FBX 动画、骨架和形态键；可选嵌入贴图 |
| **ASCII FBX 兼容** | 自动识别 FBX 7.1～7.7 文本格式，生成二进制缓存后导入；兼容混合 Root / Null 骨骼链 |
| **BomberStudio 材质对接** | 读取材质旁文件，恢复基础贴图、UV 层、平铺、偏移、Wrap 和 Alpha 使用方式 |
| **快速贴图** | 扫描贴图 / `DedupedTextures`、LOD 筛选、图片预览、应用基础色、修复丢失图片路径 |
| **贴图图集** | 材质勾选、UV 边界裁剪、尺寸统一、间距与边缘扩展；二叉树 / 行架打包；PNG / TGA |
| **PBR 图集** | 可输出 Base Color、Roughness、Metallic、Normal、Emission 五个通道 |
| **模型整理** | 位置 / 旋转归零、删除孤立点、自定义法向清理、相同材质合并、网格连接与分割 |
| **顶点组** | 清理、排序、数字补号、合并同前缀、按位置匹配改名、模型名前缀、基础骨架生成 |
| **形态键与法线** | 保拓扑修改器应用、形态键静态快照、平滑法线写 UV、TANGENT / COLOR 兼容数据 |
| **角色工具** | 合并骨架、附加网格、骨骼父级、身高缩放、MMD 基础整理和局部物理清理 |
| **口型 / 眼球** | 从 AA / OH / CH 生成 15 个 `vrc.v_*` 口型；眼骨创建、预览与复位辅助 |
| **物理缓存** | 读取 BomberStudio NoWind PC2 缓存，校验文件、顶点、帧数和 SHA256 |
| **诊断 / 更新** | 日志、诊断 JSON、`.blend` 备份、本地 ZIP 更新与回滚、手动检查 GitHub Release |

### 与其他工具的关系

- 这是 **BomberStudio 导出数据的 Blender 后处理插件**，不是在 Blender 中直接解包 Unity 资源的工具。
- PMX / PMD 导入需要对应版本的 `mmd_tools`；VRM 导入需要对应的 VRM 导入器。
- 其他格式依赖当前 Blender 提供或启用的对应导入器。
- 游戏专有 Shader、原生物理效果与 VRChat SDK 配置不由本插件完整重建。

## 安装

所有步骤都选择同一个文件：**`BomberStudio_Blender`**。

| Blender | 安装入口 | 面板位置 |
|---|---|---|
| **2.79** | 文件 → 用户设置 → 插件 → 从文件安装 | 3D 视图左侧 **T** 工具架 → BomberStudio |
| **2.80～4.1** | 编辑 → 首选项 → 插件 → 安装 | 3D 视图右侧 **N** 侧栏 → BomberStudio |
| **4.2+** | 编辑 → 首选项 → 插件 / 获取扩展 → 右上角菜单 → **从磁盘安装** | 3D 视图右侧 **N** 侧栏 → BomberStudio |

1. 下载上方 `Universal.zip`，**不要选 GitHub 的 “Code → Download ZIP” 源码压缩包**。
2. 保存正在编辑的 `.blend`。
3. 根据表格打开安装入口，选择下载的 ZIP。
4. 搜索 **BomberStudio Blender Bridge** 并启用；按需保存首选项。
5. 回到 **3D 视图**，按 N；Blender 2.79 按 T。
6. 展开 **BomberStudio · 快速访问**。
7. 首次使用先打开 **设置与诊断**，检查“临时 / 日志 / 备份目录”和“图集输出目录”，选择本机可写的工作文件夹。

成功标志：看到 **导入 BomberStudio 模型**、**导入 BomberStudio PC2 物理缓存**、**导出 FBX** 等按钮。

**从 1.0.0 / 1.0.1 更新：**可在插件“检查版本更新”中选择“从本地 ZIP 安装更新（保留备份）”，选同一份 Universal ZIP，完成后保存工作并重启 Blender。不要同时启用旧 Legacy 副本和新 Extension 副本；更换安装类型前先停用旧副本。

## 快速上手

### 1. 导出并保留目录结构

在 BomberStudio 导出模型、贴图以及可用的材质描述文件。不要把模型与贴图拆散到不相关的目录。

```text
角色A/
├── 角色A.fbx
├── 角色A.fbx.genshin.json   # 如导出器生成此旁文件，则一起保留
├── Materials/              # 如有，则保留
└── Body_Diffuse.png
```

支持按导出结果读取 `.genshin.json`、`.starrail.json`、`.hi3.json`、`.zzz.json` 等旁文件；不是每个模型都必然具有它们。

### 2. 导入并检查

1. 点击 **导入 BomberStudio 模型**，选择 FBX。
2. 首次导入保持比例为 `1`，不要随意打开“自动骨骼方向”。
3. ASCII FBX 会自动转换；原文件不覆盖、不移动。
4. 检查大纲中的网格、骨架、形态键和动画。
5. 使用材质预览检查贴图；Blender 2.79 的节点材质使用 Cycles。
6. 有缺图或材质问题时，查看临时目录中的 `last-import.json` 与 `bomberstudio.log`。

**ASCII 自动转换只在 BomberStudio 的导入入口生效。**Blender 自带 `文件 → 导入 → FBX` 不会自动调用本插件；自带入口应选择已经转换的二进制副本。

### 3. 整理模型或生成图集

1. 先保存 `.blend` 备份。
2. 只选择要处理的网格；最后选中的物体是活动物体。
3. 根据需要清理顶点组、合并相同材质，或处理角色骨架。
4. 制作图集时：**生成材质列表 → 勾选材质 → 设置尺寸 / 输出目录 → 合并贴图**。
5. 默认创建图集副本并隐藏原模型；检查结果后再导出。

> 不要把所有“模型处理”按钮依次点击一遍。位置归零、删除顶点组、分割模型等是不同用途的操作，未必适合当前模型。

### 4. 导出

在设置中确认动画、全部 Action / NLA、嵌入贴图等选项，再点击 **导出 FBX**。需要蒙皮时，同时选择骨架与对应网格。

FBX 导入默认动画帧偏移为 `1`；做 Blender 自身的原帧数导出 / 重导入比较时使用 `0`。

## 适配说明

同一个 ZIP 的结构：

```text
BomberStudio_Blender_1.0.2_Universal.zip
└── bomberstudio_blender/
    ├── __init__.py              # 2.79+ Add-on 入口，按运行版本适配
    ├── blender_manifest.toml    # 4.2+ Extension 清单
    ├── compat.py               # API 兼容层
    ├── ascii_fbx.py             # ASCII FBX 解析与转换
    ├── bridge.py               # 模型、旁文件、缓存与导出
    ├── materials.py / atlas.py
    ├── model_ops.py / rig_ops.py
    └── ...
```

旧版忽略扩展清单；新版扩展安装器读取清单。清单中的 `blender_version_min = "4.2.0"` **只描述 Extension 安装机制**，不代表同包的旧版 Add-on 入口失去 2.79～4.1 支持。

插件兼容代码使用 Python 3.5 可解析的语法，并处理注册方式、集合 / 选择、UV、材质 Alpha、顶点颜色和骨骼选择等 API 差异。安装插件不需要额外安装系统 Python、FBX SDK 或第三方 DLL。

## 已知范围与限制

- **版本 / 系统：**当前实测限上述六个 Windows x64 版本；未逐个认证全部中间版本，也未在 macOS / Linux 上实测。
- **ASCII FBX：**支持格式版本 7100～7700；单文件上限 512 MiB；6.x 不在适配范围内。
- **材质：**恢复已提供的贴图和材质描述，不自动补出模型未携带的图片。
- **图集：**适用于直接图片 / 常量驱动的常规 Principled 材质；UDIM、程序纹理、复杂混合、旋转 Mapping、多 UV 域和专有 Shader 需要先处理或烘焙。
- **PBR：**不会仅凭贴图名称把游戏遮罩或数据通道当成标准金属度 / 粗糙度。
- **形态键：**修改器应用限保拓扑形变；改变顶点数量或顺序的修改器不属于该功能的保留范围。
- **角色工具：**基础骨骼生成不是人体自动绑定；口型来自已有形态键；眼球功能是 Blender 端辅助，Unity / VRChat SDK 仍需另行设置。
- **动画：**源软件特有约束、控制器和 Shader 不等于 FBX 动画；关键动画应在源端烘焙并检查结果。
- **MMD：**内置的是基础整理，不等同于 Cats 的全部历史 Fix Model 行为。

## 常见问题

<details>
<summary><strong>为什么还是提示 ASCII FBX 不受支持？</strong></summary>

确认启用的是 1.0.1 或更新版本，更新后重启 Blender，并使用 **BomberStudio → 导入 BomberStudio 模型**，而不是 Blender 自带的 FBX 菜单。保留原文件；排查时查看 `last-import.json` 的 `fbx_conversion`。

</details>

<details>
<summary><strong>一个包是否意味着所有 Blender 都能保证使用？</strong></summary>

不是。统一的是下载文件和源码，安装入口仍随 Blender 版本变化。具体已验证版本见 [兼容验证](./docs/兼容验证.md)；未测试的历史版本、未来版本和其他系统不写成已支持。

</details>

<details>
<summary><strong>安装后找不到面板，或提示重复注册？</strong></summary>

确保当前区域是 3D 视图，2.80+ 按 N，2.79 按 T。检查插件是否启用；如果旧 Legacy 和新 Extension 同时存在，停用多余副本，保存工作并重启。

</details>

<details>
<summary><strong>贴图丢失、模型发紫或透明怎么办？</strong></summary>

先检查导出目录中是否真的有图片和材质旁文件。设置正确贴图目录，读取图片，再使用路径修复；数据 Alpha 导致透明时检查“忽略基础色 Alpha”。同名图片歧义、缺图和 UV 不匹配会写入日志。

</details>

<details>
<summary><strong>文件保存到哪里？插件是否上传模型？</strong></summary>

路径由当前场景的 **设置与诊断** 决定。通用安装在 Blender 用户配置目录下使用 `bomberstudio/临时文件夹` 与 `bomberstudio/输出`；开发机器已有指定工作目录时保留该目录。可以改成自己的工作文件夹。

模型、图片、ASCII 转换、图集与缓存处理在本地进行，不上传模型。网络功能只有用户主动触发的 GitHub Release 版本检查；没有自动后台下载或静默更新。

</details>

## 测试与源码

- `bomberstudio_blender/`：插件源码。
- `tests/`：Python 单元测试与 Blender 集成测试。
- `tools/`：确定性打包和验证脚本。
- `docs/`：使用指南、兼容记录与 ASCII FBX 说明。
- `SHA256SUMS.txt`：当前统一安装包的 SHA256。

安装包以真实 Blender 进程完成安装、启用、禁用与重新启用检查；功能测试包含 ASCII FBX、形态键、骨骼、UV、图集、旁文件与缓存。历史 1.0.1 的功能回归与本版统一包的安装结果分别记录，避免把旧数据冒充新运行。

开发者可在项目根目录执行（打包工具使用 Python 3.10+，开发实测 Python 3.13）：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python tools/build_release.py
```

Blender 测试需要先在 `tools/run_matrix.py` 配置本机可执行文件路径，再运行：

```bash
python tools/test_universal.py
```

测试脚本有的还使用开发机模型样本；未提供样本时，相关未运行项目应明确标为未运行。详见 [兼容验证](./docs/兼容验证.md)。

## 反馈问题

提交 Issue 时请附上：

1. 完整 Blender 版本、操作系统、插件版本和 ZIP 文件名。
2. 使用的安装入口与具体操作步骤。
3. 完整错误信息，以及可分享的 `last-import.json` / `diagnostics.json` 日志。
4. 最小复现文件或文件格式说明；分享前检查日志中的本机路径和资产信息。

## 参考与许可

- [MIMIBlender](https://github.com/StarBobis/MIMIBlender)：模型处理、贴图和图集工作流参考。
- [Cats Blender Plugin](https://github.com/teamneoneko/Cats-Blender-Plugin)：角色处理和面板工作流参考。
- 插件源码按 **GPL-3.0-or-later** 发布，见 [LICENSE.txt](./LICENSE.txt)。
- 不包含上述参考插件的完整代码、游戏模型、贴图、Blender 程序或第三方 FBX SDK。

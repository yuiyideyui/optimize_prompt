# 🎬 Director Table (Card Manager)

**节点类名**: `DirectorTableNode`  
**显示名称**: `🎬 Director Table (Card Manager)`  
**分类**: `my_comfyui/director`

## 功能说明

Director Table 节点用于管理和加载**导演卡片表**（包含图像、角色标签、布局信息等元数据的卡片集合）。该节点将 JSON 格式的导演数据转换为 ComfyUI 兼容的图像批次和元数据，支持多种图像缩放策略。适用于：

- 多角色/多场景管理
- 图像布局和排列
- 角色标签和分类
- 批量图像加载和处理
- 导演表工作流集成

## 输入参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `director_data` | STRING | `[]` | JSON 字符串，包含卡片数据、图片路径、角色标签等（支持多行） |
| `batch_resize_mode` | 选择 | `center_crop` | 图像缩放模式：`center_crop`（中心裁剪）、`stretch`（拉伸）、`letterbox`（等比缩放+补黑边） |

### director_data JSON 格式

```json
[
  {
    "filename": "path/to/image1.png",
    "role": "主角"
  },
  {
    "filename": "path/to/image2.jpg",
    "role": "配角"
  },
  {
    "filename": "/absolute/path/to/image3.png",
    "role": "NPC"
  }
]
```

### 字段说明

- **filename**: 图像文件路径（支持相对路径和绝对路径）
- **role**: 角色标签或分类名称（字符串，用于后续识别和分组）

### 缩放模式

| 模式 | 说明 | 适用场景 |
|------|------|--------|
| `center_crop` | 中心裁剪到目标尺寸 | 需要固定尺寸且允许丢弃边缘内容 |
| `stretch` | 拉伸/压缩到目标尺寸 | 可以扭曲纵横比以填充空间 |
| `letterbox` | 等比缩放 + 黑边补齐 | 保持原始纵横比，四周补黑边 |

## 输出说明

| 输出名 | 类型 | 说明 |
|--------|------|------|
| `images` | IMAGE | 合并后的图像批次（形状：`[N, H, W, 3]`） |
| `count` | INT | 加载的有效图像数量 |
| `roles_summary` | STRING | 角色标签摘要（逗号分隔） |
| `image_1` ~ `image_9` | IMAGE | 单个图像输出（已加载的）；未加载的为 `None` |
| `image_metadata` | STRING | 完整的图像元数据 JSON |

### 输出格式详解

#### images（图像批次）
```
形状: [N, H, W, 3]  （其中 N 为图像数量，H/W 为统一后的尺寸）
值域: [0.0, 1.0]    （浮点数，标准 ComfyUI 格式）
```

#### image_metadata（元数据 JSON）
```json
{
  "version": 1,
  "resize_mode": "center_crop",
  "canvas": [512, 512],
  "items": [
    {
      "index": 1,
      "top": 0,
      "left": 0,
      "height": 512,
      "width": 512
    },
    {
      "index": 2,
      "top": 10,
      "left": 20,
      "height": 480,
      "width": 512
    }
  ]
}
```

**元数据字段**：
- **version**: 元数据格式版本
- **resize_mode**: 使用的缩放模式
- **canvas**: 统一后的画布尺寸 `[height, width]`
- **items**: 每个图像的位置和尺寸信息
  - **index**: 图像在批次中的位置
  - **top/left**: 图像在画布上的偏移坐标
  - **height/width**: 图像的实际尺寸

## 工作原理

1. **JSON 解析**：从输入字符串解析 JSON 卡片数据
2. **路径验证**：
   - 相对路径：相对于 ComfyUI 输入目录
   - 绝对路径：直接使用
   - 验证文件存在
3. **图像加载**：逐个加载图像文件，转换为 RGB 格式和浮点张量
4. **尺寸计算**：确定统一的画布尺寸（所有图像的最大宽高）
5. **尺寸适配**：根据选定的缩放模式将所有图像适配到统一尺寸
6. **批处理**：堆叠所有图像为批次，同时生成单个图像输出和元数据

## 使用方法

### 基础使用

1. 在 ComfyUI 节点菜单中找到 `my_comfyui/director` 分类
2. 添加 **Director Table** 节点
3. 准备 JSON 卡片数据（包含图像路径和角色标签）
4. 粘贴 JSON 到 `director_data` 文本框
5. 选择合适的 `batch_resize_mode`
6. 连接输出到后续节点

### 使用示例

**简单示例**

```json
[
  {"filename": "character1.png", "role": "主角"},
  {"filename": "character2.png", "role": "反派"}
]
```

**完整示例**

```json
[
  {"filename": "inputs/hero.jpg", "role": "主角-李明"},
  {"filename": "inputs/friend.jpg", "role": "配角-王芳"},
  {"filename": "inputs/villain.jpg", "role": "反派-张三"},
  {"filename": "/absolute/path/to/npc.png", "role": "群众"}
]
```

## 依赖要求

- **必需**:
  - Python 3.8+
  - ComfyUI
  - Pillow (PIL)
  - NumPy
  - PyTorch (`torch`)

- **安装**:
```bash
pip install pillow numpy torch
```

## 异常与限制

### 已知限制

1. **最多 9 个图像**：单个节点最多支持 9 个卡片（`image_1` 到 `image_9`）
2. **RGB 转换**：所有图像统一转换为 RGB，不支持 RGBA 或灰度
3. **路径包含**：相对路径必须在 ComfyUI 输入目录内（安全限制）
4. **无效卡片**：缺少 `filename` 的卡片会被跳过
5. **空结果**：如果没有有效的图像加载，返回虚拟 512×512 的黑色图像

### 常见错误

| 错误信息 | 原因 | 解决方案 |
|---------|------|--------|
| `Invalid director_data JSON` | JSON 格式错误 | 使用 JSON 验证工具检查格式 |
| `FileNotFoundError` | 图像文件不存在 | 检查文件路径是否正确 |
| `Unknown batch_resize_mode` | 缩放模式名称错误 | 使用 `center_crop`、`stretch` 或 `letterbox` |
| `Director Table supports at most 9 images` | 卡片数超过限制 | 分批处理或创建多个节点 |

## 工作流示例

### 示例 1：加载多角色卡片

```json
[
  {"filename": "inputs/sprites/hero_face.png", "role": "主角"},
  {"filename": "inputs/sprites/companion_face.png", "role": "同伴"},
  {"filename": "inputs/sprites/enemy_face.png", "role": "敌人"}
]
```

**参数**：
- `batch_resize_mode`: `center_crop`
- 输出：3 个图像的统一批次，用于后续模型处理

### 示例 2：带绝对路径的完整工作流

```json
[
  {
    "filename": "/mnt/data/characters/alice.jpg",
    "role": "Alice"
  },
  {
    "filename": "/mnt/data/characters/bob.jpg",
    "role": "Bob"
  },
  {
    "filename": "/mnt/data/characters/charlie.jpg",
    "role": "Charlie"
  }
]
```

**参数**：
- `batch_resize_mode`: `letterbox`（保留角色原始纵横比）
- 输出：包含完整元数据的批次，便于后续的位置感知处理

## 性能考虑

- **加载速度**：图像数量和尺寸影响加载时间
- **内存占用**：大分辨率图像会占用更多内存
- **缩放开销**：`letterbox` 模式计算最多，`stretch` 最快
- **最优配置**：
  - 限制图像数量（≤ 9）
  - 使用中等分辨率（512-1024 px）
  - 预存在相对路径中避免绝对路径查找

## 常见问题

**Q: 如何支持超过 9 个卡片？**  
A: 可以级联多个 Director Table 节点，分别处理不同批次的卡片。

**Q: 能否支持 RGBA 或其他格式？**  
A: 当前所有图像统一转换为 RGB。若需保留 Alpha 通道，需要修改源代码。

**Q: 缩放模式如何选择？**  
A: 
- 需要固定尺寸且允许裁剪 → `center_crop`
- 可以扭曲纵横比 → `stretch`
- 保留原始比例 → `letterbox`

**Q: 角色标签的用途是什么？**  
A: 角色标签用于标记和分类卡片，支持后续的工作流中进行选择性处理或分组操作。

**Q: 如何调试路径问题？**  
A: 
- 使用相对路径时，验证文件在 ComfyUI 输入目录中
- 使用绝对路径时，确保路径完整且文件存在
- 检查文件权限（ComfyUI 进程是否可读）

---

**最后更新**: 2026-10-19

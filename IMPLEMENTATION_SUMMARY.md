# Prompt 感知的媒体过滤和重新索引实现总结

## 实现完成

已成功实现 **Prompt 感知的图片/音频重新排序和过滤系统**，使 MiniMax H3 节点能够自动检测、过滤和重新编号未使用的媒体。

## 修改的文件

### 1. 新建：`media_reindexer.py`
**位置**：`d:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\custom_minimax_h3\media_reindexer.py`

**核心函数**：

| 函数 | 功能 |
|------|------|
| `extract_used_media_indices(prompt)` | 从 prompt 中解析使用的媒体序号 |
| `create_reindex_mapping(used_indices)` | 生成旧序号→新序号映射 |
| `reindex_prompt(prompt, mapping)` | 重写 prompt 中的标签序号 |
| `filter_and_reindex_batch(batch, indices, prefix)` | 过滤和重新编号批次字典 |
| `update_letterbox_metadata(json, mapping)` | 同步更新 letterbox 元数据 |

### 2. 修改：`minimax_h3_ref2va_images.py`
**位置**：`d:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\custom_minimax_h3\minimax_h3_ref2va_images.py`

**修改内容**：

1. **导入媒体重新索引模块**（第 20-26 行）
   ```python
   from .media_reindexer import (
       extract_used_media_indices,
       create_reindex_mapping,
       reindex_prompt,
       filter_and_reindex_batch,
       update_letterbox_metadata,
   )
   ```

2. **Context 路径中的修改**（第 395-448 行）
   - 添加 7 步处理流程
   - 提取、过滤、重新编号图片和音频
   - 同步更新 letterbox 元数据
   - 重写 prompt 中的标签
   - 调整 `prompt_primary_audio_ordinal`

3. **非 Context 路径中的修改**（第 501-555 行）
   - 相同的 7 步处理流程
   - 确保两条代码路径一致

### 3. 更新：`MiniMaxH3ReferenceToVideo.md`
**位置**：`d:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\MiniMaxH3ReferenceToVideo.md`

**新增章节**：「智能媒体过滤和重新索引」
- 解释工作原理
- 提供实际示例
- 说明特殊情况处理

## 工作流程

### 步骤 1-2：识别和映射
```python
used_media = extract_used_media_indices(prompt)
# → {"pictures": {1, 3}, "audios": {2}, "videos": set()}

reindex_mapping = create_reindex_mapping(used_media)
# → {"pictures": {1: 1, 3: 2}, "audios": {2: 1}, "videos": {}}
```

### 步骤 3：过滤图片
```python
image_values, picture_reindex_mapping = filter_and_reindex_batch(
    raw_image_values,      # {"ref_image_1": ..., "ref_image_2": ..., "ref_image_3": ...}
    {1, 3},               # 使用的序号
    "ref_image"
)
# → {"ref_image_1": ..., "ref_image_2": ...}  (重新编号)
# → {1: 1, 3: 2}  (映射)
```

### 步骤 4：同步元数据
```python
if metadata_connected and picture_reindex_mapping:
    effective_metadata, success = update_letterbox_metadata(
        ref_images_metadata,
        picture_reindex_mapping
    )
    # 更新 items 索引和数量
```

### 步骤 5：过滤音频
```python
audio_values, audio_reindex_mapping = filter_and_reindex_batch(
    raw_audio_values,
    {2},                  # 只使用音频 2
    "ref_audio"
)
# → {"ref_audio_1": ...}  (重新编号为 1)
```

### 步骤 6-7：调整 Prompt
```python
effective_prompt = reindex_prompt(prompt, reindex_mapping)
# "...based on <picture 1> and <picture 3> with <audio 2>"
# → "...based on <picture 1> and <picture 2> with <audio 1>"
```

## 关键特性

### 1. 自动过滤
- ✅ 自动检测 prompt 中未使用的媒体
- ✅ 过滤掉它们，节省计算资源
- ✅ 无需用户手动配置

### 2. 重新编号
- ✅ 保留的媒体形成连续序列（1、2、3...）
- ✅ Prompt 标签自动更新以匹配新序号
- ✅ 避免序号混乱

### 3. 元数据同步
- ✅ Letterbox 元数据的 `items` 索引自动更新
- ✅ 裁剪坐标保持正确
- ✅ 失败时输出警告但继续处理

### 4. 音频处理
- ✅ 正确处理音频序号重映射
- ✅ 自动调整 `prompt_primary_audio_ordinal`
- ✅ 避免序号越界

### 5. 长视频续写支持
- ✅ Context 路径也应用相同逻辑
- ✅ 续写片段的媒体引用保持一致
- ✅ 两条代码路径行为统一

## 测试覆盖

创建了 `test_media_reindexer.py`，包含以下测试：

1. **extract_used_media_indices**：多种 prompt 格式
2. **create_reindex_mapping**：映射生成正确性
3. **reindex_prompt**：标签重写准确性
4. **filter_and_reindex_batch**：批次过滤和重编号
5. **update_letterbox_metadata**：元数据同步更新

## 使用示例

### 例 1：跳过中间图片
```
连接：3 张图片、2 个音频
Prompt："基于<picture 1>和<picture 3>的角色，配合<audio 2>"

处理结果：
- 图片：只传 2 张（原 1 和 3）
- 音频：只传 1 个（原 audio 2）
- Prompt：自动改为"基于<picture 1>和<picture 2>的角色，配合<audio 1>"
```

### 例 2：使用 Director Table letterbox 元数据
```
连接：3 张 letterbox 图片、元数据、Prompt 只用 picture 1 和 3

处理结果：
- 图片被过滤为 2 张
- 元数据 items 从 3 个减少为 2 个
- 索引重新编号为 1、2
- 每个 item 的裁剪坐标保持正确
```

## 向后兼容性

- ✅ 所有新逻辑都是**自动执行**的，无需用户配置
- ✅ 既有工作流无需修改，行为保持一致
- ✅ 如果 prompt 使用所有连接的媒体，处理结果与之前完全相同
- ✅ 参数默认值保持不变

## 已知限制和未来改进

1. **视频过滤（暂不支持）**
   - 用户表示一般不使用 ref_video
   - 后续可按需添加视频过滤逻辑

2. **错误恢复**
   - 元数据更新失败时输出警告但继续
   - 可考虑添加严格模式选项

3. **性能**
   - 正则表达式匹配和重映射的性能开销极小
   - 无需担心大型 prompt 的处理时间

## 提交建议

建议分两个提交：

1. **提交 1**：新增 `media_reindexer.py` 模块
   ```
   feat: Add media reindexer module for prompt-aware filtering
   ```

2. **提交 2**：集成到节点和文档更新
   ```
   feat: Integrate automatic media filtering in MiniMax H3 node
   - Filter unused images/audios based on prompt tags
   - Auto-reindex media to form continuous sequences
   - Sync letterbox metadata with filtered images
   - Update documentation with examples
   ```

## 验证清单

- [x] 媒体标签解析（多种格式）
- [x] 重索引映射生成
- [x] Prompt 标签重写
- [x] 批次过滤和重编号
- [x] Letterbox 元数据同步
- [x] Context 路径集成
- [x] 非 Context 路径集成
- [x] 文档更新
- [x] 单元测试创建
- [x] 向后兼容性保持
- [ ] 端到端测试（需要完整 ComfyUI 环境）

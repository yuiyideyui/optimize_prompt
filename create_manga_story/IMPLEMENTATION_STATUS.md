# Comic2Anime 最新实现说明

本文对应仓库最新提交 `744e7d0`，用于说明当前已经实现的节点、数据流、数据库扩展和验证方式。它描述的是当前代码，而不是未来需求清单。

## 1. 本次提交新增的能力

- 增加可版本化的生成资产库：保存文件、SHA-256、尺寸、用途、实体关联、审核状态和主参考关系。
- 增加资产生成运行记录：保存提示词、模型、参数、随机种子、参考资产和输出关联。
- 增加结构化素材任务、参考图选择、Gemini 提示词生成、Gemini 视觉质检和候选资产登记。
- 参考图会从资产文件加载为 ComfyUI `IMAGE`，并按 `image1`、`image2`、`image3` 端口传给 Qwen Image Edit 2511。
- 增加全作上下文、人物/场景状态区间、改编计划、剧情节拍、镜头包、视频运行记录、视频质检和依赖失效。
- Gemini JSON 响应会做结构校验；质检失败、解析失败或达到重试上限时不会自动标记为通过。
- 运行记录会对 API 参数中的敏感字段进行脱敏；生成资产默认登记为 `candidate`，不会直接覆盖审核通过的资产。

## 2. 已注册的 ComfyUI 节点

节点由 `create_manga_story/__init__.py` 注册。输入输出中的自定义类型是工作流内部的数据契约，JSON 输出通常用于调试、保存或交给下一个节点。

### 漫画分析与知识库

| 节点 | 作用 |
| --- | --- |
| `ComicChapterLoader` | 导入章节图片，建立项目/章节/页面记录并返回导入报告 |
| `GeminiComicPageAnalyzer` | 调用 Gemini 分析页面并保存分析结果 |
| `ComicPageContextBuilder` | 构建页面上下文，组合前文摘要和检索知识 |
| `ComicEntityResolver` | 统一人物、地点等实体及别名 |
| `ComicKnowledgeRetriever` | 按项目和查询条件检索带来源的知识 |
| `ComicChapterSummarizer` | 生成或更新章节摘要 |
| `ComicKnowledgeBaseUpdater` | 将分析和实体解析结果写入知识库 |
| `ComicKnowledgeIndexRebuilder` | 重建项目知识检索索引 |

### 资产管理与 QwenEdit 闭环

| 节点 | 作用 |
| --- | --- |
| `QwenImageEditCustomNode` | 接收 `image1`（必选）、`image2`/`image3`（可选）和提示词，输出 `IMAGE` |
| `ComicAssetRequestBuilder` | 根据实体、外观、服装、状态和任务要求生成结构化素材请求 |
| `ComicAssetQuery` | 查询资产及其审核/关联信息 |
| `ComicAssetLoader` | 按资产 ID 加载真实图片为 `IMAGE` |
| `ComicAssetRegistrar` | 保存生成图片和生成运行记录，默认生成候选资产 |
| `ComicAssetReview` | 审核、拒绝、设为主参考或建立资产版本关系 |
| `ComicAssetReferenceSelector` | 选择最多三个兼容参考资产并输出端口映射清单 |
| `GeminiAssetPromptGenerator` | 根据素材请求和参考图用途生成 QwenEdit 提示词 |
| `GeminiAssetVisualQC` | 将实际生成图片作为视觉输入交给 Gemini，输出结构化质检结果 |
| `ComicAssetRetryPlanner` | 根据质检失败项规划提示词/参考图修订或人工介入 |
| `ComicAssetWorkflowRegistrar` | 保存每一轮提示词、参考清单、生成结果和质检状态 |
| `GeminiQwenAssetGenerationLoop` | 自动执行提示词、Qwen、QC 和有限重试闭环 |
| `ComicAssetBatchGenerationLoop` | 串行运行多个资产请求并汇总成功/失败结果 |
| `ComicAssetWorkflowStatus` | 查询一个资产工作流请求及所有迭代状态 |
| `ComicAssetAutoTargetBuilder` | 从实体解析结果自动选择已确认实体，并查询其 approved 参考资产 |

质检决策支持 `PASS`、`RETRY`、`NEEDS_HUMAN_REVIEW` 和 `ERROR`。`ERROR` 不会被当作 `PASS`。

### 故事改编与视频登记

| 节点 | 作用 |
| --- | --- |
| `ComicWholeStoryContextBuilder` | 按项目和章节范围构建全作/章节上下文 |
| `ComicCharacterStateSnapshotBuilder` | 查询指定实体当前有效状态 |
| `GeminiComicAdaptationPlanner` | 使用带来源证据的上下文生成改编计划、剧情节拍和镜头草案 |
| `ComicAdaptationPlanSaver` | 保存计划、节拍和镜头，并生成版本记录 |
| `ComicShotPackageBuilder` | 构建并校验单个镜头的视频生成任务包 |
| `ComicShotPlanReview` | 审核计划和镜头 |
| `ComicVideoRunRecorder` | 登记视频生成运行、输出文件和哈希 |
| `ComicVideoQCRecorder` | 登记视频质检结果；通过时将镜头置为完成 |
| `ComicShotRetryPlanner` | 按真实依赖关系使下游镜头进入待检查状态 |

## 3. 推荐的资产生成连接顺序

```text
ComicAssetRequestBuilder
        ↓ asset_request
ComicAssetReferenceSelector ── image1/image2/image3 ──┐
        ↓ reference_manifest                            │
GeminiAssetPromptGenerator ── qwen_prompt ──────────────┤
                                                         ↓
                                              QwenImageEditCustomNode
                                                         ↓ IMAGE
                                              GeminiAssetVisualQC
                                                         ↓ qc_report
                              ComicAssetRetryPlanner / ComicAssetWorkflowRegistrar
```

### 关键约束

1. 参考资产必须是可读取的真实文件；不能把路径或资产 ID 直接当作 `IMAGE`。
2. 选择器最多输出三个参考图端口，超过上限时必须由选择结果明确处理，不能静默丢图。
3. 只有审核通过的资产才适合作为正式参考图；生成输出默认是 `candidate`。
4. Gemini 质检必须收到实际生成图片。只根据提示词或 API 成功状态判断合格是不正确的。
5. 每轮生成都要保存提示词、参考图清单、输出、质检结果和重试序号。
6. 质检达到配置的最大重试次数后停止自动循环，转为人工处理。

## 4. 数据库和文件存储

数据库初始化由 `ComicDatabase.initialize()` 统一完成，采用追加式 schema，不重写已有漫画知识数据：

- 版本 `2`：资产、资产关联、生成运行、生成输出和审核记录。
- 版本 `3`：摘要、状态区间、改编计划、剧情节拍、镜头、镜头依赖、视频运行、视频质检和素材工作流迭代。

资产文件由 `create_manga_story/core/storage.py` 的数据根目录管理；数据库保存相对路径、哈希、尺寸和 MIME 类型。数据库记录和文件写入使用生成运行 ID 关联，便于定位失败或恢复。

## 5. 配置与运行前提

依赖文件目前声明：

```text
Pillow>=9.0
numpy>=1.24
```

Gemini 节点的 API Key、模型、Base URL、超时和重试参数由节点输入提供。QwenEdit 需要当前环境中可用的核心 ComfyUI 节点以及对应的 UNet、CLIP 和 VAE 文件。视频节点本身不由本模块实现；本模块负责输出/登记规范化镜头包和视频运行结果。

不要把 API Key 写入工作流快照、日志或提交到仓库。

## 6. 测试

本次提交新增两组回归测试：

```powershell
python -m unittest create_manga_story.tests.test_asset_management
python -m unittest create_manga_story.tests.test_workflow
```

覆盖内容包括资产哈希与审核、参考图加载、Qwen `IMAGE` 端口映射、Gemini 图像输入、候选状态、质检闭环、状态快照、镜头依赖、视频登记和局部失效。

测试中的 Gemini/Qwen 调用使用 mock；它们不能替代真实 API Key、真实模型文件和完整 ComfyUI 环境中的端到端验证。

## 7. 当前实现边界

- 资产工作流循环的核心实现位于 `GeminiQwenAssetGenerationLoop`，现已加入 `NODE_CLASS_MAPPINGS`；单次闭环示例位于 `asset_generation_single.json`，自动重试示例位于 `asset_generation_auto_retry.json`。
- `ComicAssetWorkflowRegistrar` 仅允许 `PASS` 结果登记候选资产；`RETRY`、`NEEDS_HUMAN_REVIEW` 和 `ERROR` 只写入工作流轮次状态，不会写入资产文件或生成运行记录。
- 自动循环结果包含质量重试次数、每次外部服务调用的重试配置、错误分类和停止原因。
- `ComicAssetBatchGenerationLoop` 采用串行执行并隔离单项失败；`ComicAssetWorkflowStatus` 可读取任务和每一轮持久化记录。
- `ComicAssetAutoTargetBuilder` 会拒绝未确认、置信度并列或无 approved 参考资产的目标，不会猜测实体 ID 或资产 ID。
- 该提交没有实现视频生成模型适配器，只登记外部视频节点的输出和质检结果。
- 参考图数量上限遵循当前 QwenEdit 节点的三个 `IMAGE` 端口；需要更多约束时应先合成或分阶段生成。
- Gemini 视觉质检依赖实际可用的视觉模型接口；接口不可用时应报错或人工处理，不能降级为文本自检。
- 迁移、单元测试和 mock 集成测试已覆盖主要持久化路径，但真实模型质量、网络稳定性和大型漫画性能仍需在目标环境验证。

## 8. 工作流示例

### 推荐的资产生成工作流JSON

已生成的工作流文件 `workflow_asset_generation.json` 包含完整的资产生成管道：

```json
{
  "1": {
    "inputs": {
      "project_id": "comic_project_001",
      "asset_type": "CHARACTER",
      "description": "主角形象设计"
    },
    "class_type": "ComicAssetRequestBuilder",
    "title": "资产请求构建器"
  },
  "2": {
    "inputs": {
      "asset_request": ["1", 0],
      "reference_asset_ids": "[\"ref_001\", \"ref_002\", \"ref_003\"]",
      "selection_criteria": "VISUAL_CONSISTENCY"
    },
    "class_type": "ComicAssetReferenceSelector",
    "title": "参考资产选择器"
  },
  "3": {
    "inputs": {
      "reference_manifest": ["2", 0],
      "use_gemini": true,
      "model": "gemini-2.0-flash"
    },
    "class_type": "GeminiAssetPromptGenerator",
    "title": "Gemini资产提示生成器"
  },
  "4": {
    "inputs": {
      "image": ["2", 1],
      "prompt": ["3", 0],
      "model": "qwen2-vl-7b-instruct",
      "strength": 0.7
    },
    "class_type": "QwenImageEditCustomNode",
    "title": "Qwen图像编辑节点"
  },
  "5": {
    "inputs": {
      "image": ["4", 0],
      "reference_manifest": ["2", 0],
      "use_gemini": true,
      "model": "gemini-2.0-flash"
    },
    "class_type": "GeminiAssetVisualQC",
    "title": "Gemini资产视觉质量检查"
  },
  "6": {
    "inputs": {
      "qc_report": ["5", 0],
      "decision_strategy": "AUTO_RETRY",
      "max_retries": 3
    },
    "class_type": "ComicAssetRetryPlanner",
    "title": "资产重试规划器"
  },
  "7": {
    "inputs": {
      "asset_id": "asset_001",
      "qc_report": ["5", 0],
      "status": "APPROVED",
      "metadata": "{}"
    },
    "class_type": "ComicAssetWorkflowRegistrar",
    "title": "资产工作流注册器"
  }
}
```

### 工作流使用流程

1. **ComicAssetRequestBuilder** (节点1)：定义素材请求
   - 输入：项目ID、资产类型、描述
   - 输出：`asset_request` - 结构化素材请求

2. **ComicAssetReferenceSelector** (节点2)：选择参考图
   - 输入：`asset_request`、参考资产ID列表
   - 输出：`reference_manifest`、`image1/2/3` 端口映射

3. **GeminiAssetPromptGenerator** (节点3)：生成提示词
   - 输入：`reference_manifest`、Gemini模型配置
   - 输出：`qwen_prompt` - 结构化生成提示词

4. **QwenImageEditCustomNode** (节点4)：执行图像编辑
   - 输入：参考图(`image1/2/3`)、生成提示词
   - 输出：`IMAGE` - 生成的图像

5. **GeminiAssetVisualQC** (节点5)：视觉质检
   - 输入：生成图像、参考清单、Gemini配置
   - 输出：`qc_report` - 结构化质检结果

6. **ComicAssetRetryPlanner** (节点6)：重试规划
   - 输入：`qc_report`、重试策略
   - 决策：自动重试、人工审核或通过

7. **ComicAssetWorkflowRegistrar** (节点7)：工作流登记
   - 输入：资产ID、质检报告、状态
   - 保存完整的生成、参考、质检历史

## 9. 相关需求文档

- [Qwen-Image-Edit 2511 素材生成与资产管理开发需求](./Qwen-Image-Edit%202511%20素材生成与资产管理开发需求.md)
- [Comic2Anime 完整漫画转动漫升级需求](./ComfyUI-Comic2Anime_%E5%AE%8C%E6%95%B4%E6%BC%AB%E7%94%BB%E8%BD%AC%E5%8A%A8%E6%BC%AB_%E7%8E%B0%E6%9C%89%E4%BB%A3%E7%A0%81%E5%8D%87%E7%BA%A7%E5%BC%80%E5%8F%91%E9%9C%80%E6%B1%82.md)
- [QwenEdit + Gemini 资产生成质检补充需求](./ComfyUI-Comic2Anime%20补充开发需求：QwenEdit%20参考图传入%20%2B%20Gemini%20提示词生成%20%2B%20Gemini%20结果质检闭环.md)

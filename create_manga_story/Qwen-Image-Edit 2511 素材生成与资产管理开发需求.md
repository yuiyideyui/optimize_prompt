# ComfyUI Comic2Anime：Qwen-Image-Edit 2511 素材生成与资产管理开发需求

> 目标：在现有 `ComfyUI-Comic2Anime` 插件中，为 Qwen-Image-Edit 2511 建立可复用、可追踪、可审核的素材生成流程。本文用于交给 AI 编码助手实施。请先检查现有仓库和节点实现，再按阶段开发，不要一次性重写整个插件。

---

## 1. 项目背景与边界

现有插件已经具备漫画导入、Gemini 页面分析、跨页上下文、SQLite 知识库、角色/场景实体整理、章节总结和基础文本检索等能力。当前还提供一个 Qwen Image Edit 自定义节点，注册在 `QwenImageEdit` 分类，输入一张必选参考图和最多两张可选参考图，并输出 ComfyUI `IMAGE`。

Qwen 节点依赖当前 ComfyUI 提供的原生节点，例如：

- `UNETLoader`
- `CLIPLoader(type="qwen_image")`
- `TextEncodeQwenImageEditPlus`
- `FluxKontextImageScale`
- `KSampler`

具体节点接口、模型文件、参数和版本兼容性必须以当前仓库及实际 ComfyUI 环境为准，不得假设所有安装环境完全一致。

### 本次开发范围

本次重点是**Qwen 素材生成前后的业务编排和资产管理**，包括：

1. 从 SQLite 知识库读取角色、角色外观版本、场景和漫画证据。
2. 组织素材生成请求和参考资产。
3. 保留现有 Qwen-Image-Edit 2511 推理节点的职责与输出接口。
4. 将生成的 `IMAGE` 保存为资产文件，并登记到 SQLite。
5. 将素材与角色、角色外观版本、场景、场景状态、页面、分格或剧情事件关联。
6. 支持素材查询、加载、版本管理、审核和后续工作流复用。

### 不在本次范围内

- 不实现 MiniMax H3 视频生成、视频续写或视频质检。
- 不实现完整动画分镜规划系统。
- 不实现 Character LoRA 训练。
- 不要求引入向量数据库。
- 不修改 ComfyUI 核心代码。
- 不强制开发自定义前端；优先使用 ComfyUI 原生节点界面。
- 不重写现有 Gemini 分析、RAG 或实体匹配业务逻辑。

---

## 2. 开发前必须先检查

在写代码之前，先检查并输出简短报告：

1. 当前插件真实目录结构、节点注册入口和节点分类。
2. 现有 SQLite 初始化方式、数据根目录配置（包括 `COMIC2ANIME_DATA_DIR`）和迁移策略。
3. 现有数据模型中 `characters`、`character_appearances`、`locations`、`location_states`、`pages`、`panels`、`story_events` 的字段与外键。
4. 当前 Qwen Image Edit 自定义节点的完整实现、输入/输出签名和节点注册位置。
5. 当前 ComfyUI 版本及上述原生节点的接口是否能从当前环境确认；若无法启动 ComfyUI，应明确说明，不能声称已完成实际环境验证。
6. 现有测试的运行方式和测试覆盖范围。

**兼容原则：** 复用现有数据模型、数据库连接、存储工具、配置和节点注册机制。先理解现状，再提出最小改动方案；不要创建第二个互不相干的数据库或重复的角色/场景表。

---

## 3. 核心设计原则

### 3.1 图片文件与数据库分离

- 图片文件存储在插件数据根目录下的资产目录中。
- SQLite 保存稳定资产 ID、相对路径、哈希、元数据、关联关系、生成记录和审核状态。
- 不把图片二进制直接存入 SQLite。
- 原始漫画图片不得被生成流程覆盖或修改。
- 数据库中优先保存相对资产根目录的路径；解析实际路径时必须验证路径位于配置的资产根目录内，防止路径穿越。
- 数据库或索引可重建不意味着原图可丢弃；资产文件与数据库都需要明确的备份/恢复策略。

默认目录可采用如下形式，但必须服从现有 `COMIC2ANIME_DATA_DIR` 配置：

```text
comic2anime_data/
├── comic2anime.sqlite3
├── assets/
│   ├── characters/
│   │   └── <character_id>/
│   │       └── <appearance_id>/
│   ├── locations/
│   │   └── <location_id>/
│   │       └── <location_state_id>/
│   ├── panels/
│   └── generated/
└── manifests/
```

不要将运行时资产、数据库、原始漫画和日志提交到 Git 仓库。

### 3.2 身份与外观分离

- `character_id` 表示角色身份。
- `appearance_id` 表示角色的某个外观版本，例如校服、便服、战斗服或特定剧情阶段。
- 角色换装不应创建新角色。
- 角色三视图、立绘、表情图等应尽可能绑定到具体 `appearance_id`，同时关联所属 `character_id`。
- 场景身份 `location_id` 与场景状态 `location_state_id` 分开，例如同一地点的白天、夜晚、雨天版本。
- 对于身份或外观存在歧义的情况，保留候选/待审核状态，不要自动覆盖已确认资料。

### 3.3 资产不是实体本身

一张图片是一条资产记录；角色、外观版本和场景是知识库实体。两者通过关联记录连接。不要将图片路径直接当作角色 ID，也不要把一张图硬编码为角色的唯一图片。

### 3.4 生成记录必须可追踪

每个生成结果应尽可能记录：

- 生成任务 ID。
- 输入参考资产 ID 列表及其用途/顺序。
- 生成提示词和提示词版本。
- 模型名称；能确认时记录模型版本。
- seed 和实际可获取的生成参数。
- 工作流或节点配置快照引用（若可安全、可靠地获取）。
- 生成时间、状态和错误信息。
- 输出资产 ID。
- 不可获取的字段应留空，不得伪造。

ComfyUI workflow 中可能包含敏感配置；保存快照前要剔除 API Key、令牌及其他秘密。

---

## 4. 建议的数据模型

先检查已有表和迁移机制，再以兼容现有项目的方式增加必要表。下列是逻辑要求，不代表必须逐字照搬字段名。

### 4.1 `assets`：资产主表

建议字段：

- `asset_id`：稳定、唯一的 ID。
- `project_id`：所属项目 ID。
- `asset_type`：例如 `character_turnaround`、`character_portrait`、`character_expression`、`character_pose`、`scene_reference`、`scene_state_reference`、`panel_reference`、`other`。
- `display_name`：便于在节点中识别的名称。
- `relative_path`：相对于资产根目录的路径。
- `file_hash`：文件内容 SHA-256。
- `mime_type`、`width`、`height`、`file_size`。
- `status`：例如 `candidate`、`approved`、`rejected`、`archived`。
- `metadata_json`：额外元数据，必须经 Schema/类型校验。
- `created_at`、`updated_at`。

建议索引：`project_id`、`asset_type`、`status`、`file_hash`。

不要仅凭文件哈希相同就合并不同语义用途的资产；可复用相同文件内容，但仍需保留必要的关联记录。

### 4.2 `asset_relations`：资产与知识库实体关联

逻辑字段：

- `asset_id`
- `entity_type`：受控枚举，例如 `character`、`character_appearance`、`location`、`location_state`、`page`、`panel`、`story_event`。
- `entity_id`
- `relation_type`：例如 `identity_reference`、`appearance_reference`、`turnaround_sheet`、`primary_reference`、`scene_reference`、`source_evidence`。
- `is_primary`
- `created_at`

注意：`entity_type + entity_id` 是多态关系，SQLite 无法对多张目标表自动实施统一外键。必须在业务层校验目标实体存在；如果现有架构更适合强外键，也可以采用多个专用关联表。禁止留下无法验证的孤儿关联。

必须定义唯一约束或等价幂等逻辑，避免同一资产、实体和关系被重复登记。对于“每个外观版本只能有一个首选三视图”等规则，应在事务中实现，并明确切换首选资产时如何取消旧首选。

### 4.3 `asset_generation_runs`：生成任务记录

建议字段：

- `run_id`
- `project_id`
- `status`：`pending`、`running`、`succeeded`、`failed`、`cancelled`（仅在确实支持取消时使用）。
- `asset_type`
- `prompt`
- `prompt_version`
- `model_name`、`model_version`（未知时为空）
- `seed`（未知时为空）
- `parameters_json`
- `reference_asset_ids_json`：保留输入参考图顺序；如需查询，可另建规范化子表。
- `workflow_snapshot_json` 或快照文件引用（可选，先做秘密信息清理）。
- `error_message`
- `started_at`、`finished_at`、`created_at`

如果一个生成任务可以输出多张图，建议增加 `asset_generation_outputs(run_id, asset_id, output_index)`，而不是假设一次任务只产生一张图片。

生成失败时保留失败任务记录，但不得生成指向不存在文件的成功资产记录。

### 4.4 `asset_reviews`：审核记录（推荐）

建议字段：

- `review_id`
- `asset_id`
- `review_status`：`approved`、`rejected`、`needs_revision`
- `review_notes`
- `reviewed_at`

如果已有通用审核/审计机制，优先复用，不要重复建设。

### 4.5 版本管理

- 每次生成都创建新的生成任务与候选资产；不得静默覆盖已有文件。
- 资产可以通过 `supersedes_asset_id` 或独立版本表关联前后版本。
- “首选素材”应通过明确字段或关联状态表达，并保证同一实体/用途不会出现多个冲突首选项。
- 审核失败的候选图可以保留并标记为 `rejected`，是否自动清理文件必须是显式策略。
- 重新生成角色外观资产时，旧的已确认素材继续可查询和回滚。

---

## 5. 节点设计

优先把资产管理节点放在清晰的独立模块中，例如 `nodes/asset_nodes.py`；数据库逻辑放在 `core/asset_repository.py` 或现有 repository 层；Schema 和模型放在现有模型模块。目录名称应服从仓库已有架构，不要机械新增重复层。

### 节点 A：Comic Asset Request Builder

**用途：** 根据知识库实体和素材目标，创建结构化的素材生成请求，帮助工作流选择正确的参考图与提示词。

输入建议：

- `project_id`
- `entity_type`：character / character_appearance / location / location_state / panel / story_event
- `entity_id`
- `asset_type`
- `generation_goal`：例如三视图、立绘、表情参考、场景参考图
- `reference_asset_ids`（可选；允许指定顺序）
- `prompt_override`（可选）
- `include_source_evidence`（布尔值）
- `allow_unapproved_references`（默认 false）

功能：

1. 从 SQLite 查询实体及其相关资料。
2. 检查项目归属和实体是否存在。
3. 汇总稳定设定、外观版本、相关页面证据与已确认参考资产。
4. 参考图优先选择已审核的正式资产；候选图只有在用户显式允许时才能作为输入。
5. 如果检索到多个冲突外观版本，不得静默挑选；输出警告或要求显式指定。
6. 输出结构化请求，包括目标实体 ID、资产类型、建议提示词、参考资产 ID、来源证据、警告和 Schema 版本。
7. 此节点不调用 Qwen 模型，也不写入生成结果。

建议输出自定义类型，例如 `COMIC_ASSET_REQUEST`。不要把请求整体转换成一个不透明字符串。若 ComfyUI 自定义类型需要用 Python 对象传递，应确保序列化、工作流重载和节点间传递稳定。

### 节点 B：现有 Qwen-Image-Edit 2511 节点

- 优先保留现有推理节点的输入/输出和职责。
- 不要把 SQLite 读写、实体匹配、资产审核逻辑直接塞进模型推理节点。
- 由工作流将请求节点生成的提示词和参考图连接到 Qwen 编辑节点。
- 如果当前 Qwen 节点不能直接接收结构化请求，应通过明确的适配节点提取需要的文本或参考图，而不是破坏现有接口。
- 不要假设 Qwen 节点可从数据库自动选择多张图片；只有在当前实现和模型节点接口确实支持时才使用该能力。
- 保留 ComfyUI 原生 `IMAGE` 输出，便于继续连接预览、保存和其他图像节点。

### 节点 C：Comic Asset Registrar

**用途：** 把生成后的图片文件和任务元数据正式登记到资产库。

输入建议：

- `image`：ComfyUI `IMAGE`，支持 batch。
- `asset_request`：`COMIC_ASSET_REQUEST`（如果可用）。
- `project_id`
- `entity_type`、`entity_id`
- `asset_type`
- `display_name`
- `generation_run_id`（若由请求/任务节点创建）
- `prompt`、`prompt_version`
- `model_name`、`seed`、`parameters_json`
- `reference_asset_ids`
- `review_status`：默认 `candidate`，不得默认直接设为 `approved`。
- `save_policy`：明确控制新增、去重或失败行为。

功能：

1. 校验输入图片的 shape、batch 和数值范围，按当前 ComfyUI 的 IMAGE 约定处理。
2. 将图片安全地写入配置资产根目录。文件名使用稳定 ID 或安全生成的名称，不依赖用户输入直接拼接路径。
3. 正确处理 batch：每张图片有独立 `asset_id`、文件、哈希和 `output_index`，并与同一个生成任务关联。
4. 计算 SHA-256、MIME 类型、宽高、文件大小等元数据。
5. 在数据库事务中写入资产、生成输出和实体关联。
6. 文件写入与 SQLite 事务并非天然原子操作：必须设计失败补偿。若数据库写入失败，应清理本次新建的孤立文件，或记录可恢复的待清理状态；不能删除已有资产文件。
7. 使用唯一约束/幂等键避免 ComfyUI 重复执行时产生重复记录，但不能把不同生成结果错误合并。
8. 保存失败时返回明确错误，不能输出伪造的成功资产 ID。
9. 输出：
   - 原始 `IMAGE`（允许继续下游连接）
   - 已登记资产列表/结构化 `COMIC_ASSET` 结果
   - 保存报告（新增、复用、失败及警告）
10. 登记节点只负责文件和元数据登记，不负责判断图像是否符合角色设定。

### 节点 D：Comic Asset Query

**用途：** 按项目、实体、素材类型、状态和关系查询资产。

输入建议：

- `project_id`
- `entity_type`、`entity_id`
- `asset_type`
- `status_filter`
- `primary_only`
- `limit`
- 可选章节过滤条件（只有现有模型能可靠关联时才提供）

输出：

- 结构化资产列表
- 资产 ID、类型、状态、尺寸、相对路径和关联关系
- 查询警告

默认优先返回已审核资产；是否包含候选素材应由输入明确控制。查询必须限定 `project_id`，避免跨项目误取。

### 节点 E：Comic Asset Loader

**用途：** 按资产 ID 或查询结果加载图像，输出 ComfyUI `IMAGE`，让已登记资产重新进入生成工作流。

要求：

- 验证资产记录存在、状态符合策略、文件路径在资产根目录内、文件存在且可解码。
- 对批量加载明确顺序与失败策略（整批失败或跳过并报告），不能悄悄错位。
- 记录缺失文件或哈希不一致警告。
- 输出图片及对应资产 ID/加载报告。
- 不应自动把未经审核的候选图作为正式参考图，除非用户显式开启。

### 节点 F：Comic Asset Review / Set Primary（可合并）

支持：

- 将候选资产标记为 `approved`、`rejected` 或 `needs_revision`。
- 为角色/外观版本/场景状态指定首选资产。
- 切换首选资产时在同一事务中取消旧首选，避免重复首选。
- 保存审核备注和时间。
- 不得因审核一个新资产而删除旧资产或原始漫画证据。

---

## 6. 典型工作流

### 6.1 角色三视图生成

```text
Comic Knowledge Query / Asset Query
             |
             v
Comic Asset Request Builder
             |
             +---- 已审核的角色/外观参考资产
             |
             v
Qwen-Image-Edit 2511 原有节点
             |
             v
Preview / 人工检查
             |
             v
Comic Asset Registrar  (status=candidate)
             |
             v
Comic Asset Review / Set Primary
             |
             v
Comic Asset Query / Loader
```

角色三视图资产应绑定 `character_id` 和明确的 `appearance_id`。如果三视图只描述某套校服，则不应把它设为所有外观版本共用的唯一参考图。

### 6.2 场景参考图生成

```text
Comic Knowledge Query
       |
       v
Asset Request Builder (location_id + location_state_id)
       |
       v
Qwen-Image-Edit 2511
       |
       v
Asset Registrar
       |
       v
Review / Set Primary
```

同一地点的白天、夜晚或天气变化应绑定到不同的 `location_state_id`，但仍关联相同的 `location_id`。

### 6.3 从漫画分格生成构图参考图

```text
Comic Page / Panel Analysis
       |
       v
Asset Request Builder (panel_id / story_event_id)
       |
       v
Qwen-Image-Edit 2511
       |
       v
Asset Registrar
```

原始分格是来源证据；编辑后生成的构图图是新资产，不能覆盖原始分格图片。

---

## 7. 数据质量与安全要求

1. 所有数据库写入使用参数化 SQL。
2. 对 JSON 字段使用 Schema 或明确的类型校验。
3. 使用 SQLite 事务处理关联记录与首选素材切换。
4. 为常用查询添加索引；评估并遵循现有数据库连接的线程使用方式，不要随意跨线程共享连接。
5. 使用稳定 ID 和唯一约束实现幂等，不依赖随机文件名判断业务重复。
6. 资产路径必须防止 `..` 路径穿越，且不能允许用户输入任意绝对路径覆盖任意文件。
7. 日志不得输出 API Key 或其他秘密。
8. 数据库迁移必须兼容现有用户数据；升级前提供备份或明确恢复方式。
9. 原始漫画证据、历史分析结果、已确认资产不能因新生成失败而丢失。
10. 不要仅凭文件哈希或提示词相同就把不同语义的资产合并。
11. ComfyUI 节点执行可能重复，因此登记和首选切换必须可安全重试。
12. 不要声称图片通过了角色一致性检查；本阶段只做资产登记和人工审核状态管理。

---

## 8. 测试与验收标准

必须编写并实际运行适合当前环境的测试。若无法在真实 ComfyUI 中运行，应清楚标注哪些是单元测试、哪些仍需集成测试。

### A. 数据库与迁移

- 新表能在现有数据库中初始化。
- 升级现有数据库不会丢失原有漫画、角色和剧情数据。
- 外观版本和场景状态关联正确。
- 非法或不存在的实体关联会被拒绝。
- 约束能阻止重复关系和冲突的首选素材。

### B. 资产登记

- 单张 IMAGE 能保存并登记。
- IMAGE batch 中每张图片都有独立资产 ID 和正确的 output index。
- 文件哈希、宽高和大小正确。
- 数据库事务失败时不会留下无法恢复的孤立文件。
- 同一工作流重复执行不会无条件重复登记。
- 不同生成结果不会因实体和类型相同而相互覆盖。
- 保存失败时不返回成功状态。

### C. 查询与加载

- 可按项目、角色、外观版本、场景状态、素材类型和审核状态查询。
- 可按资产 ID 加载为 IMAGE。
- 缺失文件、损坏图片、哈希不匹配时有清晰报告。
- 候选资产默认不会被当作已审核正式素材。
- 多图加载的输出顺序可预测。

### D. 版本与审核

- 候选、通过、拒绝和待修改状态可以正确更新。
- 同一实体与用途的首选资产切换是事务性的。
- 切换首选不会删除旧版本。
- 审核备注与时间可追踪。

### E. ComfyUI 集成

- 所有节点正确注册并显示。
- 节点的 `INPUT_TYPES`、`RETURN_TYPES`、`RETURN_NAMES`、`FUNCTION`、`CATEGORY` 等符合当前 ComfyUI 接口。
- IMAGE 输入输出可与原有 Qwen 节点和原生预览/保存节点连接。
- 保存工作流、重启 ComfyUI、重新加载后，数据库中的资产仍可查询。
- 不修改 ComfyUI 核心文件。
- 只有确实在目标环境运行过，才能声称 ComfyUI 集成测试通过。

### F. 回归测试

- 运行现有插件测试，例如仓库当前文档中的：
  `python -m unittest discover -s create_manga_story/tests`
- 根据实际目录结构确认命令是否正确；如果测试目录已调整，应使用真实路径并报告结果。
- 新增测试覆盖资产 repository、迁移、登记、查询、加载、版本与审核逻辑。
- 不得因本次开发破坏现有漫画导入、Gemini 缓存、RAG 检索、实体匹配和章节总结功能。

---

## 9. 分阶段实施顺序

严格按阶段交付，每阶段先检查、实现、运行测试并报告结果。

### 阶段 1：数据库与资产存储基础

- 检查现有 schema 和迁移机制。
- 实现资产表、关系表、生成任务表及必要索引。
- 实现安全的资产文件存储工具。
- 测试事务、路径、哈希、迁移和失败补偿。

### 阶段 2：资产登记节点

- 实现 `Comic Asset Registrar`。
- 支持 IMAGE 单图和 batch。
- 记录生成任务与输入参考资产。
- 输出原始 IMAGE、结构化资产结果和保存报告。
- 运行单元测试。

### 阶段 3：资产查询与加载

- 实现 `Comic Asset Query` 和 `Comic Asset Loader`。
- 支持审核状态过滤和确定性的图片顺序。
- 测试缺失文件、损坏文件、哈希异常和跨项目隔离。

### 阶段 4：生成请求与审核

- 实现 `Comic Asset Request Builder`。
- 实现资产审核和首选资产切换。
- 让请求节点与现有 Qwen 节点在工作流中连接。
- 验证角色身份、外观版本、场景状态和来源证据关联。

### 阶段 5：端到端验证与文档

- 提供角色三视图、角色立绘和场景参考图的示例工作流搭建说明。
- 在真实 ComfyUI 环境中验证节点连接；无法验证的部分明确列为待验收项。
- 运行所有现有和新增测试。
- 更新 README，说明安装、数据库迁移、数据目录、节点用途、工作流、失败恢复和限制。

---

## 10. AI 编码助手的交付要求

开始时先给出仓库检查报告和最小改动计划，再开始阶段 1。不要直接生成整套代码后宣称完成。

最终交付应包含：

- 新增/修改文件清单及每个文件的职责。
- 数据库迁移说明。
- 完整节点源码和注册代码。
- 资产文件存储与 repository 实现。
- 新增测试及实际测试输出。
- 示例工作流或可按步骤搭建的工作流说明。
- README 更新。
- 明确列出未运行的集成测试和未验证的 ComfyUI 兼容性。

**总原则：先复用现有 Comic2Anime 知识库和 Qwen 推理节点，再增加独立的资产管理层；以稳定 ID 关联知识实体、图片文件、生成记录和审核状态，为未来角色参考图、场景参考图和 MiniMax H3 镜头工作流提供可靠素材来源。**

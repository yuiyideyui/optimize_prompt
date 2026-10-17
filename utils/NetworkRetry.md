# Network Retry 网络重试工具

## 节点名称

**模块名称**: `network_retry`  
**功能**: 为网络请求提供自动重试机制的装饰器工具

## 功能说明

`network_retry` 模块提供了一个通用的网络请求重试装饰器 `retry_on_network_error`，用于处理网络请求中的临时性错误。当遇到网络错误时，该装饰器会自动等待指定时间后重试，直到达到最大重试次数才抛出异常。

这个工具主要用于提高 ComfyUI 自定义节点在不稳定网络环境下的健壮性，避免因临时性网络问题导致工作流中断。

## 核心功能

### retry_on_network_error 装饰器

```python
@retry_on_network_error(max_retries=4, retry_delay=60)
def your_network_function():
    # 你的网络请求代码
    pass
```

**参数说明**:

- `max_retries` (int, 默认=4): 最大重试次数
- `retry_delay` (int, 默认=60): 重试间隔秒数

**捕获的异常类型**:
- `urllib.error.HTTPError`: HTTP 错误（如 500, 502, 503 等）
- `urllib.error.URLError`: URL 错误（如网络超时、DNS 解析失败等）
- `RuntimeError`: 运行时错误（通常由 API 返回的错误信息包装而成）

## 工作原理

1. **首次尝试**: 执行被装饰的函数
2. **捕获错误**: 如果发生网络相关错误，捕获异常
3. **重试逻辑**:
   - 如果是最后一次尝试，立即抛出包装后的异常
   - 否则打印重试信息，等待指定时间后重试
4. **成功返回**: 任何一次尝试成功都会立即返回结果

## 使用方法

### 基本用法

```python
from .utils.network_retry import retry_on_network_error

@retry_on_network_error(max_retries=4, retry_delay=60)
def _post_json(url, headers, payload, timeout):
    # 网络请求逻辑
    request = urllib.request.Request(url, ...)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())
```

### 自定义重试参数

```python
# 快速重试（适用于轻量级请求）
@retry_on_network_error(max_retries=3, retry_delay=30)
def quick_request():
    pass

# 长时间重试（适用于重要的长时请求）
@retry_on_network_error(max_retries=5, retry_delay=120)
def important_request():
    pass
```

## 输出示例

当发生网络错误时，控制台会输出以下信息：

```
[网络错误] 第 1/4 次尝试失败: API request failed (500): Internal Server Error
[重试] 等待 60 秒后进行第 2 次尝试...
```

如果所有重试都失败，最终会抛出：

```
RuntimeError: 网络请求失败，已重试 4 次: API request failed (500): Internal Server Error
```

## 依赖要求

- Python 标准库:
  - `time`: 用于等待重试
  - `urllib.error`: 捕获网络异常
  - `functools`: 提供装饰器功能

**无新增第三方依赖**

## 应用场景

1. **API 调用**: Gemini、Claude、OpenAI 等 AI 服务的 API 请求
2. **不稳定网络**: 在网络连接不稳定的环境下提高成功率
3. **服务器临时故障**: 应对 API 服务器的临时性错误（如 502, 503）
4. **速率限制**: 给 API 速率限制一些缓冲时间

## 异常与限制

### 已知限制

1. **阻塞执行**: 重试期间会阻塞当前线程，不适用于需要立即响应的场景
2. **固定延迟**: 所有重试使用相同的延迟时间，不支持指数退避
3. **不区分错误类型**: 对所有捕获的异常都执行相同的重试策略

### 不适用场景

- **客户端错误**: 4xx 错误（如 401 未授权、404 未找到）通常不应重试
- **明确的逻辑错误**: 如 API 密钥无效、参数格式错误等
- **即时响应要求**: 需要快速失败的场景

### 错误排查

**问题**: 重试后仍然失败
- **原因**: API 密钥无效、网络配置问题、API 服务长时间宕机
- **解决**: 检查 API 密钥、网络连接、API 服务状态

**问题**: 等待时间过长
- **原因**: `retry_delay` 设置过大，或 `max_retries` 过多
- **解决**: 调整装饰器参数，减少重试次数或缩短延迟时间

## 已集成此工具的节点

目前已经集成网络重试功能的节点：

1. **Gemini / Claude Text** ([gemini_claude_text.md](../gemini_claude_text.md))
2. **Gemini Video Inspector** ([gemini_video_inspector.md](../gemini_video_inspector.md))

这些节点的所有网络请求都会在遇到错误时自动重试最多 4 次，每次间隔 60 秒。

## 版本历史

### v1.0.0 (2026-10-17)
- ✨ 初始版本
- 🔧 支持 `max_retries` 和 `retry_delay` 参数
- 📝 捕获 `HTTPError`、`URLError` 和 `RuntimeError`
- 💬 提供详细的重试日志输出

## 技术细节

### 装饰器实现

使用 `functools.wraps` 保留原函数的元信息（函数名、文档字符串等），确保装饰后的函数行为符合预期。

### 异常链

使用 `raise ... from error` 语法保留原始异常堆栈，便于调试时追踪错误来源。

### 线程安全

该装饰器是线程安全的，可以在多线程环境中使用，但要注意重试期间会阻塞当前线程。

## 未来改进方向

1. **指数退避**: 支持逐渐增加重试延迟时间
2. **可配置异常**: 允许用户指定要重试的异常类型
3. **回调函数**: 在每次重试前/后执行自定义回调
4. **异步支持**: 支持 asyncio 异步函数的重试

# V8.2 验收记录

## 本轮目标

- 重设计真实 Tkinter UI，而非只改 HTML 概念稿。
- 保留 V8.1 的全部对局、推演、人工接管、API 和棋谱功能。
- 在 1080×720 最小窗口与 Windows 高 DPI 下不截断左右栏。

## 已验证事实 [V]

- `python -m py_compile app.py` 通过。
- `python -m pytest -q`：7 项通过。
- `tests/smoke_gui_startup.py`：Pikafish 就绪、27,369 盘棋谱、默认标准速度通过。
- 最小窗口实测：根窗口宽 1080；第三栏右边界 1080；左栏内容底边 711，小于窗口高 720。
- HTML 预览经 Python `HTMLParser` 完整读取。

## 修复根因 [V]

左右栏内部使用 `pack`，旧代码却调用 `grid_propagate(False)`，没有真正固定内容尺寸。
右侧 `Text` 的请求宽度会扩大整栏并将内容推到窗口外。V8.2 改为
`pack_propagate(False)`，并加入最小尺寸回归检查。

## 工程判断 [H]

桌面象棋指导工具应让棋盘承担主视觉，左侧只放命令，右侧只放分析结果；
低频按钮无需与“推荐一步”争夺相同视觉权重。

## 尚需用户验收 [T]

- 用户对配色、密度、字号和按钮命名的主观接受度。
- 用户当前已经打开的旧 V8 进程需重启后才会载入 V8.2 UI。

## API 优先代走增量

### 问题与目标

用户要求接入 DeepSeek 等 API 后，由 API 优先决定“代走一步”，同时参考
Pikafish 的专业搜索结果，而不是让语言模型脱离引擎自由猜着。

### 修改文件

- `api_client.py`：新增受约束 JSON 选着和兼容网关降级请求。
- `app.py`：新增 Pikafish MultiPV → API选择 → 本地双重校验 → 落子链路。
- `tests/test_api_client.py`、`tests/test_app_interactions.py`：新增成功、越权、
  JSON模式不兼容和超时回退测试。
- `README.txt`、`CHANGELOG_V8.2.txt`：更新职责边界和使用说明。

### 已验证事实 [V]

- 全量 `pytest`：12项通过。
- 真实 Pikafish 集成验证：深度15返回至少两个候选，模拟 DeepSeek 选择第二候选，
  程序采用第二候选，输出 `REAL_PIKAFISH_API_GUIDED_OK`。
- API返回候选外着法会抛出 `ApiError`；应用层再次用候选集合和 `legal_moves`
  双重校验，异常时采用 `analysis.best`。
- GUI启动、27,369盘棋谱加载和1080×720布局冒烟检查通过。

### 剩余风险 [T]

- 未调用用户的真实 DeepSeek Key，避免未经确认消耗额度；真实网关的模型名、
  配额、限流和响应时间仍需用户首次点击“API代走”验证。
- DeepSeek官方说明 JSON Output 偶尔可能返回空内容；该分支会安全回退Pikafish，
  但用户会看到一次“API安全回退”。

### 快照与回滚

- 当前目录不是Git仓库，未创建提交或标签。[V]
- 回滚时需恢复本轮的 `app.py`、`api_client.py`、两份测试和说明文件；
  未在用户验收前执行清理或删除。[V]

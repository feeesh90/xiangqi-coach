# Pikafish / Px0 接入说明

## 当前状态（2026-09-19）

**真实 Px0 已安装并通过对局验收，不需要再手动下载程序或模型。**
已从官方 Px0 源码构建 Windows x64 程序，使用 ONNX Runtime 1.30.0 的 `onnx-cpu`
后端及官方 PxZero Run 1 network 50 权重。两种引擎分别连续完成 60 个半回合（30 回合），
真实 Tk 界面的红黑执方、推荐、悔棋、重开、切回 Pikafish 均通过。
`data/px0_acceptance.json` 与 `data/pikafish_acceptance.json` 保存真实引擎验收结果及资源校验值。
`tests/fake_px0.py` 仅用于异常协议测试，不参与真实验收或游戏运行。

## 原项目与调用链

本项目是 Python 3.11+ / Tkinter 桌面应用，不存在 Web 前后端或服务端引擎。

| 文件/目录 | 作用 |
| --- | --- |
| app.py | Tkinter/ttk/Canvas 界面、对弈、后台任务和主线程回调 |
| rules.py | Position、FEN、合法性检查、Move 坐标与中文记谱 |
| engine.py | 现有 Pikafish 进程和搜索封装，未重写 |
| coach.py | 走法分析、预演及复盘说明 |
| api_client.py | 可选 HTTP 模型协作；不承担本地搜索 |
| master_book.py、chinese_pgn.py | SQLite/JSON 棋谱查询、中文棋谱解析 |
| engine/ | 现有 pikafish.exe 和 pikafish.nnue |
| scripts/、tests/ | 启动/安装/导入/打包及测试 |

```text
App.opponent_move / recommend / ai_take / analyze_premove / analyze_my_move
    → Position.fen()：10×9 棋盘，红方 w / 黑方 b
    → 后台线程（任务代次与当前引擎绑定）
    → EngineManager.analyze / analyze_limit
        ├─ Pikafish：原 engine.py
        └─ Px0：px0_engine.py
    → subprocess.Popen：stdin/stdout，独立本地进程
    → uci / uciok（首次启动）
    → position fen ...
    → go movetime ...（或 depth / nodes；可带 searchmoves）
    → info depth / score / multipv / pv
    → bestmove ...
    → Move.from_ucci：列 a-i，行 0-9 → 数组行 9-坐标行
    → Analysis / Candidate
    → Tk 主线程队列：校验任务代次、FEN 和合法性
    → App.make_move 更新棋盘
```

`Move.ucci()` 命名是历史遗留的坐标名称，实际进程握手是 UCI，不是 UCCI。
默认棋力等级保持原来的休闲/入门/初级/中级/进阶/高手/大师，以 100–7000ms
限制思考时间；同等时间不代表两种引擎的棋力相同。Px0 的 depth 是其自身搜索统计，
不能等同于 Pikafish 的 alpha-beta 深度。

## 新增与修改

新增：
- engine_manager.py：统一接口、串行调用、任务代次、切换及配置。
- px0_engine.py：复用 Pikafish 的进程读写基础，独立实现 Px0 握手、选项、搜索解析。
- engine_settings.example.json：配置示例。
- engine/px0/README.md：资源放置说明。
- scripts/check_engines.py：真实引擎诊断与 60 步验收。
- tests/fake_px0.py、tests/test_px0_engine.py：协议进程、异常、取消、切换测试。
- tests/smoke_engine_switching.py：真实 GUI 与 Pikafish 操作验证。
- tests/smoke_real_px0.py、tests/smoke_real_px0_lifecycle.py：真实 Px0 的界面与进程故障验证。
- engine/px0/：可执行文件、模型、运行库、UCI 记录、来源清单、许可证和独立源码归档。
- 本文及 data/ 中两份验收报告。

修改：
- app.py：创建 EngineManager；已有“引擎”设置增加选择、路径、backend 和玩家执方；
  所有分析线程绑定任务代次；关闭异步清理；避免忙碌时漏掉 AI 回合。
- coach.py、api_client.py：将写死 Pikafish 的说明改成“当前引擎”。
- tests/test_api_client.py：相应更新提示文字断言。

未移动/替换 Pikafish 二进制、NNUE、棋谱库，未改写规则模块。

## Px0 初始化和模型加载

`App.__init__ → EngineManager`，默认选择 Pikafish。
在引擎设置中选择 Px0 后，后台执行 `EngineManager.switch → Px0.start`：

1. 检查 executable 和 network 都是实际文件，缺失时报明确路径。
2. 启动独立进程，发送 uci，记录真实 id 和所有 option。
3. 检查 id 包含 Px0，拒绝将国际象棋 Lc0 当成中国象棋引擎。
4. 仅在引擎声明相应选项时设置 WeightsFile、Backend、BackendOptions、Threads。
5. 如果 ScoreType 明确提供 centipawn，则选用它；否则不把 cp 字段冒充可比较的分数。
6. 发送开局 position，再发送 isready。官方源码的 backend 初始化发生在
   SetPosition；只得到 readyok 不等于已经完成模型加载。
7. 执行一次 100ms 的真实搜索；成功且未被取消才保存配置并提交切换。

线程缺少 Threads 选项时使用引擎默认值；backend 空值表示使用引擎声明的默认后端。
CPU/GPU 后端名称不硬编码为可用列表，指定值必须属于该二进制返回的 Backend 列表。
不会向 Px0 发送 Pikafish 的 EvalFile 或 Hash MB。

## 资源与配置

推荐目录（保留原 Pikafish 文件）：

```text
engine/
  pikafish.exe
  pikafish.nnue
  px0/
    px0.exe
    network.pb.gz
    <安装包配套 DLL>
```

`network.pb.gz` 已安装，是官方发布的 gzip 格式权重；重命名没有改变文件内容。
解压后的 SHA-256 与官方发布文件名一致，详见 `engine/px0/provenance.json`。
默认权重与程序同目录，使用相对文件名发送给引擎，已验证中文项目路径。
外部中文模型路径会尝试 Windows 短路径；无法表示时明确提示改用引擎目录和英文模型文件名。
相对路径以项目目录（app.py 所在目录）解析，也支持界面选择外部文件。

配置复用现有用户配置目录：`~/.xiangqi-coach/engine_settings.json`，与
`api_settings.json` 分开，不修改 API Key。只在切换成功后保存配置。
每次启动仍默认 Pikafish；Px0 的路径、backend 等设置会保留。

| Px0 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| enginePath | engine/px0/px0.exe | 独立可执行文件 |
| networkPath | engine/px0/network.pb.gz | 配套中国象棋权重 |
| backend | 空 | 使用二进制默认后端，可填写其声明支持的值 |
| backendOptions | 空 | 后端专属参数字符串，GPU 设备等由对应后端解释 |
| threads | 2 | 搜索线程，仅在引擎声明支持时应用；不等于 GPU 数量 |
| startupTimeout | 60 | 握手/模型准备超时秒数，可在 JSON 中调整，范围 1–300 |
| searchTimeout | 30 | 搜索额外超时秒数；time 模式加上 movetime，范围 1–300 |

Pikafish 单独保留 threads、hash_mb。图形界面将 Hash 输入与 Px0 设置分开启用。
默认日志：`data/logs/engine.log`，单份 1MB、保留两份备份。
记录启动、命令/FEN、bestmove、选项、切换和关闭，不将整段搜索 info 刷到界面。

## 使用与测试

1. 双击原有“一键启动.bat”。
2. 点击右侧分析卡底部“引擎”。
3. 选择 Px0，保留默认程序和模型路径，backend 留空（或填写 `onnx-cpu`），点击“保存并应用”。
4. 模型加载和试算成功后，状态显示 Px0 已就绪，棋盘对弈/分析流程与以前相同。
5. 同一设置中选择 Pikafish 即可切回。失败时保留之前的引擎，可重试/切回/新局。
6. 玩家执方可选红或黑；修改执方后会新开局。棋盘仍采用原红方视角，不自动翻转。

在项目目录运行（Python 3.11+）：

```powershell
python scripts/check_engines.py --engine px0 --plies 60 --report data/px0_acceptance.json
python scripts/check_engines.py --engine pikafish --plies 60 --report data/pikafish_acceptance.json
# 也可以显式指定资源（相对路径从项目目录解析）：
python scripts/check_engines.py --engine px0 --engine-path engine/px0/px0.exe --network-path engine/px0/your-model.pb.gz

# 开发回归测试，需要 pytest：
python -m pip install pytest
python -m pytest -q tests
python tests/smoke_gui_startup.py
python tests/smoke_engine_switching.py
python tests/smoke_real_px0.py
python tests/smoke_real_px0_lifecycle.py
```

验收脚本记录可执行文件和权重 SHA-256、真实 UCI 选项、开局/应将/残局、红黑双方、
time/depth/nodes、searchmoves、60 步连续走棋、撤回局面后重新分析、新局。
资源缺失时返回失败，不会改用假引擎或 Pikafish 冒充 Px0。

## 已验证与未验证

- 原有测试与新增协议测试合计 43 项通过；模拟协议覆盖 60 步、score/mate/PV、多PV缺失、
  网络/程序缺失、非法着、错误引擎、进程崩溃、超时、stop不响应、取消切换、关闭与重启。
- 真实 Pikafish：开局/应将/残局、各搜索模式、连续 60 步合法；GUI 最小窗口布局、
  引擎缺失回退、玩家红黑执方、悔棋和新局通过。
- 真实 Px0：实际 UCI 握手、神经网络推理、score/depth/PV/MultiPV、红黑双方、开局/中局/
  应将/残局、time/depth/nodes/searchmoves、连续 60 个半回合均通过。真实 GUI 完成红黑对局、
  推荐、悔棋、快速重开和切回 Pikafish；真实进程通过取消、强制崩溃后恢复、损坏模型回退、
  重复启动、新局、终局阻止落子和关闭清理。
- 没有 MultiPV 时仅显示一个实际候选，其余按钮保持禁用。没有评分不伪造零分，
  没有足够 PV 时只播放实际合法部分。未新增胜率换算。
- Px0 centipawn 与 Pikafish 分数不可跨引擎比较；现有指导分类仅作为当前引擎的启发式提示。
- 保留现有“当前 FEN”输入方式，未新增完整历史输入或长将/长捉/重复局面裁判。
  因而不能宣称完整赛事规则或 Px0 历史神经网络输入已实现。
- 每次新局/悔棋/切换/取消使旧任务失效；串行执行避免混读 bestmove。
  Px0 未响应 stop 时约 1 秒内终止搜索进程。关闭会等待后台清理完成，避免遗留进程。
- 本次只交付已验证的 CPU 后端；没有安装 CUDA/TensorRT/GPU 推理依赖。UCI 列表中的
  onnx-cuda / onnx-trt 名称不代表本包带有对应提供程序，请使用默认的 onnx-cpu。
- 此二进制针对当前 Ryzen 7 5700X 编译；迁移到旧 CPU 或其他系统时需要重新构建。
- 未测试其他平台构建、外部联网 API 或 PyInstaller 成品。
  现有打包脚本包含整个 engine 目录，但实际成品仍需另行验收。

## 官方核对来源

- https://github.com/official-pikafish/px0
- https://github.com/official-pikafish/px0/releases （2026-09-18 查询无 Releases）
- https://github.com/official-pikafish/px0/blob/master/src/chess/uciloop.cc
- https://github.com/official-pikafish/px0/blob/master/src/neural/shared_params.cc
- https://github.com/official-pikafish/px0/blob/master/src/engine.cc
- https://github.com/official-pikafish/px0/blob/master/src/search/classic/params.cc

- https://github.com/official-pikafish/pxzero-networks/releases/tag/px0_client （真实模型来源）
- https://github.com/microsoft/onnxruntime/releases/tag/v1.30.0
- https://github.com/mstorsjo/llvm-mingw/releases/tag/20260908

实际握手记录在 `engine/px0/uci-handshake.txt`，来源/校验信息在 `provenance.json`。
构建过程只对外部 Px0 源码做了两个编译兼容修复：补充 iterator 头文件，显式实例化
CheckersTo<true> 模板；补丁与原始源码压缩包在 `engine/px0/source/`。
未把引擎源码混入游戏业务代码。排查时尝试过 Eigen，但该分支残留 8×8 棋盘常量；
最终构建已关闭 Eigen/BLAS，采用官方已有的 10×9 ONNX 实现，未交付试验性的推理修改。
详见 `engine/px0/BUILD.md`。

"""Summarize this machine's raw measurements without inventing playing strength."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/"data/performance/20261017"


def load(name):
    return json.loads((DATA/name).read_text(encoding="utf-8-sig"))


def p95(values):
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def stats(samples, field):
    values = [sample[field] for sample in samples]
    return {"count": len(values), "p50": statistics.median(values),
            "p95": p95(values), "max": max(values)}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    baseline = load("baseline_7s.json")
    optimized = load("optimized_matrix.json")
    gui = load("gui_latency_2500.json")
    groups = defaultdict(list)
    all_groups = defaultdict(list)
    for sample in baseline["samples"]+optimized["samples"]:
        key = (sample["profile"], sample["budget"])
        all_groups[key].append(sample)
        if sample["category"] != "tactical":
            groups[key].append(sample)
    labels = {
        "pikafish_cpu_2": "Pikafish 原 2 线程",
        "pikafish_cpu_8": "Pikafish 优化 8 线程",
        "px0_original_cpu_1": "Px0 原 CPU 配置",
        "px0_gpu_1_fp32": "Px0 GPU（1 搜索线程、FP32）"}
    output = {"search_groups": [], "gui_groups": [], "quality": {}}
    search_rows = []
    for (profile, budget), samples in groups.items():
        metric = stats(samples, "elapsed_ms")
        metric.update(profile=profile, budget_ms=budget,
                      median_nodes=statistics.median(s["counters"]["nodes"] for s in samples))
        output["search_groups"].append(metric)
        search_rows.append(f'| {labels[profile]} | {budget/1000:g} | {metric["p50"]/1000:.3f} | '
                           f'{metric["p95"]/1000:.3f} | {metric["median_nodes"]:,.0f} |')
    gui_rows = []
    gui_groups = defaultdict(list)
    for sample in gui["samples"]:
        gui_groups[sample["profile"]].append(sample)
    for profile, samples in gui_groups.items():
        metric = stats(samples, "total_ms")
        metric.update(profile=profile, within_3s=sum(s["within_3_seconds"] for s in samples))
        output["gui_groups"].append(metric)
        gui_rows.append(f'| {labels[profile]} | {metric["count"]} | {metric["p50"]/1000:.3f} | '
                        f'{metric["p95"]/1000:.3f} | {metric["max"]/1000:.3f} | '
                        f'{metric["within_3s"]}/{metric["count"]} |')
    cases = json.loads(
        (ROOT/"tests/fixtures/performance_positions.json").read_text(encoding="utf-8"))
    quality_sections = []
    for engine, original, current in (
            ("Pikafish", "pikafish_cpu_2", "pikafish_cpu_8"),
            ("Px0", "px0_original_cpu_1", "px0_gpu_1_fp32")):
        rows, details = [], []
        for case in cases:
            sets = []
            for key in ((original, 7000), (current, 7000), (current, 2500)):
                selected = [s for s in all_groups[key] if s["case"] == case["id"]]
                sets.append(sorted({s["bestmove"] for s in selected}))
            details.append({"case": case["id"], "original_7s": sets[0],
                            "optimized_7s": sets[1], "optimized_2_5s": sets[2]})
            rows.append(f'| {case["id"]} | {", ".join(sets[0])} | {", ".join(sets[1])} | {", ".join(sets[2])} |')
        output["quality"][engine] = details
        quality_sections.append(f'### {engine}\n\n| 局面 | 原 7 秒 | 优化 7 秒 | 优化 2.5 秒 |\n'
                                '|---|---|---|---|\n'+"\n".join(rows))
    tests = (DATA/"pytest_final.txt").read_text(encoding="utf-8-sig")
    count = re.search(r"(\d+) passed", tests)
    if not count:
        raise RuntimeError("Final regression result not complete")
    acceptances = {name: load(name+"_acceptance.json") for name in
                   ("pikafish", "px0_cpu", "px0_gpu")}
    assert all(d["status"] == "passed" and d["plies"] >= 60 for d in acceptances.values())
    smoke = load("gui_smoke/result.json")
    assert smoke["status"] == "passed"
    assert "RESTART_OK" in (DATA/"px0_cpu_lifecycle.log").read_text()
    assert "RESTART_OK" in (DATA/"px0_gpu_lifecycle.log").read_text()
    output["regression_passed"] = int(count[1])
    output["engine_acceptance_plies"] = {name: d["plies"] for name, d in acceptances.items()}
    output["gui_smoke_cases"] = len(smoke["cases"])
    output["resources_verified_after_measurements"] = {
        name: digest(ROOT/name) for name in (
            "engine/pikafish.exe", "engine/pikafish.nnue", "engine/px0/px0.exe",
            "engine/px0-gpu/px0.exe", "engine/px0/network.pb.gz")}
    before = [s for s in load("gui_scheduling_before.json") if s["version"] == "baseline"]
    after = load("gui_scheduling_after.json")
    delay_before = statistics.median(s["request_delay_ms"] for s in before)
    delay_after = statistics.median(s["request_delay_ms"] for s in after)
    cold = {**baseline["cold_start_ms"], **optimized["cold_start_ms"]}
    raw_count = sum(len(load(name)["samples"]) for name in
                    ("baseline_7s.json", "optimized_matrix.json", "cpu_sweep.json",
                     "px0_backend_sweep.json", "gui_latency_2500.json"))
    output["real_search_measurements"] = raw_count
    output["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (DATA/"summary.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    report = f"""# 引擎提速与功能验收实测

日期：2026-10-17。全部数据来自本机实际执行，原始记录位于 data/performance/20261017/。

## 本次交付

- 主棋盘和图片残局预览改为一键翻转，执方与显示方向独立。
- 新增主棋盘创造模式，支持任意移动、交换、添加、替换、删除与先行方选择。
- 应用建立新起点；取消恢复原棋局；旧引擎/API 回调失效。联机只允许翻转。
- 确认调度延迟后，将正常 AI 调度从 250ms 改为 20ms，繁忙重试为 100ms。
- 本机 Pikafish 推荐 8 线程、512MB Hash；Px0 推荐独立 ONNX CUDA 包、1 搜索线程、FP32。
- 所有棋力档位保持原时间：大师仍为 7000ms。2.5 秒仅用于独立测试进程。

硬件：Ryzen 7 5700X（8 核 16 线程），RTX 5060 Ti（16311 MiB），驱动 596.21，Python 3.14.7。
GPU 使用原 Px0 可执行文件及相同网络，BackendOptions=fp16=false。
配置禁用 FP16 模型转换、保留 FP32 张量；未使用缩小或较弱模型。
GPU 算子实现与 CPU 不同，这不构成逐位一致或棋力相同的保证。

## 方法与边界

12 个固定合法局面，覆盖红黑行棋、开局、中局、残局和两个独立可验证的一步杀。
每个配置、预算、局面重复 3 次；另有 CPU 线程和 GPU 精度/线程初筛。
共 {raw_count} 条真实搜索测量。每次清理搜索状态，预热后再测量，关闭 GUI 结果缓存。
原 CPU 配置及 Px0 适配器由修改前备份加载，程序、模型哈希在 summary.json 中复核。
测试在正常 Windows 桌面环境执行，存在后台应用与调度波动，不是隔离性能实验室。

下表排除两个会提前终止的一步杀，采用其余 10 个局面 × 3 次的搜索统计。
Px0 在 movetime 上限内可能主动提前结束，给定时间上限并不保证耗满预算。
不同引擎的节点数、NPS 和深度不能互相比较。

| 引擎配置 | 搜索上限/秒 | 实际耗时中位数/秒 | P95/秒 | 节点数中位数 |
|---|---:|---:|---:|---:|
"""+"\n".join(search_rows)+f"""

Pikafish 原 2 线程 7 秒约 1419 万节点，优化 8 线程 2.5 秒约 1729 万节点。
这表明本机计算效率提高，不能据此认定棋力完全相同。
原 Px0 CPU 的推理线程默认是 1；增加搜索线程收益不明显，显式设置 ONNX CPU threads=8 有收益。
CPU 备选配置已完成连续 60 个半回合验证，但当前优先配置为已验证的 GPU FP32。
FP16 初筛更快，本次仍选择 FP32；两种精度都使用同一网络权重。

## 完整 GUI 调用链的 2.5 秒实验

测量从合法 AI 局面就绪、调度开始，到引擎返回、合法性复核和 Canvas 更新完成。
12 局面 × 3 次，窗口离屏运行，实际鼠标对局另由下面的 GUI 冒烟验证。
这里不把结果当作 Windows 显示器物理刷新延迟，也不包含引擎首次初始化。

| 配置 | 次数 | 中位数/秒 | P95/秒 | 最长/秒 | 3秒以内 |
|---|---:|---:|---:|---:|---:|
"""+"\n".join(gui_rows)+f"""

零搜索时间替身诊断：落子到请求开始的中位数从 {delay_before:.1f}ms 降为 {delay_after:.1f}ms。
该诊断只测调度，不能当作真实引擎速度；真实搜索数据见上表。
本次基准的独立进程就绪加首次单节点搜索：Pikafish 8 线程 {cold["pikafish_cpu_8"]:.0f}ms，
Px0 GPU FP32 {cold["px0_gpu_1_fp32"]:.0f}ms。初次启用、驱动和磁盘缓存会影响启动耗时。

## 走法差异与棋力限制

以下列出每种配置 3 次测量得到的不同 bestmove，坐标为内部 UCCI，与显示方向无关。
两个一步杀在全部配置中均找到正确答案。
相同着法不能证明棋力相同，不同着法也不能直接判为更差。
这些样本和节点数不能生成 Elo、胜率或“等同原大师”的结论。

"""+"\n\n".join(quality_sections)+f"""

建议：先体验本机 GPU 配置。若要稳定接近 3 秒，可采用经过测试的 2500ms 搜索上限，
但应由用户确定是增加独立时间设置还是调整现有档位。本次保留大师 7000ms。

## 功能与稳定性验收

- 修改前回归：167 项通过；最终回归：{count[1]} 项通过，见 pytest_final.txt。
- Pikafish 8 线程、Px0 CPU 备选、Px0 GPU FP32，各连续 60 个半回合合法走棋通过。
- CPU 与 GPU 均通过取消、异常退出后恢复、损坏模型回退、重开验证。
- 自动化真实双引擎 GUI 共 {len(smoke["cases"])} 个执方用例：通过 Canvas 鼠标事件完成创造模式、红黑落子、
  真实 AI 应手、悔棋回新起点，以及思考中编辑/取消/重新应手。
- 引擎切换失败后，Pikafish 仍能继续走棋；全部验证程序拥有的引擎进程均已关闭。
- 实际检查 1080×720 截图；编辑时收起暂停的指导/回放栏，保留更大的可编辑棋盘。
- 上述鼠标验证使用 Tk 事件注入，截图由程序采集后检查，不写作真人手动验收。
- 使用本机保存配置的设置窗口选择 Px0、切换执黑、翻转并编辑后继续应手通过，见 saved_config_gui.json。
- 自动测试覆盖图片预览翻转、90 点坐标、联机双方独立翻转与本机双实例同步。
- 未执行两台实体电脑测试；不宣称完整长将、长捉、重复局面赛事裁判已实现。

GPU 执行证据：gpu_execution_proof.json 中，同一 Px0 PID 加载 CUDA/cuDNN/cuBLAS 并出现在
NVIDIA 计算进程列表。WDDM 下单进程显存不可用；文件中的显存/利用率是整张显卡的读数。
依赖来源及 SHA-256 见 engine/px0-gpu/provenance.json；网络未上传，驱动和全局 PATH 未改动。

## 代码、配置和复测

主要修改：app.py、endgame_ui.py、lan_ui.py，以及说明和相关测试。
新增：board_editor.py、creative_ui.py、创造模式测试、性能局面与基准脚本。
engine.py、px0_engine.py、engine_manager.py、rules.py 的业务实现保持原样。
原文件及引擎配置备份：data/backups/20261017_flip_creative_speed/。

本机配置由原有 ~/.xiangqi-coach/engine_settings.json 管理：
Pikafish 8 线程；Px0 程序 engine/px0-gpu/px0.exe，
网络 engine/px0/network.pb.gz，Backend onnx-cuda，BackendOptions fp16=false，搜索线程 1。
软件启动仍默认 Pikafish，在“引擎”设置选择 Px0 即可使用 GPU。
本机依赖已安装，无需手工下载。CPU 恢复参数见 engine_settings.cpu.example.json。

复测命令（项目目录）：

```powershell
python -m pytest -q tests
python tests/smoke_creative_mode.py --gpu
python tests/smoke_real_px0_lifecycle.py --gpu
python scripts/benchmark_engines.py --profiles data/performance/20261017/optimized_profiles.json --output data/performance/retest_search.json --times 7000,2500 --repeat 3
python scripts/benchmark_gui_latency.py --profiles data/performance/20261017/optimized_profiles.json --output data/performance/retest_gui.json --budget 2500 --repeat 3
```

关闭旧软件，再双击“一键启动.bat”或“run.bat”。使用细节见“翻转与创造模式说明.md”。
"""
    (ROOT/"引擎提速实测_20261017.md").write_text(report, encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("regression_passed", "real_search_measurements", "gui_groups")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

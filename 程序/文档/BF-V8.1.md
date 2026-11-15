# BF-V8.1 修复记录

- 问题：四步推演瞬间跳终局；将军状态缺少可理解的交互；AI回合不能安全人工接管；API只追加讲解；CCPD导入过慢且会先删除正式库；固定depth/nodes分数解析失败。
- 根因：PV在单次UI回调中循环落子；界面未暴露将军合法着状态；AI定时任务ID未保存；API上下文只有当前方案；中文棋谱每手生成全盘合法着；导入直接写正式库；正则把 `\\s` 写成了字面反斜杠。
- 修改文件：`app.py`、`api_client.py`、`chinese_pgn.py`、`engine.py`、`master_book.py`、`scripts/import_ccpd.py`、相关测试与说明。
- 最小补丁：增加可取消的逐步播放状态、单回合接管状态和将军提示；API只扩展为候选评议；解析器缩小候选范围；数据库改为旁路构建。
- 验证：`py_compile`通过；pytest 7项通过；真实Pikafish返回合法MultiPV三方案；
  GUI启动冒烟显示“Pikafish 已就绪”“大师棋谱：27369盘”；SQLite
  `integrity_check=ok`，共有27369盘去重对局和1069242条前缀记录。
- 验证纠正：第一次GUI冒烟只等待2.2秒，在引擎仍启动时提前断言而失败；
  改为真实Tk `mainloop` 最多等待10秒并确保finally关闭后，复测通过。
- 剩余风险：长将、长捉和复杂重复局面仍未实现完整赛事裁判规则；API输出仍可能有语言幻觉，因此不授予落子权。
- 审查状态：已完成本轮对抗式自审；仍待用户实际试玩验收。
- Git快照：未创建（目录不是Git仓库，且用户未要求提交/快照）。
- 回滚：用原始V8压缩包中的同名源码覆盖上述文件；棋谱原始PGN位于 `%USERPROFILE%\\.xiangqi-coach-data\\Chinese-Chess-Practical-Dataset`，不受本次修改影响。

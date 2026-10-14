# 真实模型测试与质量测评

`scripts/live_deepseek_eval.py` 是显式运行的付费测试工具，普通 pytest 和 CI 不执行它。密钥可存为单行原始 key，或 dotenv 的 `DEEPSEEK_API_KEY` / `LLM_API_KEY`；文件仅作为配置数据读取，不执行其他文字。`env_test.txt` 已被 Git 和 Docker 构建排除。

```powershell
.venv\Scripts\python.exe -X utf8 scripts\live_deepseek_eval.py --env-file env_test.txt
```

默认最多 20 次 completion 请求，SDK 重试为 0：一个旧模型名称探针、四个真实业务生成/审稿请求、十二个写作样本、三次匿名批次评分。模型列表查询不计入 completion 上限。每次生成上限 1600 tokens，评委上限 3000 tokens。`--max-calls` 可调整总请求上限；不足以完成所有阶段时会停止，不会自动补充预算。计数按尝试次数计算，不只统计成功请求。

只连接官方 `https://api.deepseek.com`。固定使用公开 StyleMuse 提交 `1eb1fbd8be3adda5143254e3438dc0755eac0ffa` 下的刘亮程指南、示例、语料和作者配置作为测试素材，不读取本地私有作者资料。该提交必须在本地 Git 对象库中存在。源文件哈希写入协议记录。所有结果默认保存在忽略的 `.stylemuse/evaluations/<时间>/` 下，生产作者目录和任务数据库不修改。

业务模拟启动真实本地 HTTP 服务、SQLite 队列和实际 `AuthorStyleSkill`。显式启用 `review_llm_enabled`，同时保留规则审稿和语料重复检查；默认生产配置并未启用 LLM 审稿。仅文件保存失败由脚本注入，用于检查恢复保存不重复付费调用。未提供 embedding 密钥时验证有警告的降级，不会将 DeepSeek 密钥发送到其他向量服务。业务检查通过并不代表作品质量通过。

质量阶段包含三个主题、每主题四个候选：Flash 通用写作、Flash 指南、Flash 指南＋示例、Pro 指南＋示例。禁止 RAG，保留免费规则评价，另使用 Pro 对隐藏模型和方案名称的随机候选作批次盲评。每组只有三个样本，Pro 自评和同厂商偏差仍存在；评分不能视为准确率或可靠模型排名。

汇总已完成证据不会再次调用模型：

```powershell
.venv\Scripts\python.exe -X utf8 scripts\summarize_live_eval.py <证据目录> <报告目录>
```

证据含调用耗时、用量、提示哈希、正常停止标记、完整正文、规则评分、盲评分、任务和版本数据。密钥不持久化，外部异常仅记录类型与 HTTP 状态码，不记录可能包含凭证的异常正文。费用估算不是账户账单。

如果业务初稿与重写已完整保存、质量阶段尚未开始，可用原 `--output` 加 `--resume` 继续；它复用已记录调用、只完成本地历史恢复检查，然后执行质量测评。质量阶段已产生样本时会拒绝续跑，以免悄悄重复付费。目录默认拒绝覆盖。历史失败证据保留，最终 `summary.json` 决定整体是否完成。

本工具未覆盖真实 embedding 检索、导入风格分析、模型请求期间取消、硬进程崩溃、Docker 或大规模并发。这些需要独立测试协议。

"""Render saved live evidence without making any model requests."""
import argparse
import json
import statistics
from pathlib import Path

LABELS = {"basic": "通用写作基线", "guide": "风格指南", "guide_examples": "指南＋示例"}
DIMENSIONS = {"topic": "扣题", "style": "风格", "naturalness": "自然度", "originality": "原创观察"}


def summarize(source, output):
    def read(name):
        return json.loads((source / name).read_text(encoding="utf-8"))
    samples, calls, simulation, summary = (read(n) for n in ("samples.json", "calls.json", "simulation.json", "summary.json"))
    if not summary.get("completed") or len(samples) != 12 or any("judge" not in s for s in samples):
        raise ValueError("Only completed evaluation evidence can be summarized")
    rows = []
    for model, condition in dict.fromkeys((s["model"], s["condition"]) for s in samples):
        group = [s for s in samples if (s["model"], s["condition"]) == (model, condition)]
        times = [c["seconds"] for c in calls if c["label"] in ["quality:" + s["id"] for s in group]]
        rows.append({"model": model, "condition": condition, "n": len(group),
                     "chars": round(statistics.mean(s["chars"] for s in group), 1),
                     "length_pass": sum(s["length_pass"] for s in group),
                     "latency": round(statistics.mean(times), 2),
                     "rule_score": round(statistics.mean(s["review"]["score"] for s in group), 1),
                     **{d: round(statistics.mean(s["judge"][d] for s in group), 1) for d in DIMENSIONS}})
    # Price ranges use official off-peak/peak rates, not the user's actual bill.
    cost = 0
    by_model = {}
    for call in calls:
        usage = call.get("usage")
        if not usage:
            continue
        model = call["requested_model"]
        cache = usage.get("input_token_details", {}).get("cache_read", 0)
        rates = (0.15, 4.5, 13.5) if model == "deepseek-v4-pro" else (0.02, 1, 4)
        amount = (cache * rates[0] + (usage["input_tokens"] - cache) * rates[1] + usage["output_tokens"] * rates[2]) / 1_000_000
        cost += amount
        total = by_model.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "off_peak_estimate_cny": 0})
        total["calls"] += 1
        total["input_tokens"] += usage["input_tokens"]
        total["output_tokens"] += usage["output_tokens"]
        total["off_peak_estimate_cny"] += amount
    aggregates = {"groups": rows, "usage_by_model": by_model, "estimated_cny_range": [round(cost, 4), round(cost * 2, 4)],
                  "usage_missing_calls": [c["number"] for c in calls if not c.get("usage")],
                  "zero_literal_topic_coverage": sum(s["review"]["requirement"]["metrics"]["topic_coverage"] == 0 for s in samples)}
    (source / "aggregates.json").write_text(json.dumps(aggregates, ensure_ascii=False, indent=2), encoding="utf-8")
    output.mkdir(parents=True, exist_ok=True)
    table = ["| 模型 | 提示方案 | 样本 | 扣题 | 风格 | 自然度 | 原创观察 | 平均字符数 | 篇幅合规 | 平均生成耗时 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        table.append(f"| {row['model']} | {LABELS[row['condition']]} | {row['n']} | {row['topic']} | {row['style']} | {row['naturalness']} | {row['originality']} | {row['chars']} | {row['length_pass']}/3 | {row['latency']} 秒 |")
    checks = ["| 检查 | 结果 |", "|---|---|"] + [f"| {c['name']} | {'通过' if c['passed'] else '失败'} |" for c in simulation["checks"]]
    report = f"""# StyleMuse DeepSeek 真实模拟测试与模型质量测评

日期：2026-10-14。真实调用官方 DeepSeek API，业务模型为 Flash，测评包含 Flash 和 Pro。正文、审稿和评委响应均来自真实模型；仅保存失败由测试脚本注入。本轮完成 20 次模型请求，无自动重试。没有修改生产任务库、原作者语料或用户 `.env`。

## 结论

生成、审稿、任务恢复、历史版本的业务链路可运行，但写作质量控制还有明确缺口。12 个测评样本均被规则审稿判为通过，其中只有 **2/12** 按项目统计口径符合 400–500 字目标；两种模型的指南＋示例组都为 **0/3**。真实重写生成了与初稿完全相同的正文。

本轮 Pro 的指南＋示例组风格均分为 **91**，Flash 同组为 **84**；平均生成耗时约 **13.27 秒与 4.37 秒**。Pro 输出也更长，不能将耗时差异完全归因于模型速度。每组仅三个样本，且 Pro 担任评委，存在同厂商及自评偏差。这是探索性结果，不是可靠模型排名。

## 真实业务模拟

使用隔离作者目录、SQLite 数据库以及实际本地 HTTP 服务调用 `/api/jobs`。业务模拟显式启用 `review_llm_enabled=True`，默认生产配置仍为关闭。流式写作和 LLM 审稿各一次，重写和重新审稿各一次，另有一个旧模型名称探针。用户关闭页面后通过任务编号读取结果的行为由 HTTP 查询模拟；本轮没有重新执行浏览器 UI 测试。历史恢复的最后三项在续跑时通过 Flask 测试客户端完成。

{chr(10).join(checks)}

初稿按项目口径为 {simulation['draft_chars']} 字，重写后仍为 {simulation['rewrite_chars']} 字，正文完全相同。业务检查通过代表响应和持久化正确，不代表改稿质量合格。

初次脚本错误地要求重写比较必须产生非空 diff，因此在质量阶段开始前停止。修正为校验比较接口能返回有效字符串，并将“是否改变正文”单独作为质量观察。续跑复用了原有五次调用结果，没有重发付费生成。原失败记录保留于证据目录，不影响后续已完成结果。

`deepseek-chat` 虽未出现在账户模型列表，探针调用仍成功，不能认定旧名称失效。默认配置改为账户列表和官方文档中的当前名称 `deepseek-flash`，另加入 `deepseek-v4-pro`。

## 测评方法和结果

三个主题为“故乡的狗”“深夜的地铁”“一只旧闹钟”。每主题生成四个候选：Flash 通用写作基线、Flash 风格指南、Flash 指南＋示例、Pro 指南＋示例。通用基线用于观察风格约束的整体作用；只有指南组与指南＋示例组是在同一生成模板中比较示例作用。

生成温度 0.7、非思考模式、输出上限 1600 tokens、零重试。评委 Pro 使用温度 0、输出上限 3000 tokens。四份文章随机排序，仅向评委提供匿名编号、主题、参考指南和示例，隐藏模型名称、提示组和规则分数。排序种子固定；生成本身没有确定性保证。

{chr(10).join(table)}

各维度是模型主观评分，范围 0–100，不是准确率。字数采用项目现有口径：去掉空白后计全部字符，包含标点、英文、数字及模型返回的标题标记。业务审稿和测评均沿用同一口径；若产品所说“字”专指汉字，需要另行统一定义。

现有语料范围内的重复检测为 **12/12 通过**，最长连续重复为 **5–7 个字符**。所有测评生成都正常停止，没有触发 token 长度截断。这些结果只覆盖固定作者语料，不代表全网原创性或语义层面的无借鉴。

## 发现的问题与调整方案

1. **篇幅要求未成为通过门槛。** 规则审稿会报告超长，却仍用加权总分放行；实际 LLM 审稿也将 578 字描述为长度合适。应在审稿提示中传入数值上下限和已计算的字符数，让硬性约束决定 `warn/fail`，风格高分不能抵消超长。重写明确给出需删减的字数，并在保存前复核，避免无上限自动重试。
2. **中文扣题规则误报。** {aggregates['zero_literal_topic_coverage']}/12 文章未逐字包含完整主题，`topic_coverage` 被算为 0，但正文讨论对应核心事物，盲评扣题分为 82–92。例如 S02 全文写故乡的黑狗，字面规则却判“可能偏题”。应使用分词/核心概念覆盖作辅助信号，结合结构化语义审稿；完整字符串匹配不能单独决定扣题。
3. **规则风格分缺乏辨别力。** 通用基线规则均分 87.3，高于风格指南组的 81.0；盲评风格均分却是 71.7 对 82.7。句长、标点和常用字只能用作描述指标，不能等价于文学风格。需要用正反例与人工标注校准评分。
4. **重写可返回原文。** 本轮原文和重写 SHA-256 相同，篇幅问题仍存在。应检测改稿无变化并明确提示未完成修改；把审稿建议转化为可验证的修改目标。不要仅凭 API 调用成功或创建新版本宣称优化成功。
5. **示例作用不稳定。** Flash 风格均分在加入示例后从 82.7 到 84.0，但原创观察从 79.3 到 78.3；主题间有升有降。S03 直白讲“人应向狗学习”，S06 的抽象比喻较密；S08 又沿用了指南里的风与土墙意象。应减少对固定意象的依赖，加入不使用乡村符号也能体现叙事节奏的示例，并检查提示文本本身的语义复用。
6. **真实 RAG 尚未测到。** 只有聊天模型密钥，未提供向量服务配置。业务模拟验证了检索失败时警告并使用指南和示例继续生成；质量测评显式关闭检索。后续需要有效 embedding 配置，单独测召回质量及 RAG 对写作的净增益，不能把这次结果标为 RAG 测评通过。

建议先处理前三项审稿可信度问题和改稿无变化检测，再扩展到更多作者、至少 20 个主题、每主题多次采样，并加入匿名人工评审及不同厂商评委。Flash 可继续作为低延迟候选；本轮 Pro 的风格评分更高，但篇幅控制更弱，不能直接当作可替换上线的结论。

## 调用、成本和验证范围

20/20 模型请求返回成功。19 次取得 token 用量：输入 **{summary['input_tokens']:,}**、输出 **{summary['output_tokens']:,}**。首个旧模型名称探针未记录 usage，输出上限为 8 tokens，因此这些用量不是账户总账单。

按[官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)和已记录缓存命中量估算，19 次调用约 **¥{cost:.4f}–¥{cost*2:.4f}**（空闲/高峰范围）；不含首个探针，不代表实际扣款。没有查询账户余额。

数据只来自用户指定的[公开项目](https://github.com/MissingDanial/StyleMuse)固定提交 `1eb1fbd8be3adda5143254e3438dc0755eac0ffa` 中的指南、示例、作者配置和作品。审批初次拒绝了可能包含私有语料的脚本；核对公开来源并改为从固定 Git 提交读取之后获准执行。没有发送本地私有作家文件。密钥文件已加入 Git 和 Docker 排除规则，密钥没有写入测试配置、数据库、报告或调用记录。

接入修改包括当前模型预设、仅对官方 DeepSeek 域名发送非思考模式参数与流式用量选项。其他 OpenAI 兼容端点不发送 DeepSeek 参数。原 `.env` 保持原状。最终回归检查：**160 个 Python 测试、13 个 JavaScript 测试通过**，Waitress 三个健康检查通过。测试证据的全部 25 个文件（包括 SQLite）经二进制密钥扫描通过。

本轮未验证完整向量检索、导入后的真实模型风格分析、付费调用中断取消、硬进程崩溃、Docker 部署或大规模并发。Worker 重建与保存失败恢复已测，不能等同于这些场景。

## 证据文件

原始证据目录：`{source.resolve()}`。包含 `protocol.json`（协议与公开文件哈希）、`calls.json`（调用耗时和用量）、`simulation.json`（业务检查）、`simulation_original.json`、`simulation_rewrite.json`、`samples.json`（12 篇正文与评分）、`summary.json` 和 `aggregates.json`。完整样本另见同目录交付的 `DeepSeek完整测评样本.md`。
"""
    (output / "DeepSeek真实测试与质量测评报告.md").write_text(report, encoding="utf-8")
    texts = ["# DeepSeek 完整测评样本", "", "所有正文和评分均为本轮真实模型返回；编号保留便于核对原始记录。", ""]
    for sample in samples:
        judge = sample["judge"]
        texts += [f"## {sample['id']} · {sample['topic']} · {sample['model']} · {LABELS[sample['condition']]}", "",
                  f"字符数：{sample['chars']}；规则分：{sample['review']['score']}；连续重复：{sample['plagiarism']['max_common']} 字符。",
                  "盲评分：" + "，".join(f"{label} {judge[d]}" for d, label in DIMENSIONS.items()) + "。", "",
                  "评委意见：" + judge["reason"], "", sample["article"], ""]
    (output / "DeepSeek完整测评样本.md").write_text("\n".join(texts), encoding="utf-8")
    print(json.dumps({"groups": rows, "estimated_cny_range": aggregates["estimated_cny_range"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    summarize(args.source, args.output)

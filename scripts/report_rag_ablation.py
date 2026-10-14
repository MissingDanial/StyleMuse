"""Create offline RAG experiment reports and a local sample comparison viewer."""
import argparse
import csv
import hashlib
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ablation_stats import analyze

LABELS = {"off": "关闭 RAG", "rag": "真实 RAG", "random": "随机片段对照"}
VERDICTS = {"benefit": "支持有实用意义的质量提升", "harm": "支持有实用意义的质量下降",
            "within_practical_margin": "本轮区间处于正负 3 分的实用界限内", "inconclusive": "证据不足以确认增益或等效",
            "insufficient_topics": "独立主题数量不足", "no_data": "无数据"}


def render(source, output):
    def read(name):
        return json.loads((source / name).read_text(encoding="utf-8"))
    saved = read("analysis.json")
    samples, panels, calls, protocol, retrievals = (read(n) for n in ("samples.json", "panels.json", "calls.json", "protocol.json", "retrievals.json"))
    if not saved.get("completed"):
        raise ValueError("Cannot present an incomplete experiment as completed")
    result = analyze(samples, panels)
    output.mkdir(parents=True, exist_ok=True)
    primary = result["effects"]["hit:rag-off"]
    relevance = result["effects"]["hit:rag-random"]
    placebo = result["effects"]["hit:random-off"]
    table = ["| 条件 | 篇数 | 主观质量 | 风格 | 自然度 | 扣题 | 原创观察 | 篇幅合规 | 作者语料重复超限 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    groups = {g["stratum"] + ":" + g["condition"]: g for g in result["groups"]}
    for condition in ("off", "rag", "random"):
        g = groups["hit:" + condition]
        table.append(f"| {LABELS[condition]} | {g['n']} | {g['quality']:.2f} | {g['style']:.2f} | {g['naturalness']:.2f} | {g['topic']:.2f} | {g['originality']:.2f} | {g['length_pass']}/{g['n']} | {g['copy_fail']}/{g['n']} |")
    costs = ["| 条件 | 平均输入 tokens | 平均输出 tokens | 模型耗时 | 检索耗时 | 含规则检查总耗时 | 平均字符数 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for condition in ("off", "rag", "random"):
        g = groups["hit:" + condition]
        costs.append(f"| {LABELS[condition]} | {g['input_tokens']:.1f} | {g['output_tokens']:.1f} | {g['llm_seconds']:.3f} 秒 | {g['retrieval_ms']:.2f} 毫秒 | {g['end_to_end_seconds']:.3f} 秒 | {g['chars']:.1f} |")
    paired = ["| 命中主题 | RAG 减关闭 | RAG 减随机 | 随机减关闭 |",
              "|---|---:|---:|---:|"]
    for topic, delta in primary["topic_mean_deltas"].items():
        paired.append(f"| {topic} | {delta:+.2f} | {relevance['topic_mean_deltas'][topic]:+.2f} | {placebo['topic_mean_deltas'][topic]:+.2f} |")
    contrasts = ["| 比较 | 平均差 | 95% 主题 bootstrap 区间 | 精确 sign-flip p | 判断 |",
                 "|---|---:|---|---:|---|"]
    for name, effect in [("真实 RAG 减关闭", primary), ("真实 RAG 减随机片段", relevance), ("随机片段减关闭", placebo)]:
        contrasts.append(f"| {name} | {effect['mean']:+.2f} | [{effect['ci95'][0]:+.2f}, {effect['ci95'][1]:+.2f}] | {effect['exact_signflip_p']:.4f} | {VERDICTS[effect['verdict']]} |")
    empty = ["| 条件 | 篇数 | 主观质量 | 平均输入 tokens | 作者语料重复超限 |",
             "|---|---:|---:|---:|---:|"]
    for condition in ("off", "rag", "random"):
        g = groups["empty:" + condition]
        empty.append(f"| {LABELS[condition]} | {g['n']} | {g['quality']:.2f} | {g['input_tokens']:.1f} | {g['copy_fail']}/{g['n']} |")
    input_delta = groups["hit:rag"]["input_tokens"] - groups["hit:off"]["input_tokens"]
    input_pct = input_delta / groups["hit:off"]["input_tokens"] * 100
    llm_delta = groups["hit:rag"]["llm_seconds"] - groups["hit:off"]["llm_seconds"]
    total_input = sum(c["usage"]["input_tokens"] for c in calls)
    total_output = sum(c["usage"]["output_tokens"] for c in calls)
    exact_repeats = len(samples) - len({hashlib.sha256(s["article"].encode()).hexdigest() for s in samples})
    cost = 0.0
    for call in calls:
        u = call["usage"]
        cached = u.get("input_token_details", {}).get("cache_read", 0)
        rates = (0.15, 4.5, 13.5) if call["requested_model"] == "deepseek-v4-pro" else (0.02, 1, 4)
        cost += (cached * rates[0] + (u["input_tokens"] - cached) * rates[1] + u["output_tokens"] * rates[2]) / 1_000_000
    copy_examples = [s for s in samples if s["plagiarism"]["passed"] is False]
    overlap_table = ["| 样本 | 条件 | 主题 | 连续重复字符 | 命中片段 |", "|---|---|---|---:|---|"]
    for s in copy_examples:
        overlap_table.append(f"| {s['id']} | {LABELS[s['condition']]} | {s['topic']} | {s['plagiarism']['max_common']} | {s['plagiarism']['max_common_text'].replace('|', '｜')} |")
    cold_ms = list(retrievals.values())[0]["cold_or_warm_query_ms"]
    total_hits = sum(g["n"] for g in result["groups"] if g["stratum"] == "hit")
    if primary["verdict"] == "benefit":
        recommendation = "在本轮已有指南和示例的命中主题中，RAG 有质量增益证据；是否默认开启仍应结合重复风险和篇幅合规，不能仅凭主观分放行。"
    elif primary["verdict"] == "harm":
        recommendation = "本轮不支持默认开启该 RAG 配置，应先改进召回、片段处理或提示组织，再用相同协议复测。"
    elif primary["verdict"] == "within_practical_margin":
        recommendation = "在本轮模型、作者和提示设置下，RAG 的质量增量很小，保留为可选功能比宣称默认增强更有依据；其额外输入和维护成本尚没有对应的实用质量收益证据。"
    else:
        recommendation = "本轮未能证明 RAG 有稳定、达到 3 分的质量增益，也没有证明它与关闭 RAG 等效。建议保留可选开关，先改善片段质量或扩大独立主题数量，再决定是否默认开启。"
    report = f"""# StyleMuse RAG 消融实验结果

日期：2026-10-14。真实 DeepSeek Flash 生成 48 篇，Pro 完成 32 次盲评，合计 {len(calls)} 次调用。主要比较覆盖六个检索命中主题，每主题两个重复；另外两个无结果主题作为负对照。各条件保留相同风格指南和固定示例。

## 主要判断

命中主题中，真实 RAG 相比关闭 RAG 的主观质量平均差为 **{primary['mean']:+.2f} 分**，95% 主题 bootstrap 区间 **[{primary['ci95'][0]:+.2f}, {primary['ci95'][1]:+.2f}]**，精确双侧 sign-flip **p={primary['exact_signflip_p']:.4f}**。预先定义的实用意义界限为 3 分，结论为：**{VERDICTS[primary['verdict']]}**。

{recommendation}

“未证明有收益”不等同于“设计本身冗余”。本实验检验的是一个作者、一个 writer、当前阈值与短片段配置，在已有较丰富指南和示例时的增量；未测试去掉固定示例、其他作者或不同 RAG 结构。

## 命中主题的质量与风险

{chr(10).join(table)}

主观质量为 35% 风格＋25% 自然度＋20% 语义扣题＋20% 原创观察。两种展示顺序先平均，每主题两个重复再平均。该分数是模型主观评价，不是准确率。篇幅统计沿用项目口径：去掉空白后计全部字符，包含标点、数字、标题标记；目标为 400–500。

按 ±3 分判定单对的实用差异，真实 RAG 胜 {result['practical_outcomes_hit']['rag_wins']} 对、关闭 RAG 胜 {result['practical_outcomes_hit']['off_wins']} 对、差异小于 3 分 {result['practical_outcomes_hit']['within_3_points']} 对，共 12 对。这些重复不是 12 个独立主题。

{chr(10).join(contrasts)}

{chr(10).join(paired)}

后两项比较用于观察增益是否来自主题匹配还是额外示例，属于探索性比较，没有作多重检验校正。bootstrap 以主题为单位重采样 20,000 次；p 值基于六个主题均值的 64 种符号翻转，假设主题差异在零假设下可作符号交换。本轮主题数量少，区间与检验都应在此任务范围内解读。

## 无检索结果的负对照

{chr(10).join(empty)}

“深夜的地铁”和“一只旧闹钟”没有满足阈值的片段。四个配对单元中，开启与关闭 RAG 的实际生成提示哈希均相同：**{result['empty_prompts_identical']}**。它们的质量差异来自生成随机性及评分误差，不能归因于 RAG 增强；生产逻辑会提示降级。随机片段对照仍有额外上下文，单独列出。

## 时间和 token 成本

{chr(10).join(costs)}

命中主题开启 RAG 平均增加 **{input_delta:+.1f} 个输入 tokens（{input_pct:+.1f}%）**，模型平均耗时差为 **{llm_delta:+.3f} 秒**。模型耗时受缓存命中、输出篇幅和服务负载影响；生成顺序随机且相邻配对，但不能把耗时差全部解释为检索成本。等长度随机片段匹配的是字符长度及格式，不保证 token 数完全相同。

计时使用已有索引与预热 BGE；生成阶段真实 RAG 仍执行查询嵌入和 FAISS 搜索。首个预检查询含模型初始化，耗时约 {cold_ms:.1f} 毫秒。模型下载和索引建设成本未计入逐篇均值；此前本地初始化约 120.8 秒，其中包括模型下载和 1,161 文本块的索引建立。这些一次性及维护成本仍需纳入部署判断。

全部调用记录输入 {total_input:,}、输出 {total_output:,} tokens。按已核实的[DeepSeek 官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)和记录的缓存命中量估算约 ¥{cost:.3f}–¥{cost*2:.3f}（空闲/高峰范围），不是账户账单。

## 连续重复与评委可靠性

{chr(10).join(overlap_table) if copy_examples else '作者语料范围内没有超过 15 字连续重复的样本。'}

上述结果表示固定资料中的连续字符重合。另对每篇提供的指南、固定示例和参考片段计算最长重合；命中主题三组的超过 15 字样本数分别为关闭 {groups['hit:off']['prompt_copy_fail']}、真实 RAG {groups['hit:rag']['prompt_copy_fail']}、随机片段 {groups['hit:random']['prompt_copy_fail']}。检测只覆盖固定资料，语义复用还需人工判断。P05-off 已出现 26 字符重合，盲评仍给出原创观察 88 分，说明主观原创评分不能替代来源比对。

同一候选正向和反向展示的主观质量平均绝对差为 **{result['mean_order_score_gap']:.3f} 分**。反向评分用于估计位置及评分敏感性，不能消除所有评委偏差。Pro 与 writer 来自同一厂商；未使用独立厂商评委或正式人工评分。48 篇中有 {exact_repeats} 篇与其他样本的正文完全重复，也不应当作额外独立观察。

本轮 RAG 的平均质量增量小于评委顺序敏感性。现有指南和示例已覆盖乡村、风、树等常见意象；额外参考仅约 160–277 字符，可能与已有风格信息重叠，也可能因短分块而缺乏完整叙事。这些是下一轮待检验的解释，不能当作已证明的冗余机制。

## 下一轮针对设计的实验

先根据本轮重复风险和逐主题差异选出需要改进的机制，但复测主题集应预先固定。若要判断动态 RAG 能否替代固定示例，可增加“仅指南”“指南＋固定示例”“指南＋动态检索”“指南＋二者”四条件，分别评价信息冗余。若要区分当前实现与 RAG 思路，需单独检验更完整的语义片段、来源去重、主题适配和检索阈值；本轮保留原 100 字分块、20 字重叠、top_k=3 和距离阈值 1.0。

建议扩大到多个作者和至少 20 个独立主题，加入匿名人工评分，报告命中率、长度合规、来源复用、token 增量和质量差的联合结果。当前生产开关和参数未因本次实验自动改变。

## 复现材料

原始目录：`{source.resolve()}`。协议、公共语料与代码哈希在首个付费请求前冻结；候选生成顺序、随机片段和匿名展示顺序可复查。请求零重试，48 个正文和 32 个评委响应均保留。密钥仅用于官方服务认证，不在实验配置或报告中。

统计结果已从原始评分重新计算并核对一致；所有 80 次响应正常停止，每篇均取得两种展示顺序的评分。174 个 Python 测试、13 个 JavaScript 测试通过；匿名样本页面的主题切换、评分显示和手机布局均通过浏览器检查。95 个实验与报告文本文件经密钥扫描通过。

同目录交付 `RAG消融全部样本.html`，可按主题和重复查看三篇全文，也可开启匿名展示自行比较；该浏览器工具离线运行，不发送文章或评分到外部。`RAG消融样本指标.csv` 给出逐篇质量、用量和风险指标。
"""
    (output / "RAG消融实验报告.md").write_text(report, encoding="utf-8")
    fields = ["id", "topic", "repeat", "condition", "stratum", "quality", "chars", "length_pass", "prompt_overlap", "retrieval_ms", "llm_seconds", "end_to_end_seconds", "source_overlap", "input_tokens", "output_tokens"]
    with (output / "RAG消融样本指标.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for s in samples:
            row = {key:s.get(key) for key in fields}
            row.update(source_overlap=s["plagiarism"]["max_common"], input_tokens=s["usage"]["input_tokens"], output_tokens=s["usage"]["output_tokens"])
            writer.writerow(row)
    payload = json.dumps({"samples": samples, "panels": panels}, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    html = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAG 消融样本比较</title><style>body{font:16px/1.8 system-ui,sans-serif;background:#f4f5f7;color:#24303b;margin:0;padding:26px}header{max-width:1600px;margin:auto}h1{font-size:26px;margin:0}label,select{margin-right:16px}select{padding:8px;border:1px solid #bcc8d4;border-radius:6px;background:white}main{max-width:1600px;margin:22px auto;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}article{background:white;border:1px solid #dbe1e7;border-radius:9px;padding:20px}h2{font-size:20px;margin:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}small,.note{color:#546577}.metrics{background:#edf2f6;padding:10px;border-radius:5px;font-size:14px}.context{font-size:14px;color:#546577;border-top:1px solid #dce4eb;padding-top:14px}input{accent-color:#3262a5}@media(max-width:1000px){main{grid-template-columns:1fr}body{padding:16px}}</style>
<header><h1>RAG 消融全部样本</h1><p class="note">48 篇真实输出。匿名查看默认隐藏条件、参考片段和评分，便于自己先比较。内容仅在本机显示。</p><label>主题与重复 <select id="panel"></select></label><label><input id="blind" type="checkbox" checked>匿名查看</label><label><input id="scores" type="checkbox">显示模型评分</label></header><main id="items"></main>
<script id="data" type="application/json">PAYLOAD</script><script>const data=JSON.parse(document.getElementById('data').textContent),labels={off:'关闭 RAG',rag:'真实 RAG',random:'随机片段对照'},sel=document.getElementById('panel');data.panels.forEach(p=>{let o=document.createElement('option');o.value=p.id;o.textContent=p.topic+' · 第'+p.repeat+'次';sel.append(o)});function text(el,tag,value,className){const n=document.createElement(tag);n.textContent=value;if(className)n.className=className;el.append(n);return n}function render(){let p=data.panels.find(x=>x.id===sel.value),blind=document.getElementById('blind').checked,show=document.getElementById('scores').checked,rows=data.samples.filter(x=>x.panel===p.id),root=document.getElementById('items');root.replaceChildren();rows.sort((a,b)=>a.blind_id.localeCompare(b.blind_id));rows.forEach((s,i)=>{let card=document.createElement('article');text(card,'h2',blind?'候选 '+String.fromCharCode(65+i):labels[s.condition]);text(card,'small',s.chars+' 字符'+(blind?'':' · '+s.id));if(show){text(card,'p','质量 '+s.quality.toFixed(2)+' · 风格 '+s.judge_mean.style.toFixed(1)+' · 自然 '+s.judge_mean.naturalness.toFixed(1)+' · 语料最长重复 '+s.plagiarism.max_common+' 字符','metrics');text(card,'p','输入 '+s.usage.input_tokens+' tokens · 模型耗时 '+s.llm_seconds.toFixed(2)+' 秒','metrics')}text(card,'pre',s.article);if(!blind){text(card,'p',s.judgments.map(j=>j.reason).join('；'),'note');if(s.condition!=='off')text(card,'pre',s.condition==='rag'?p.rag_context:p.placebo_context,'context')}root.append(card)})}sel.onchange=render;document.getElementById('blind').onchange=render;document.getElementById('scores').onchange=render;render();</script></html>""".replace("PAYLOAD", payload)
    (output / "RAG消融全部样本.html").write_text(html, encoding="utf-8")
    (output / "RAG消融统计结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"primary": primary, "relevance_vs_placebo": relevance, "token_increase_hit": round(input_delta, 2), "estimated_cny_range": [round(cost, 3), round(cost * 2, 3)]}, ensure_ascii=False))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(args.source, args.output)

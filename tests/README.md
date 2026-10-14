# 测试说明

在项目根目录执行，先安装 `requirements.lock`。以下测试使用模拟模型，不需要真实 API 密钥，不执行付费模型请求。

```bash
python -m pytest -q
node --test tests/frontend.test.cjs tests/tasks.test.cjs
python scripts/smoke_check.py
python scripts/smoke_check.py --production
```

`python` 应为虚拟环境解释器，Windows 未激活环境时使用 `.\.venv\Scripts\python.exe`。JavaScript 测试使用 Node.js 22 或以上的内置测试工具，无需安装 npm 依赖。GitHub Actions 在 Python 3.12 / Node.js 22 上运行上述检查。

Python 测试覆盖缓存更新、来源位置与复用证据、模型配置、文件边界、任务恢复、版本历史、语义候选和人工意见等。语义单元测试使用模拟向量，不下载 BGE；真实本地模型验证使用 `scripts/verify_rag.py` 和 `scripts/evaluate_semantic_reuse.py`，需先按 [嵌入文档](../docs/EMBEDDINGS.md) 安装初始化。

## 浏览器 QA

安装可选依赖：

```bash
python -m pip install -r requirements-browser.txt
```

Windows 脚本使用已经安装的 Microsoft Edge。Linux / macOS 使用 Playwright Chromium，首次需要下载浏览器：

```bash
python -m playwright install chromium
```

然后执行：

```bash
python tests/run_browser_qa.py
```

启动器在 `127.0.0.1:5367` 运行隔离测试服务，使用测试口令 `browser-password` 与模拟写作服务。请确保该端口空闲。覆盖登录、生成中刷新、来源证据、人工意见保存与刷新、篇幅失败、重写、历史对比、恢复版本、任务列表和手机布局。

结果及截图默认写入 `.tmp/browser-qa/`，测试数据库和工作区也位于 `.tmp/`。如需保留多次输出，可在运行前把 `STYLEMUSE_QA_OUTPUT` 设为另一个 `.tmp/` 子目录。测试结束后启动器关闭服务，生产作家资料和任务数据库不修改。

浏览器 QA 不在当前 CI 工作流中运行。付费测评脚本也不属于本目录的默认测试，见 [真实模型测试](../docs/LIVE_TESTING.md) 和 [RAG 消融协议](../docs/RAG_ABLATION.md)。

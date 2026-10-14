# StyleMuse 后台任务与作品历史

StyleMuse 0.4 将 Web 写作、重写和语料导入移入持久化任务队列，加入作品库和版本恢复。提交后可关闭页面，任务继续执行；重新打开页面会查询已有任务，不通过再次生成来恢复界面。

## 任务与版本的一致性

SQLite 保存任务状态、生成草稿、质量报告、事件和正式作品版本。正文生成完成后先记录检查点，再执行查重、审稿和文件导出。正式版本插入、作品最新版本指针更新与任务完成状态在同一数据库事务中提交。

正文、审稿和版本 JSON 导出使用固定的版本 UUID 文件名，失败可重复执行。导出失败或数据库提交失败时，作品库不会显示一个已完成的新版本；草稿和已完成检查保留，继续处理时可修复导出，避免再次生成正文。部分导出文件可能暂时存在，历史迁移会识别 job_id 并跳过未提交的任务文件。

每个请求提供 Idempotency-Key。相同编号及相同内容返回已有任务；编号被用于不同内容时返回 409。取消、重写、恢复及所有查询沿用现有访问密码校验。重写从服务端读取原稿与审稿意见，客户端不能提交伪造正文或父版本。恢复旧稿创建新版本，原稿保持不变。

## 运行方式

本机直接运行：

```bash
python app.py
```

生产启动：

```bash
waitress-serve --host=0.0.0.0 --port=5000 --threads=8 --call app:create_app
```

默认在 Web 进程内启动一个独立后台任务线程。等待模型不占用请求线程。需要独立进程时，在 .env 设置 STYLEMUSE_EXTERNAL_WORKER=1，使用另一个进程运行 `python scripts/worker.py`。没有 worker 时，任务会保持排队状态。

SQLite 使用 WAL 和事务领取任务。同一作家的任务按顺序执行，多个 worker 不会同时处理同一作家；队列最多容纳 30 个等待或运行中的任务。工作进程每 5 秒更新心跳，约 45 秒失去心跳后运行中的任务标记为中断。排队但尚未开始的任务可在启动后继续执行；中断的生成任务不自动重新调用模型。

取消会在流式片段或处理阶段间检查标记。正在执行的外部模型请求无法立即强制终止，需要等待其返回或超时。取消后保留已持久化草稿，正式版本不提交。

## 页面使用

- 写作工坊可显示后台进度、取消任务，或继续处理有完整正文的失败任务。
- 任务记录可查看最近 30 个任务，包括导入任务和中断任务。
- 作品库可按主题和作家筛选，打开任意已保存版本，查看正文及版本关系。
- 对比以同一作品的两个版本为单位，提供并排正文和逐行变化。
- 恢复生成新的“恢复稿”，不调用写作模型，原有版本仍可查看。

浏览器仅保存任务编号、提交编号和最近版本编号；正文以 SQLite 为准。提交响应丢失时可按提交编号查找任务，重试会使用原编号。前端默认使用短轮询获取状态；另有 SSE 接口支持事件序号续传和心跳。

## HTTP API

| 请求 | 用途 |
| --- | --- |
| POST /api/jobs | 提交 write、rewrite 或 restore 任务，要求 Idempotency-Key |
| GET /api/jobs | 查询任务记录 |
| GET /api/jobs/任务编号 | 查询进度、持久化正文及结果 |
| GET /api/jobs/by-key/提交编号 | 查找响应丢失的提交 |
| POST /api/jobs/任务编号/cancel | 请求取消 |
| POST /api/jobs/任务编号/resume | 继续完整正文的失败或中断任务 |
| GET /api/jobs/任务编号/events?after=序号 | 查询后续事件与任务快照 |
| GET /api/jobs/任务编号/stream | SSE 事件续传，可用 Last-Event-ID |
| POST /api/import-jobs | multipart 文件导入，要求 Idempotency-Key |
| GET /api/articles | 作品列表，支持 author、search、offset、limit |
| GET /api/articles/作品编号 | 作品及完整版本列表 |
| GET /api/versions/版本编号 | 读取不可变版本 |
| GET /api/articles/作品编号/compare?left=版本&right=版本 | 同一作品的版本对比 |

write 提交示例：

```json
{"kind":"write","author":"liu_liangcheng","topic":"故乡的狗","length":"medium","include_retrieval":true}
```

rewrite 或 restore 提交示例：

```json
{"kind":"rewrite","version_id":"已保存版本的UUID"}
```

新任务首次提交返回 202，重复提交返回 200。客户端原参数沿用第一份成功提交的任务；查重或审稿未知状态不会被视为通过。旧同步写作、流式写作和导入接口继续保留兼容，Web 页面已改用新任务 API。

## 数据保存与旧稿迁移

保留 `.stylemuse/` 和 `authors/` 两个目录。前者包含 SQLite、事件和未完成导入的暂存文件，后者包含源文、作家配置与导出作品。Docker Compose 已同时挂载这些目录，重建容器不会把它们留在容器可写层。

已有 TXT 文件会在作品库查询时索引为历史稿，有 review 与 version JSON 时一并读取；根据父版本关系恢复版本链。同一路径只导入一次，原文件不修改。任务产生的导出稿只由数据库事务确认，不作为旧稿重复导入。

成功导入和重复上传的临时副本会清理；失败或中断导入保留原暂存文件便于排查。旧向量索引、归档、任务事件和正式历史不自动删除。备份时可停止服务和 worker 后复制完整 `.stylemuse/` 与 `authors/`；不要仅复制正在写入的 SQLite 主文件。

当前是本机 SQLite 队列和共享密码模式，尚未加入多用户数据隔离、分布式数据库、任务优先级或真实模型质量评测。配置仍按重启生效，模型及凭据在工作进程执行时读取运行配置。

## 验证

```bash
python -m pytest -q
node --test tests/frontend.test.cjs tests/tasks.test.cjs
python scripts/smoke_check.py
python scripts/smoke_check.py --production
```

本次使用 Python 3.12，在 Windows 上验证 147 项 Python 测试和 13 项前端逻辑测试。覆盖幂等提交、队列领取、取消、活跃工作进程租约、服务中断、短草稿保留、导出重试、事务回滚、归属校验、历史迁移及导入任务。

真实浏览器以本地模拟模型验证登录、生成中刷新、重写、历史查询、对比、恢复、任务列表及移动布局。浏览器 QA 可选依赖为 requirements-browser.txt：Windows 使用本机 Edge，无界面运行；其他系统需安装 Playwright Chromium。

```bash
pip install -r requirements-browser.txt
# 非 Windows 环境需先运行 python -m playwright install chromium
python tests/run_browser_qa.py
```

截图和结果默认保存在 `.tmp/browser-qa/`，可通过 STYLEMUSE_QA_OUTPUT 指定目录。QA 使用临时作家、模拟模型与本地向量，不调用付费服务。生产 Waitress 启动检查已验证；Docker 引擎在本机不可用，容器构建和云端 CI 尚未运行。

# 课程注册系统

成员 1–4 的统一功能交付版本：FastAPI + SQLAlchemy + MySQL / SQLite + Jinja2 页面。学生、教师和教务页面都调用真实业务服务，外部课程目录与计费接收端为独立模拟系统。

## 快速启动

在项目根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m scripts.run_demo
```

已有虚拟环境时直接执行最后一条。打开 `http://127.0.0.1:8000/login`。启动命令会完成迁移、初始化演示数据并启动主应用、只读课程目录、计费服务和后台重试。默认 SQLite 方便本机复现，Ctrl+C 停止本次启动的服务。

教务 `registrar`，学生 `S001`–`S010`，教师 `T001`–`T010`；初始密码均为 `Initial123`，首次登录必须改密。重复启动保留现有数据和密码。使用 MySQL 8 时：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_demo --database-url 'mysql+pymysql://用户名:密码@127.0.0.1:3306/数据库名?charset=utf8mb4'
```

数据库需预先创建。完整步骤、端口选项、账号、验证证据与文档入口见 [最终版运行与验证说明](docs/最终版运行与验证说明.md)。

## 功能

- 登录、退出、首次改密、Cookie 会话、角色与 CSRF 校验；账号停用立即使旧会话失效。
- 只读外部目录查询，真实名额轮询；个人草稿保存、主备选类型与顺序、首次 4+2、后续增退选、换班、退课、删除方案与版本冲突处理。
- 先修课、同课程重复修读、时间冲突、容量校验；事务失败保留原正式结果，MySQL 锁后当前读防止超额。
- 教师授课资格、认领与取消、真实名单、上一已完成学期成绩录入/修改/留空、成绩变更记录和学生本人查询。
- 完整师生资料维护、自动分配唯一编号、启停、重置密码；有业务历史时删除转为停用。
- 教务真实关闭、两轮取消与备选、固定课表和金额快照；真实账单状态、自动重试、人工恢复、计费端持久去重。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

真实 MySQL 竞争检查必须配置独立测试库：

```powershell
$env:MYSQL_TEST_DATABASE_URL='mysql+pymysql://用户名:密码@127.0.0.1:3306/course_registration_test?charset=utf8mb4'
.\.venv\Scripts\python.exe -m pytest -q
```

测试库名称必须以 `_test` 结尾；测试会创建/删除该库的测试表，不能使用业务数据库。未配置时五项 MySQL 用例跳过。最终结果与本轮浏览器流程记录见运行说明；不把 SQLite 测试当作 MySQL 并发证明。

## 结构

```text
app/models.py                  数据结构
app/services/registration.py   选课与关闭事务
app/services/auth.py           账号与会话
app/services/personnel.py      师生资料
app/services/teaching.py       教师与成绩
app/services/catalog.py        只读外部目录适配
app/services/billing.py        账单派发与重试
app/routers/                   真实 API 与页面适配
web/                           三种角色页面和请求交互
mock_catalog/                  独立目录模拟系统
mock_billing/                  独立计费模拟系统
migrations/                    数据库迁移
scripts/run_demo.py            完整启动入口
scripts/seed_final_demo.py      最终版演示数据
tests/                         统一回归测试（auth、teaching、集成与并发）
docs/课程材料/                  原始题目、要求、封面、需求草稿与分工
docs/验证记录/                  最终版实际页面验证截图和记录
```

成员 5、6 按当前代码编写正式文档。阅读入口见 [成员 1–4 代码阅读清单](docs/成员1-4最终版代码阅读清单.md)。旧阶段报告和重复启动入口已清理，历史版本可从 Git 提交记录恢复；当前运行入口统一为 `scripts.run_demo`。本轮统一修复由 AI 执行，不能表述为用户独立开发或所有成员已掌握全部代码。

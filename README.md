# 课程注册系统

这是软件工程课程设计的 FastAPI 课程注册系统。成员 1–4 的代码已集成，包括核心选课、网页、登录与师生管理、教师授课与成绩、计费对接。

**当前仍未完成全部网页联调**：学生选课和教务关闭/账单状态页面仍调用模拟接口；真实后端已存在。不得把这些页面的成功提示作为选课落库或账单发送成功的证据。最新检查与待办见 [成员1–4集成检查报告](docs/成员1-4集成检查报告_2026-10-08.md)。

## 已完成

- 草稿与正式结果分开保存，保存草稿不占名额。
- 首次提交严格校验 4 个主选和 2 个备选；后续允许 0-4 个主选和 0-2 个备选。
- 校验班次状态、容量、先修课、上课冲突和同课程重复修读。
- 提交、换班式修改、退课和删除方案均在事务中执行；版本号阻止旧页面覆盖新状态。
- 关闭选课采用已确认的两轮处理，取消无教师/人数不足班次，按排队顺序尝试备选。
- 关闭事务内固定最终课表和金额，创建待发送账单；成员 4 已提供计费接收模拟服务、发送和后台重试。
- 成员 3 已接入正式 Cookie 会话、首次改密、角色权限和师生账号管理。
- 成员 4 已接入真实教师授课、名单、成绩与成绩变更记录接口及对应页面。

## 本地运行	

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

若暂时没有 MySQL，可把 `.env` 中的 `DATABASE_URL` 改为：

```text
DATABASE_URL=sqlite:///./course_registration.db
```

然后执行：

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000/login` 使用网页，或打开 `http://127.0.0.1:8000/docs` 查看真实接口。登录后浏览器会保存 `HttpOnly` 会话 Cookie；网页公共请求函数会提交 CSRF Token，但成员 1 的真实写入口仍需补后端校验。约定见 [接口文档](docs/接口约定v0.2.md) 和 [成员4接口说明](docs/成员4接口说明.md)。

计费功能另需启动模拟服务与重试进程（在另两个终端、项目根目录运行）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn mock_billing.mock_billing_service:app --host 127.0.0.1 --port 8082
.\.venv\Scripts\python.exe -m scripts.billing_retry_worker
```

成员 4 演示数据和详细运行步骤见 [成员4运行验证说明](docs/成员4运行验证说明.md)。目录模拟服务可运行 `python -m uvicorn mock_catalog.mock_catalog_service:app --host 127.0.0.1 --port 8081`，但当前应用尚未接入它。

## 初始化教务账号

首次使用空数据库时，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.init_registrar --login-number registrar
```

脚本会安全地提示输入初始密码，不会回显或输出密码。也可为部署环境设置 `INITIAL_REGISTRAR_PASSWORD` 环境变量后执行同一命令。脚本可重复运行：若教务账号已存在，会保留原账号和密码。

普通学生、教师的初始密码：当前统一为  **`Initial123`** 。

## 可选：初始化答辩演示数据

初始化教务账号后，可批量补充 10 名随机姓名的演示学生、10 名随机姓名的演示教师和 3 门演示课程：

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_demo_data
```

脚本会提示输入教务初始密码，也支持 `INITIAL_REGISTRAR_PASSWORD` 环境变量。它可以重复执行，只创建缺失的演示记录；新建师生的登录编号由系统生成，初始密码均为 `Initial123`，首次登录必须修改。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试不仅检查响应，还检查选课人数、原结果、方案版本、关闭状态和账单快照等数据库结果。

2026-10-08 集成回归为 **168 passed, 2 skipped**；两项 MySQL 并发用例未配置测试库而跳过。页面模拟接口的遗留问题不在现有测试的完整覆盖范围内，详见集成检查报告。

若有专用 MySQL 测试库（库名必须以 `_test` 结尾），可执行真实双事务名额竞争测试：

```powershell
$env:MYSQL_TEST_DATABASE_URL='mysql+pymysql://user:password@127.0.0.1/course_registration_test?charset=utf8mb4'
.\.venv\Scripts\python.exe -m pytest -q -m mysql
```

## 项目结构

```text
app/models.py                    总体数据模型
app/services/registration.py    选课与关闭事务
app/main.py                      FastAPI 接口
migrations/                     Alembic 数据库迁移
tests/                           核心业务和接口测试
docs/成员1核心设计说明.md         设计与并发约定
docs/接口约定v0.2.md             给成员 2、3、4 的集成契约
app/services/auth.py             正式登录与会话
app/services/personnel.py        师生管理
app/services/teaching.py         教师授课与成绩
app/services/billing.py          账单派发与重试
web/                            页面模板与请求适配
mock_catalog/                   尚待应用联调的目录模拟服务
mock_billing/                   计费接收模拟服务
scripts/billing_retry_worker.py  独立后台重试进程
```

# 课程注册系统

这是软件工程课程设计的 FastAPI 后端。当前版本完成了成员 1（组长）的核心范围：数据库总体结构、学生选课事务、关闭选课、账单任务落库、接口约定和自动化测试。

## 已完成

- 草稿与正式结果分开保存，保存草稿不占名额。
- 首次提交严格校验 4 个主选和 2 个备选；后续允许 0-4 个主选和 0-2 个备选。
- 校验班次状态、容量、先修课、上课冲突和同课程重复修读。
- 提交、换班式修改、退课和删除方案均在事务中执行；版本号阻止旧页面覆盖新状态。
- 关闭选课采用已确认的两轮处理，取消无教师/人数不足班次，按排队顺序尝试备选。
- 关闭事务内固定最终课表和金额，创建待发送账单；实际发送和重试由成员 4 接入。
- 开发环境身份适配器已提供；正式会话认证由成员 3 替换。

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

打开 `http://127.0.0.1:8000/docs` 查看接口。当前开发身份请求头示例：学生使用 `X-Role: student` 和 `X-Subject-ID: 1`；教务使用 `X-Role: registrar`。该方式只用于成员 3 接入认证之前，非开发环境会主动拒绝。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试不仅检查响应，还检查选课人数、原结果、方案版本、关闭状态和账单快照等数据库结果。

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
docs/接口约定.md                 给成员 2、3、4 的集成契约
```

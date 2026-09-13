# 成员 3 学习与实现说明

这组文件用于理解成员 3 的业务规则：

- `models.py`：学习用账号、会话、师生资料和内存仓库。
- `auth.py`：密码、登录、会话、当前用户、角色、改密、退出和 CSRF 函数。
- `personnel.py`：师生资料、编号、增查改、删除/停用、重置密码和教务初始化函数。
- `test_member3_functions.py`：三个关键规则测试。
- `requirements.txt`：学习代码使用的 Argon2 密码哈希依赖。

这些代码不会被当前 `app.main` 自动导入，也不会改动课程系统数据库。它们是可以运行的规则参考；正式开发时应将同样的规则迁移到 SQLAlchemy 模型、服务和 FastAPI 依赖中。

## 1. 我打开前端时做了什么

当前项目不是一个单独的 Vue/React 前端。成员 2 写的是 Jinja2 模板、CSS 和 JavaScript，由同一个 FastAPI 程序提供页面。

我执行的动作如下：

1. 查看 `requirements.txt`、`app/main.py`、Python 路径和 8000 端口，确认 `app.main:app` 是启动入口。
2. 发现仓库没有 `.venv`，于是用 `python -m venv .venv` 创建隔离环境。
3. 安装 `requirements.txt` 中的 FastAPI、Uvicorn、SQLAlchemy 等依赖。页面渲染还需要 Jinja2，但根依赖文件没有声明，所以本地额外安装了 `jinja2`。
4. 执行以下命令启动应用：

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
5. 请求 `/health`、`/login`、`/static/css/style.css`、`/static/js/app.js` 和 `/admin/users`，它们都返回 HTTP 200，说明页面模板和静态资源可以访问。
6. 在 Codex 浏览器中打开 `http://127.0.0.1:8000/login`。

页面能打开只证明路由和模板运行。当前登录会返回固定的 `mock-jwt-token-secd`，用户列表也是固定数组，不能证明真实认证和资料管理已经完成。

重新启动时，在项目根目录运行第 4 步命令即可。如果缺依赖，先运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt jinja2
```

## 2. 成员 1 已完成的数据结构

成员 1 的模型位于 `app/models.py`。你不需要重建这些表，但必须理解账号和身份怎样与它们连接。

| 模型                               | 作用                                                     | 你需要理解的连接点                                                                                                                     |
| ---------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `Student`                        | 学生主体，目前含`id`、学号、姓名、启用状态。           | 真实学生账号的`subject_id` 指向 `Student.id`。还需由成员 3 补出生日期、社会保障号码、毕业日期和资料状态。                          |
| `Teacher`                        | 教师主体，目前含`id`、教师编号、姓名、院系、启用状态。 | 教师账号的`subject_id` 指向 `Teacher.id`。还需补出生日期、社会保障号码和资料状态。                                                 |
| `Semester`                       | 学期及开放/关闭状态。                                    | 不是成员 3 的业务；认证接入不能改变其状态规则。                                                                                        |
| `Course`、`CoursePrerequisite` | 课程、费用和先修关系。                                   | 不是成员 3 的业务。登录用户通过后，成员 1/4 的业务读取这些表。                                                                         |
| `Offering`、`OfferingSlot`     | 具体班次、教师、容量、状态和上课时段。                   | `Offering.teacher_id` 指向教师。判断教师是否拥有班次属于成员 4；删除教师时你要先确认历史关联，不能依赖 `SET NULL` 抹去历史。       |
| `Schedule`                       | 每个学生每学期的一份方案，含版本、提交和软删除状态。     | `Schedule.student_id` 指向学生。学生当前身份来自 `CurrentUser.subject_id`，不能接受请求体自报学生 ID。它也决定学生能否被物理删除。 |
| `DraftChoice`                    | 编辑中的主选和备选草稿。                                 | 与正式结果分开，保存草稿不占名额。不是你的业务实现。                                                                                   |
| `Enrollment`                     | 正式选中的班次。                                         | `(schedule_id, course_id)` 唯一，防止同课程两个班次同时正式选中。属于成员 1。也是删除学生时要检查的历史链。                          |
| `FormalAlternate`                | 正式提交后保留的备选及优先级。                           | 关闭时由成员 1 使用，不由成员 3处理。                                                                                                  |
| `Grade`                          | 学生在班次中的成绩。                                     | 数据模型已有；录入、修改、归属检查和变更记录属于成员 4。成绩是删除学生/教师时的业务历史证据。                                          |
| `BillingJob`                     | 关闭选课时固定的账单金额和课表快照。                     | 由成员 1 创建，成员 4发送。你只在删除学生时检查关联，不能重新计算或发送账单。                                                          |

最关键的关系是：

```text
登录 Cookie -> ServerSession -> Account -> (role, subject_id)
                                            ├─ student -> Student.id
                                            ├─ teacher -> Teacher.id
                                            └─ registrar -> 教务主体设计
```

`CurrentUser` 只包含 `role` 和 `subject_id`。成员 1 的学生接口调用 `require_student(user)` 得到学生 ID，再访问其方案。你接入真实认证时必须保持这一结构和含义。

成员 1 还建立了两种并发控制：写操作按“学期 → 业务主体 → 班次 ID 升序”加锁；页面用 `Schedule.version` 防止旧数据覆盖新数据。认证接入只提供可信身份，不应改写这些事务规则。

## 3. 你需要掌握的认证流程

```text
提交登录编号和密码
        ↓
authenticate_user：查账号、验哈希、检查启用状态
        ↓
create_session：服务端保存会话，Cookie 只放随机会话 ID
        ↓
current_user：每个请求再次检查会话、账号和人员状态
        ↓
require_student / require_teacher / require_registrar
        ↓
成员 1 或成员 4继续做业务归属检查
```

“认证”和“授权”需要分开理解：

- 认证回答“你是谁”，由会话得到 `CurrentUser`。
- 角色授权回答“这个角色能不能进入接口”，由 `require_*` 完成。
- 业务归属回答“这个方案或班次是不是你的”，由成员 1 或成员 4 的业务函数完成。

只检查角色是不够的。例如一个教师角色通过 `require_teacher` 后，仍不能修改另一个教师班次的成绩。

## 4. 学习代码中的函数

### 4.1 `auth.py`

| 函数                                                            | 你要理解的规则                                                                | 正式接入位置                                      |
| --------------------------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------- |
| `validate_password`                                           | 密码至少 8 位并包含字母和数字；前后端提示必须一致。                           | 请求 Schema 或认证服务。                          |
| `hash_password`                                               | 使用`pwdlib` 推荐的 Argon2 算法，只保存哈希。                               | 创建账号、重置密码、改密。                        |
| `verify_password`                                             | 用密码库验证明文与哈希。                                                      | 登录和改密。                                      |
| `create_account`                                              | 登录编号唯一，角色与人员资料对应，首次必须改密。                              | SQLAlchemy 账号服务。                             |
| `authenticate_user`                                           | 错误账号/密码返回同类错误，停用账号拒绝。                                     | 登录接口。                                        |
| `create_session`                                              | 生成随机会话 ID 和 CSRF 令牌并设置有效期。                                    | 登录成功后的服务端会话表。                        |
| `get_session_account`                                         | 每次请求检查会话、到期、撤销、账号及人员状态。                                | FastAPI 基础会话依赖。                            |
| `current_user`                                                | 输出约定的`CurrentUser(role, subject_id)`；默认阻止未首次改密用户进入业务。 | 替换`app/auth_contract.py` 中的开发身份头逻辑。 |
| `require_student`、`require_teacher`、`require_registrar` | 统一角色检查并返回业务主体 ID。                                               | 各业务路由依赖。                                  |
| `invalidate_sessions`                                         | 改密、重置或停用后撤销该账号全部旧会话。                                      | 认证服务共用函数。                                |
| `change_password`                                             | 验证原密码、保存新哈希、清除首次改密标记并撤销旧会话。                        | 改密接口。                                        |
| `logout`                                                      | 在服务端撤销当前会话。                                                        | 退出接口；前端不能只清 localStorage。             |
| `validate_csrf`                                               | 修改请求比较会话里的 CSRF 令牌。                                              | 所有 Cookie 认证的写接口。                        |

### 4.2 `personnel.py`

| 函数                                                               | 你要理解的规则                                                               |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| `validate_person_profile`                                        | 校验姓名、日期、社会保障号码、院系和毕业日期。                               |
| `generate_login_number`                                          | 系统分配唯一学号/教师编号；正式数据库仍需唯一约束处理并发。                  |
| `create_student`、`create_teacher`                             | 资料和账号必须在同一事务中创建，任何一步失败都回滚。                         |
| `list_students`、`list_teachers`                               | 列表及筛选，不返回密码哈希或会话秘密。                                       |
| `get_student`、`get_teacher`                                   | 读取详情，不存在时给明确错误。                                               |
| `update_student`、`update_teacher`                             | 只修改允许的资料，不能借此切换角色或业务归属。                               |
| `has_student_business_records`、`has_teacher_business_records` | 决定物理删除还是停用；正式代码需查询方案、成绩、账单、班次和成绩变更等关联。 |
| `deactivate_account`                                             | 同步停用账号和人员，并立即撤销旧会话；不退课、不取消授课、不删除历史。       |
| `set_account_status`                                             | 根据目标布尔值幂等设置状态，不能每次请求盲目反转。                           |
| `reset_account_password`                                         | 若保留成员 2 的重置按钮，保存新哈希、要求首次改密并撤销旧会话。              |
| `delete_or_deactivate_student`、`delete_or_deactivate_teacher` | 无业务历史才删除；有历史只停用。                                             |
| `init_registrar`                                                 | 教务账号只通过初始化建立，重复运行不覆盖已有密码。                           |

学习代码用 `MemoryStore.transaction()` 展示失败回滚。正式代码应使用 SQLAlchemy 的数据库事务和唯一约束；内存集合只用于观察业务规则。

## 5. 如何处理成员 2 提供的前端接口

### 5.1 你负责接入的接口

| 前端当前请求                                     | 当前状态                         | 你的处理                                                            |
| ------------------------------------------------ | -------------------------------- | ------------------------------------------------------------------- |
| `POST /api/v1/auth/login`                      | 返回固定假令牌，按用户名猜角色。 | 接入`authenticate_user` 和 `create_session`；角色从数据库取得。 |
| `POST /api/v1/auth/change-password`            | 只检查长度，不保存。             | 接入`change_password`；允许首次改密会话访问。                     |
| `POST /api/v1/auth/logout`                     | 当前不存在。                     | 新增接口，调用`logout`，并让成员 2 的按钮调用它。                 |
| `GET /api/v1/admin/users`                      | 返回固定数组。                   | 教务鉴权后查询真实师生资料。                                        |
| `POST /api/v1/admin/users`                     | 返回成功但不入库。               | 教务鉴权后调用`create_student` 或 `create_teacher`。            |
| `POST /api/v1/admin/users/{id}/reset-password` | 返回固定提示。                   | 若小组保留该扩展，调用`reset_account_password`。                  |
| `PATCH /api/v1/admin/users/{id}/status`        | 不读取目标状态。                 | 读取`{is_active}` 并调用 `set_account_status`。                 |
| 详情、修改、删除                                 | 当前缺失。                       | 与成员 2 对齐后增加`GET/PATCH/DELETE /api/v1/admin/users/{id}`。  |

这些 `/api/v1/...` 是成员 2 当前模拟路径，`docs/接口约定.md` 尚未把认证和资料管理路径定为正式契约。实现前应把最终请求字段、响应、Cookie、CSRF、状态码写回统一接口文档。

### 5.2 不能由前端决定的事情

- 前端现在把令牌和角色放在 localStorage。正式权限不能信任这两个值，应改用 HttpOnly Cookie；前端只负责展示。
- 前端角色叫 `admin`，后端契约叫 `registrar`。`CurrentUser` 必须保持 `registrar`；页面可以显示“教务管理员”。
- 页面用 URL 参数 `first=true` 显示首次改密。后端仍需在每次业务请求检查 `must_change_password`，不能靠跳转保护。
- 创建页面允许手填编号和选择 `admin`。需求规定编号由系统分配，普通管理入口只创建学生/教师，教务账号由初始化脚本创建。
- 所有失败必须使用 `{"detail": "可直接展示给用户的原因"}`，以便成员 2 的公共请求代码直接显示。

### 5.3 对成员 1 正式接口的认证适配

成员 1 已提供以下正式路径，不能用成员 2 的模拟路径替换：

- `GET /semesters/{semester_id}/schedule`
- `PUT /semesters/{semester_id}/schedule/draft`
- `POST /semesters/{semester_id}/schedule/submit`
- `DELETE /semesters/{semester_id}/schedule/offerings/{offering_id}`
- `DELETE /semesters/{semester_id}/schedule`
- `POST /registrar/semesters/{semester_id}/close`

你的任务是让这些接口从真实会话获得 `CurrentUser`。请求中的 `expected_version`、`choices`、备选优先级和 409 版本冲突行为属于成员 1 的契约，不应由认证模块改变。

## 6. 与成员 4 的边界

成员 4 会复用你提供的 `current_user()` 和 `require_teacher()`。交接给成员 4 的内容应只有可信的教师 ID，不应替他完成教师业务。

| 事项          | 成员 3                                                                     | 成员 4                                                                  |
| ------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| 登录和会话    | 验证教师账号，返回`CurrentUser(role="teacher", subject_id=Teacher.id)`。 | 直接复用，不另写登录。                                                  |
| 教师状态      | 每个请求检查账号和教师当前是否启用。                                       | 在可信教师身份之上检查具体业务。                                        |
| 授课资格      | 不判断教师能教哪门课。                                                     | 实现资格查询和认领校验。                                                |
| 班次归属      | 不因角色为教师就允许访问任意班次。                                         | 检查班次是否由当前`teacher_id` 负责。                                 |
| 认领/取消授课 | 不实现。                                                                   | 检查资格、冲突、班次占用、学期开放和并发。                              |
| 花名册        | 不查询。                                                                   | 仅返回当前教师所教班次的正式学生。                                      |
| 成绩          | 不录入、不修改、不判断通过。                                               | 成绩 A-D 为通过，维护成绩及变更记录，拒绝修改其他班次。                 |
| 学生查看成绩  | 只提供可信学生身份。                                                       | 查询当前学生自己的成绩。                                                |
| 计费          | 不计算、不发送、不重试。                                                   | 消费成员 1 创建的`BillingJob`，使用已固定金额和 JSON 快照发送及重试。 |
| 删除教师      | 判断是否存在班次、成绩变更等业务历史；有历史则停用。                       | 提供本模块关联含义，避免成员 3 漏查历史。                               |

成员 4 不能只调用 `require_teacher()` 就认为权限检查完成；还必须验证教师与班次的归属。成员 3 也不能把成员 4 的成绩、认领或计费规则塞进认证服务。

## 7. 运行学习代码

安装学习依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r learn/requirements.txt
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest learn/test_member3_functions.py -q
```

当前结果为 `3 passed`，覆盖：首次改密及旧会话撤销、CSRF 与停用拦截、无历史删除和有历史停用。

推荐阅读顺序是 `models.py` → `auth.py` → `test_member3_functions.py` → `personnel.py`。先跟踪一名学生从创建、登录、首次改密到停用的状态变化，再把内存仓库逐步换成 SQLAlchemy。

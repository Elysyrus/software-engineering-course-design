# 成员 3 待办核对结果

核对日期：2026-09-13
核对依据：`member3/unacheived.md`、当前代码、测试与 `docs/接口约定.md`。
最近验证：`.\\.venv\\Scripts\\python.exe -m pytest -q`，结果为 **53 passed, 1 skipped**。

## 结论

**成员 3 自己负责的“认证、会话、权限隔离、师生账号与基础人员管理”已完成。**

但不能把 `unacheived.md` 的全部条目都标为完成：其中“学生只能操作自己的选课”“教师只能管理自己班次”“替换模拟选课/教师 API”为成员 1、4 真实业务完成后的联调工作。它们不应由成员 3 重写业务服务，但必须在真实业务 API 接入时加上成员 3 提供的认证依赖。

另有一批“出生日期、社会保障号码、毕业日期”等资料字段，已由当前需求决定**不补充**，因此是范围调整，不是遗漏。

状态含义：

- **完成**：代码、接口或测试已落地。
- **不做（已确认）**：曾列为待办，但之后已确认不属于本次范围。
- **待联调**：依赖成员 1、4 的真实业务接口；成员 3 的认证能力已提供。
- **可增强**：当前验收不阻塞，后续可补。

## 1. 账号与会话生命周期

| 原待办                                     | 结果                                          | 证据                                                                                                |
| ------------------------------------------ | --------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 教务创建学生和教师账号；教务通过初始化创建 | **完成**                                | `create_student()`、`create_teacher()`、`init_registrar()`；`scripts/init_registrar.py`。   |
| 唯一学号/工号作为唯一登录编号              | **完成**                                | `generate_login_number()`；`Account.login_number` 数据库唯一约束。                              |
| 初始密码、首次登录强制改密                 | **完成**                                | `Account.must_change_password`、`change_password()`、`current_account()`。                    |
| 使用成熟哈希库保存密码                     | **完成**                                | `auth.py` 使用 Argon2 `PasswordHasher`。                                                        |
| 服务端会话、HttpOnly Cookie、CSRF          | **完成**                                | `ServerSession`、`create_session()`、`_set_auth_cookies()`、`validate_csrf()`。             |
| 首次改密前仅可改密/退出                    | **完成**                                | `get_session_account(... allow_password_change=True)` 与 `current_user_for_password_change()`。 |
| 登出、改密、重置、停用撤销旧会话           | **完成**                                | `logout()`、`invalidate_sessions()`、`reset_account_password()`、人员停用逻辑。               |
| 每次受保护请求检查当前状态                 | **完成（已接入的认证/人员接口与页面）** | `get_session_account()` 每次检查会话、账号和关联人员状态。真实业务 API 的接入见“待联调”。       |

## 2. 身份与权限

| 原待办                                              | 结果             | 证据                                                                                                      |
| --------------------------------------------------- | ---------------- | --------------------------------------------------------------------------------------------------------- |
| 移除`X-Role`、`X-Subject-ID` 的开发身份来源     | **完成**   | `current_account()` 从 `session_id` Cookie 解析真实账号。                                             |
| 保持`CurrentUser(role, subject_id)`               | **完成**   | `app/auth_contract.py` 中结构保持不变。                                                                 |
| 统一角色`student`、`teacher`、`registrar`     | **完成**   | `AccountRole` 与三个 `require_*` 函数。                                                               |
| 学生接口使用可信`subject_id`，不信任请求体学生 ID | **待联调** | 成员 3 已提供`CurrentUser.subject_id`；成员 1 的真实选课服务需要在路由中使用它。                        |
| 学生仅操作自己方案/成绩，教师仅管理自己授课/成绩    | **待联调** | 方案归属是成员 1 责任，班次与成绩归属是成员 4 责任；当前`web/web_routes.py` 对应业务 API 仍有模拟数据。 |
| 教务维护师生资料                                    | **完成**   | `/api/v1/admin/users` 的创建、查询、编辑、启停、重置和删除接口均要求 `registrar`。                    |

## 3. 师生资料管理

| 原待办                               | 结果                     | 证据                                                                     |
| ------------------------------------ | ------------------------ | ------------------------------------------------------------------------ |
| 学生姓名、状态、系统学号             | **完成**           | `Student` 与学生管理服务。                                             |
| 教师姓名、状态、院系、系统工号       | **完成**           | `Teacher` 与教师管理服务。                                             |
| 出生日期、社会保障号码、毕业日期     | **不做（已确认）** | 当前需求已明确无需补充这些字段，因此未改变模型或迁移。                   |
| 虚构社会保障号码与相关格式规则       | **不做（已确认）** | 前置字段不纳入本次范围。                                                 |
| 教务新增、查询、修改资料             | **完成**           | `create_*`、`list_*`、`get_*`、`update_*`；对应教务 API 与页面。 |
| 无历史物理删除；有历史停用保留       | **完成**           | `has_*_business_records()`、`delete_or_deactivate_*()`。             |
| 停用不改变选课、授课、成绩、账单历史 | **完成**           | 停用只变更`active` 并撤销会话，不删除业务记录。                        |

## 4. 模拟函数和页面入口

| `unacheived.md` 中的原模拟项           | 结果                                 | 证据                                                                                     |
| ---------------------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------------- |
| `api_login()`                          | **完成**                       | 查询账号、验证哈希、创建 Session、设置 Cookie，不返回 Token。                            |
| `api_change_password()`                | **完成**                       | 验证旧密码、CSRF、新密码规则，改密后撤销会话。                                           |
| `api_get_users()`                      | **完成**                       | 查询真实学生、教师账号资料；不返回密码哈希或会话秘密。                                   |
| `api_create_user()`                    | **完成**                       | 教务权限、CSRF、自动编号、人员与账号入库。                                               |
| `api_reset_password()`                 | **完成**                       | 重置为`Initial123`、要求下次改密、撤销旧会话。                                         |
| `api_toggle_status()`                  | **完成（以更明确的名称实现）** | `PATCH /api/v1/admin/users/{account_id}/status` 接受 `is_active`。                   |
| 改密、学生、教师、教务页面入口接真实身份 | **完成**                       | 页面均使用`current_user()` / `current_user_for_password_change()` 后再执行角色检查。 |
| 基础页面退出操作调用服务端退出           | **完成**                       | 前端调用`POST /api/v1/auth/logout`，服务端撤销会话并清除 Cookie。                      |

## 5. `unacheived.md` 建议函数核对

### 5.1 认证与会话函数

以下函数均已实现：

```text
validate_password              hash_password
verify_password                create_account
authenticate_user              create_session
get_session_account            change_password
invalidate_sessions            logout
validate_csrf                  require_teacher
current_user（在 auth_contract.py）
```

额外实现了 `current_user_for_password_change()`，它是首次登录用户进入改密页的例外入口。

### 5.2 师生管理函数

以下函数均已实现：

```text
generate_login_number          create_student / create_teacher
list_students / list_teachers  get_student / get_teacher
update_student / update_teacher
has_student_business_records   has_teacher_business_records
delete_or_deactivate_student   delete_or_deactivate_teacher
set_account_status             reset_account_password
init_registrar
```

对教务页面还补充了 `list_users_for_registrar()`、`get_user_for_registrar()`、`update_user_for_registrar()`、`delete_user_for_registrar()`。

未实现 `validate_person_profile()`、分页参数以及日期/社保号验证，原因是这些字段已确认不在本次资料范围。

## 6. 数据库、迁移与初始化

| 原待办                                  | 结果                                                                                               |
| --------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 新增`Account`、`ServerSession` 模型 | **完成**。                                                                                   |
| 新增账号、会话表 Alembic 迁移           | **完成**：`migrations/versions/51ba2d09cfed_add_account_and_server_session_tables.py` 等。 |
| 密码库、Cookie、会话安全配置            | **完成**：Argon2、会话有效期、开发/生产 Cookie `Secure` 处理。                             |
| 初始教务账号脚本                        | **完成**：`python -m scripts.init_registrar --login-number registrar`。                    |
| 虚构师生演示账号脚本                    | **完成**：`python -m scripts.seed_demo_data`，还会创建演示课程，且可重复执行。             |
| 补充生日/社保号/毕业日期迁移            | **不做（已确认）**。                                                                         |

## 7. 测试验收结果

| 验收项                                      | 结果                                                                         |
| ------------------------------------------- | ---------------------------------------------------------------------------- |
| 正确/错误密码、账号不存在、停用账号         | **完成并测试**。                                                       |
| 密码仅保存哈希，接口不泄露哈希或会话秘密    | **完成并测试**。                                                       |
| 首次改密限制                                | **完成并测试**。                                                       |
| 登出、改密、重置、停用后旧会话失效          | **完成并测试**。                                                       |
| CSRF 缺失、错误、正确的行为                 | **完成并测试**。                                                       |
| 人员创建、编辑、启停、删除/历史保护         | **完成并测试**。                                                       |
| 初始化脚本、演示数据幂等性                  | **完成并测试**。                                                       |
| 并发重复编号的 MySQL 真实事务测试           | **可增强/环境未配置**：测试会在无 `MYSQL_TEST_DATABASE_URL` 时跳过。 |
| 真实选课与教师业务 API 的身份和数据权限回归 | **待联调**。                                                           |

## 8. 剩余工作清单

### 不阻塞当前成员 3 交付的增强项

1. 定期清理已过期、已撤销的会话记录。
2. 并发编号冲突时将数据库 `IntegrityError` 转为统一 `409`。
3. 覆盖损坏密码哈希的异常处理。
4. 清理 `TestClient` Cookie 弃用警告与代码格式。

## 9. 交付文件

- `app/services/auth.py`
- `app/auth_contract.py`
- `app/services/personnel.py`
- `web/web_routes.py`
- `web/templates/admin/users.html`
- `scripts/init_registrar.py`
- `scripts/seed_demo_data.py`
- `member3/test/`
- `docs/接口约定.md`
- `docs/成员3模块总结.md`

因此，成员 3 的独立开发任务可以视为完成；剩余“待联调”事项应在其他成员的真实业务 API 合并后进行，不应通过继续修改成员 3 的 CRUD 代码来替代。

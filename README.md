# honguo-buddy-backend

红果校园帮帮后端服务，基于 FastAPI + SQLAlchemy Async + Redis，面向微信小程序登录、用户信息管理与后续帖子/聊天等校园场景。

## 一、开发环境与部署 (Environment & Running)

### 1.1 环境依赖

- Python 3.11+
- FastAPI
- SQLAlchemy (Async)
- Redis
- MySQL

### 1.2 包管理工具

推荐使用 uv 管理虚拟环境与依赖：

若无uv, 首先下载uv

```bash
pip install uv
```

创建虚拟环境, 并下载对应库文件
```bash
uv venv
uv pip install -r requirements.txt
```

### 1.3 本地启动

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Swagger 文档地址：

- http://127.0.0.1:8000/docs
- http://127.0.0.1:8000/openapi.json

### 1.4 配置说明 (.env)

以下是推荐最小配置项（必填项优先）：

```env
# 基础
PROJECT_NAME=HONGUO-BUDDY
DEBUG=true

# 数据库
DATABASE_URL=mysql+aiomysql://user:password@127.0.0.1:3306/honguo

# Redis
REDIS_URL=redis://:password@127.0.0.1:6379/0
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password

# 微信登录
WX_APP_ID=your_wx_app_id
WX_APP_SECRET=your_wx_app_secret
WX_CODE_TO_SESSION_URL=https://api.weixin.qq.com/sns/jscode2session

# 鉴权
SECRET_KEY=replace_with_strong_secret
TOKEN_ALGORITHM=HS256
TOKEN_EXPIRE_TIME=1440

# Swagger 调试登录
DEBUG_MASTER_PASSWORD=123456
DEBUG_SKIP_PASSWORD_CHECK=false

# 邮箱
EMAIL_FROM=example@qq.com
SMTP_SERVER=smtp.qq.com
SMTP_PORT=465
SMTP_USER=example@qq.com
SMTP_PASSWORD=qq_mail_auth_code
```

## 二、项目架构 (Project Structure)

项目遵循职责分离，推荐按以下方式理解：

- app/api/: 路由定义与依赖注入，只做接口编排，严禁在此定义 BaseModel。
- app/schemas/: Pydantic 请求/响应模型定义。
- app/services/: 核心业务逻辑（登录、验证码、用户状态流转）。
- app/models/: SQLAlchemy 数据库实体。
- app/core/: 全局配置、安全工具、异常处理、中间件。
- app/db/: 数据库与 Redis 连接管理。
- app/static/: 静态资源目录。

- tests/unit : 单元测试目录
- tests/integration : 集成测试目录
当前目录简表：

```text
app/
    main.py
    api/
        auth.py
        user.py
    core/
        config.py
        exception_handler.py
        security.py
    db/
        base.py
    models/
        user.py
        user_access_log.py
    schemas/
        auth.py
        response.py
        user.py
    services/
        auth_service.py
tests/
    unit/
    integration/
```

## 三、接口统一规范 (API Standards)

### 3.1 统一响应结构

所有接口响应均采用：

```json
{
    "code": 0,
    "message": {}
}
```

结构约束：

```json
{"code": int, "message": object | list | str}
```

### 3.2 核心错误码

```text
SUCCESS: 0
UNKNOWN: 97
HTTP_ERROR: 98
REQ_ERROR: 99
AUTH_FAILED(LOGIN_FAILED): 101
INSUFFICIENT_AUTHORITY: 102
USER_GET_FAILED: 103
UPDATEPROFILE_FAILED: 104
TOKEN_INVALID: 105
DATA_GET_FAILED: 301
```

### 3.3 异常处理规范

- AuthHTTPException: 认证与鉴权失败，例如登录失败、Token 无效、账号不可用。
- BusinessHTTPException: 业务规则与参数校验失败，例如验证码错误、邮箱已占用。
- ResourceHTTPException: 资源级错误，例如用户不存在、外部资源操作失败。

全局异常处理器会把异常统一转换为：

```json
{
    "code": 105,
    "message": {
        "error": "认证时出现异常",
        "msg": "Token无效或已失效"
    }
}
```

## 四、接口列表 (API List)

说明：以下接口均遵循统一响应结构。需要登录态的接口请在请求头携带 Bearer Token。

### 4.1 AUTH 认证模块

#### 4.1.1 微信登录/注册 (POST: /auth/wxLogin)

用途: 微信小程序登录。若 openid 首次出现则自动创建用户。

请求头: 无。

请求示例:

```json
{
    "code": "wx_login_code_from_miniprogram"
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "token": "<jwt_token>",
        "tokenType": "bearer",
        "expiresIn": 86400,
        "isNewUser": true,
        "userId": 10001,
        "username": "用户483920"
    }
}
```

常见错误:

- code: 99 - 微信登录 code 为空或请求体不合法。
- code: 101 - 微信接口返回错误或账号不可用。

#### 4.1.2 Swagger 调试登录 (POST: /auth/swagger-login)

用途: 仅开发/测试场景使用，通过 wx_id + Debug 密码获取访问令牌。

请求头: 无。

请求示例:

```json
{
    "wx_id": "wx_80e60b2f40a711f1af7d525400cb2282",
    "password": "123456"
}
```

成功响应:

```json
{
    "access_token": "<jwt_token>",
    "token_type": "bearer"
}
```

常见错误:

- code: 99 - wx_id 为空。
- code: 101 - 微信标识不存在或密码错误。

#### 4.1.3 发送邮箱验证码 (POST: /auth/email/send-verify-code)

用途: 已登录用户发送邮箱验证码，用于绑定邮箱。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "email": "student@example.com"
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "detail": "验证码已发送到你的邮箱，请在5分钟内验证",
        "email_masked": "s***@example.com"
    }
}
```

常见错误:

- code: 105 - Token 失效。
- code: 104 - 邮箱格式错误、发送过于频繁、邮箱被占用。

#### 4.1.4 校验邮箱验证码 (POST: /auth/email/verify-code)

用途: 已登录用户提交验证码并完成邮箱绑定。

说明：
- 若绑定的是校内邮箱，系统会在验证码校验通过后自动将当前用户标记为 `is_verified=true`。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "email": "student@example.com",
    "code": "123456"
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "detail": "邮箱绑定成功",
        "email": "student@example.com"
    }
}
```

常见错误:

- code: 105 - Token 失效。
- code: 104 - 验证码错误、过期或次数超限。
- code: 103 - 当前用户不存在。

#### 4.1.5 退出登录 (POST: /auth/logout)

用途: 删除当前用户 token 映射并退出登录。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{}
```

成功响应:

```json
{
    "code": 0,
    "message": "登出成功"
}
```

常见错误:

- code: 105 - Token 失效。
- code: 301 - Redis 操作失败导致登出失败。

### 4.2 USER 用户模块

#### 4.2.1 获取当前用户信息 (GET: /users/info)

用途: 获取当前登录用户基础资料，用于首页和个人中心初始化。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "userUuid": "6f7d2f9c-4f5f-4de5-a2b2-6f8d6e4ce100",
        "userName": "用户483920",
        "isAdmin": false,
        "isVerified": false,
        "userType": "user"
    }
}
```

常见错误:

- code: 105 - Token 无效或已过期。
- code: 103 - 用户不存在。

#### 4.2.2 获取本人资料 (GET: /users/me)

用途: 获取当前登录用户完整资料。返回中的 `avatar` 为可直接访问的静态资源 URL，来源于 `attachment` 表中 `avatar_id` 关联的记录。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "user_id": 1001,
        "user_uuid": "6f7d2f9c-4f5f-4de5-a2b2-6f8d6e4ce100",
        "user_name": "测试用户",
        "avatar": "/static/avatar/avatar_1001_1680000000.png",
        "avatar_id": 123,
        "sex": "男",
        "email": "test@example.com",
        "phonenumber": "13800000000",
        "user_type": "user",
        "credit_score": 100,
        "is_verified": false,
        "is_active": true,
        "is_admin": false,
        "last_login_ip": "127.0.0.1",
        "last_login_time": 1700000000,
        "wechat_unionid": null
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 103 - 用户不存在。

说明：
- `avatar` 字段不再直接由客户端提交 URL 填写，而是由 `user.avatar_id`（外键指向 `attachment.attachment_id`）提供，接口返回的 `avatar` 为 `/static/...` 相对路径，可直接在前端加载。

#### 4.2.3 修改本人资料 (PATCH: /users/me)

用途: 修改当前登录用户资料。`avatar_id` 用于指定头像附件记录，不再直接提交头像 URL。若已上传头像附件，接口返回中的 `avatar` 仍为可直接访问的静态资源 URL。

说明：
- 请求体字段均为可选，用户可只提交部分字段进行局部更新。
- `user_name`、`avatar_id`、`sex` 都是可选字段。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "user_name": "新昵称",
    "avatar_id": 123,
    "sex": "女"
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "user_id": 1001,
        "user_uuid": "6f7d2f9c-4f5f-4de5-a2b2-6f8d6e4ce100",
        "user_name": "新昵称",
        "avatar": "/static/avatar/avatar_1001_1680000000.png",
        "avatar_id": 123,
        "sex": "女",
        "email": "test@example.com",
        "phonenumber": "13800000000",
        "user_type": "user",
        "credit_score": 100,
        "is_verified": false,
        "is_active": true,
        "is_admin": false,
        "last_login_ip": "127.0.0.1",
        "last_login_time": 1700000000,
        "wechat_unionid": null
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 102 - 无权限修改他人资料。
- code: 99 - 请求体校验失败，或 `avatar_id` 不合法。

说明：
- 请求体中 `avatar_id` 必须为 `attachment` 表中已存在且 `target_type=USER` 的附件 ID，且该附件的 `creator_id` 必须等于当前用户（即只能使用自己上传的图片作为头像）。
- 管理员（`is_admin=true`）通过管理员接口 (`PUT /users/{user_id}`) 可为任意用户设置 `avatar_id`。
- 上传附件的接口会在 `target_type=USER` 并且 `target_id` 指定为某用户时，自动把该附件回填为该用户的 `avatar_id`。

#### 4.2.4 获取他人公开资料 (GET: /users/{user_id})

用途: 获取他人公开资料，脱敏后返回。

请求头: 无。

请求示例:

```json
{
    "user_id": 1002
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "user_id": 1002,
        "user_uuid": "6f7d2f9c-4f5f-4de5-a2b2-6f8d6e4ce100",
        "user_name": "公开用户",
        "avatar": "/static/avatar/avatar_1002_1680000000.png",
        "sex": "女",
        "credit_score": 50,
        "is_verified": true,
        "user_type": "user"
    }
}
```

常见错误:

- code: 103 - 用户不存在。
- code: 301 - 查询公开资料失败。

#### 4.2.5 注销本人账号 (DELETE: /users/me)

用途: 注销当前登录用户账号（逻辑删除）。注销后无法登录，数据保留在数据库。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "message": "账号已注销"
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 103 - 用户不存在。

#### 4.2.6 关注/取消关注用户 (POST: /users/follow)

用途: 对指定用户执行关注/取消关注双态翻转。未关注则创建关注，已关注则取消关注。禁止自己关注自己。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "following_id": 1002
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "following_id": 1002,
        "is_following": true
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 99 - 尝试自己关注自己，或关注的目标用户不存在。

#### 4.2.7 获取我的关注列表 (GET: /users/me/followings)

用途: 分页拉取当前登录用户的关注人列表，返回关注对象的公开资料及互关标志。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "page": 1,
    "page_size": 20
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "total": 1,
        "page": 1,
        "page_size": 20,
        "list": [
            {
                "user": {
                    "user_id": 1002,
                    "user_uuid": "6f7d2f9c-4f5f-4de5-a2b2-6f8d6e4ce100",
                    "user_name": "关注用户",
                    "avatar": "/static/avatar/avatar_1002.png",
                    "sex": "男",
                    "credit_score": 80,
                    "is_verified": false,
                    "user_type": "user"
                },
                "is_mutual": true
            }
        ]
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。

#### 4.2.8 获取我的粉丝列表 (GET: /users/me/followers)

用途: 分页拉取当前登录用户的粉丝列表，返回粉丝的公开资料及互关标志。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "page": 1,
    "page_size": 20
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "total": 1,
        "page": 1,
        "page_size": 20,
        "list": [
            {
                "user": {
                    "user_id": 1003,
                    "user_uuid": "...",
                    "user_name": "粉丝用户",
                    "avatar": "/static/avatar/avatar_1003.png",
                    "sex": "女",
                    "credit_score": 90,
                    "is_verified": true,
                    "user_type": "user"
                },
                "is_mutual": false
            }
        ]
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。

#### 4.2.9 收藏/取消收藏 (POST: /users/favorite)

用途: 对指定帖子或商品执行收藏/取消收藏双态翻转。`target_type` 支持 `POST` 与 `GOODS`。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "target_type": "POST",
    "target_id": 1001
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "target_type": "POST",
        "target_id": 1001,
        "is_favorite": true
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 99 - `target_type` 非法或目标实体不存在。

#### 4.2.10 获取我的收藏列表 (GET: /users/me/favorites)

用途: 分页拉取当前登录用户的收藏列表。采用批量灌水机制补齐帖子/商品详情，联动返回原资产的生存状态（`is_effective`）与满员状态（`is_full`），并附带发布者简影。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "page": 1,
    "page_size": 20
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "total": 1,
        "page": 1,
        "page_size": 20,
        "list": [
            {
                "target_type": "POST",
                "target_id": 1001,
                "title": "帮忙代拿快递",
                "description": "从东门快递站拿到宿舍楼",
                "price": 5.0,
                "target_status": "OPEN",
                "is_effective": true,
                "is_full": false,
                "create_time": 1680000000123,
                "publisher": {
                    "user_name": "发帖用户",
                    "avatar": "/static/avatar/avatar_1002.png"
                }
            }
        ]
    }
}
```

说明：
- `is_effective` 为 `false` 表示原帖子已删除或状态为 CLOSED，前端可据此置灰。
- `is_full` 为 `true` 表示当前接单人数已达上限。
- `create_time` 为 13 位毫秒级时间戳。
- `publisher` 为发布者脱敏简影，仅包含 `user_name` 与 `avatar`。

常见错误:

- code: 105 - Token 失效或缺失。

#### 4.2.11 获取我的历史浏览足迹 (GET: /users/me/histories)

用途: 分页拉取当前登录用户的历史浏览足迹（基于 Redis ZSET 纯内存存储）。返回最近浏览的帖子/商品列表，采用批量灌水机制补齐详情、满员状态与发布者简影。最多保留最近 100 条记录，自动滚动淘汰。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "page": 1,
    "page_size": 20
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "total": 1,
        "page": 1,
        "page_size": 20,
        "list": [
            {
                "target_type": "POST",
                "target_id": 1001,
                "title": "帮忙代拿快递",
                "description": "从东门快递站拿到宿舍楼",
                "price": 5.0,
                "target_status": "OPEN",
                "is_effective": true,
                "is_full": false,
                "view_time": 1680000000456,
                "publisher": {
                    "user_name": "发帖用户",
                    "avatar": "/static/avatar/avatar_1002.png"
                }
            }
        ]
    }
}
```

说明：
- 历史足迹通过 Redis ZSET 以 `user:history:{user_id}` 为 Key 存储，不写入 MySQL。
- 用户浏览详情页时自动异步刷入足迹，Redis 自动去重并置顶最新浏览。
- 系统自动裁剪保留最新 100 条，有效期 30 天滚动过期。
- `view_time` 为 13 位毫秒级时间戳。
- `is_effective` 与 `is_full` 含义同收藏列表。

常见错误:

- code: 105 - Token 失效或缺失。

#### 4.2.12 [管理员] 修改用户信息 (PUT: /users/{user_id})

用途: 管理员修改指定用户信息（用户名、头像、性别）。请求体字段均为可选，可只提交部分字段进行局部更新。

请求头: Authorization: Bearer <token>（需管理员权限）。

请求示例:

```json
{
    "user_name": "新昵称",
    "avatar_id": 123,
    "sex": "男"
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "user_id": 1002,
        "user_uuid": "...",
        "user_name": "新昵称",
        "avatar": "/static/avatar/avatar_1002.png",
        "sex": "男",
        "email": "...",
        "phonenumber": "...",
        "user_type": "user",
        "credit_score": 100,
        "is_verified": false,
        "is_active": true,
        "is_admin": false,
        "last_login_ip": "127.0.0.1",
        "last_login_time": 1700000000,
        "wechat_unionid": null
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 102 - 权限不足，仅管理员可操作。

#### 4.2.13 [管理员] 禁用/删除用户 (DELETE: /users/{user_id})

用途: 管理员禁用或删除指定用户（逻辑删除）。管理员无法删除自己的账号。

请求头: Authorization: Bearer <token>（需管理员权限）。

请求示例:

```json
{}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "message": "用户 1002 已被禁用/删除"
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 102 - 权限不足或尝试删除自己的账号。

#### 4.2.14 多维清理历史足迹 (POST: /users/me/histories/delete)

用途: 按照指定模式清理用户的历史浏览足迹。支持三种清理模式：SINGLE（单条删除）、RANGE（按时间段删除）、CLEAR_ALL（全量清空）。禁止使用 DELETE 带 Body，故采用 POST 承载清理载荷。

请求头: Authorization: Bearer <token>。

请求示例（SINGLE 模式）:

```json
{
    "action_type": "SINGLE",
    "target_type": "POST",
    "target_id": 1001
}
```

请求示例（RANGE 模式）:

```json
{
    "action_type": "RANGE",
    "start_time": 1700000000000,
    "end_time": 1700000100000
}
```

请求示例（CLEAR_ALL 模式）:

```json
{
    "action_type": "CLEAR_ALL"
}
```

说明：
- `action_type` 为必填，可选值 `SINGLE` / `RANGE` / `CLEAR_ALL`。
- SINGLE 模式下 `target_type` 与 `target_id` 均为必填。
- RANGE 模式下 `start_time` 与 `end_time` 均为必填（13位毫秒级时间戳），且 `start_time` 不大于 `end_time`。
- CLEAR_ALL 模式下无需额外参数，一键清空当前用户全部足迹。

成功响应:

```json
{
    "code": 0,
    "message": {
        "action_type": "SINGLE",
        "message": "清理意图已成功接收并在后台异步蒸发",
        "deleted_count": 1
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 99 - 请求参数校验失败（如 SINGLE 缺少 target_type，或 RANGE 起止时间不合法）。

#### 4.2.15 用户主页声誉画像 (GET: /users/{user_id}/profile)

用途: 获取指定用户的双角色星级评分与印象标签。优先读取 Redis 缓存，击穿时回数据库重算。

请求头: 无（公开接口）。

请求示例:

```json
{
    "user_id": 1001
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "user_id": 1001,
        "carrier_score": 4.5,
        "carrier_order_count": 10,
        "client_score": 4.8,
        "client_order_count": 5,
        "tags_json": "{}"
    }
}
```

说明：
- `carrier_score` / `carrier_order_count`：「接单人」角色维度的平均评分与订单数。
- `client_score` / `client_order_count`：「发单人」角色维度的平均评分与订单数。
- `tags_json`：高频印象标签（JSON 字符串），如 `{"好评": 12, "快速响应": 8}`。

常见错误:

- code: 103 - 用户不存在或已被删除。

#### 4.2.16 用户评价详情列表 (GET: /users/{user_id}/reviews)

用途: 延迟加载指定用户的评价列表，支持按角色分页（CARRIER 接单人 / CLIENT 发单人）。执行严格双向脱敏：评价发表人头像置 None，姓名打码。仅展示已通过双盲释放机制（`is_visible=True`）的评价。

请求头: 无（公开接口）。

请求示例:

```json
{
    "role": "CARRIER",
    "offset": 0,
    "limit": 20
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "total": 1,
        "offset": 0,
        "limit": 20,
        "role": "CARRIER",
        "list": [
            {
                "review_id": 8001,
                "order_id": 7001,
                "rating": 5,
                "content": "非常好",
                "is_anonymous": false,
                "reviewer": {
                    "user_id": 1002,
                    "user_name": "张**",
                    "avatar": null
                },
                "create_time": 1700000000000
            }
        ]
    }
}
```

说明：
- `role`：`CARRIER` 查看用户作为接单人收到的评价，`CLIENT` 查看用户作为发单人收到的评价。
- 评价发表人强制执行脱敏：`avatar` 始终为 `null`，`user_name` 打码（如「张学长」→「张**」）。
- 仅展示 `is_visible=True` 的评价（双盲机制释放后）。
- `create_time` 为 13 位毫秒级时间戳。

常见错误:

- code: 103 - 用户不存在。

### 4.3 附件上传模块

#### 4.3.1 上传附件 (POST: /attachments/upload)

用途: 上传图片附件并返回附件 ID 与可访问 URL。若 `target_type=USER`，系统会自动回填该附件到对应用户的 `avatar_id`。

请求头: Authorization: Bearer <token>。

请求说明:
- 使用 `multipart/form-data` 上传。
- `file`：文件字段。
- `target_type`：可选，上传附件类型。
- `target_id`：可选，关联目标 ID。

请求示例（multipart/form-data）:

```json
{
    "file": "image.png",
    "target_type": "USER",
    "target_id": 1001
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "id": 123,
        "url": "/static/avatar/avatar_123.png"
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 99 - 上传文件格式不合法或请求体缺失。
- code: 301 - 文件保存失败或数据库写入失败。

### 4.4 Category 模板分类模块

说明：

- 创建/更新/删除 接口为管理员权限（需要 `is_admin=true`）。
- 获取列表与详情对外开放。

#### 4.4.1 创建模板分类 (POST: /categories/)

用途：创建一个模板分类（管理员）。

请求示例：

```json
{
    "name": "二手电子",
    "item_type": "GOODS",
    "icon": "/static/category/electronics.png",
    "config_json": {
        "fields": [
            {"key": "brand", "label": "品牌", "type": "string", "required": true},
            {"key": "condition", "label": "成色", "type": "select", "required": true}
        ]
    }
}
```

约束说明：

- `icon` 可选，可不传。
- `item_type` 必填或有默认（`POST`），可选值：`POST` 或 `GOODS`。
- `config_json` 必填，且不能为空对象。

成功响应:

```json
{
    "code": 0,
    "message": {
        "category_id": 1,
        "name": "二手电子",
        "icon": "/static/category/electronics.png",
        "item_type": "GOODS",
        "config_json": {"fields": [{"key": "brand", "label": "品牌", "type": "string", "required": true}, {"key": "condition", "label": "成色", "type": "select", "required": true}]},
        "create_time": "2025-09-01T12:00:00",
        "update_time": "2025-09-01T12:00:00"
    }
}
```

常见错误:

- code: 102 - 权限不足。
- code: 99 - 请求体校验失败。

#### 4.4.2 获取模板分类列表 (GET: /categories)


查询参数：

- `type`（可选）：按业务类型过滤，取值 `POST` 或 `GOODS`，示例：`GET /categories?type=POST`。

用途：获取模板分类列表，供前端展示分类选择。

请求示例:

```json
{
    "type": "POST"
}
```

成功响应:

```json
{
    "code": 0,
    "message": [
        {
            "category_id": 1,
            "name": "代跑服务",
            "icon": "/static/category/run.png",
            "item_type": "POST",
            "config_json": {"fields": []},
            "create_time": "2025-09-01T12:00:00",
            "update_time": "2025-09-01T12:00:00"
        }
    ]
}
```

#### 4.4.3 获取模板分类详情 (GET: /categories/{category_id})

用途：按 ID 获取模板分类详情。

请求示例:

```json
{
    "category_id": 1
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "category_id": 1,
        "name": "代跑服务",
        "icon": "/static/category/run.png",
        "item_type": "POST",
        "config_json": {"fields": []},
        "create_time": "2025-09-01T12:00:00",
        "update_time": "2025-09-01T12:00:00"
    }
}
```

#### 4.4.4 更新模板分类 (PUT: /categories/{category_id})

用途：更新模板分类信息（管理员）。

请求头: Authorization: Bearer <token>。

请求示例：

```json
{
    "name": "二手数码",
    "item_type": "GOODS",
    "icon": null,
    "config_json": {
        "fields": [
            {"key": "brand", "label": "品牌", "type": "string", "required": true},
            {"key": "storage", "label": "容量", "type": "string", "required": false}
        ]
    }
}
```


- `config_json` 更新时同样必填，且不能为空对象。
- `icon` 可选，传 `null` 可清空图标。

成功响应:

```json
{
    "code": 0,
    "message": {
        "category_id": 1,
        "name": "二手数码",
        "icon": null,
        "item_type": "GOODS",
        "config_json": {"fields": [{"key": "brand", "label": "品牌", "type": "string", "required": true}, {"key": "storage", "label": "容量", "type": "string", "required": false}]},
        "create_time": "2025-09-01T12:00:00",
        "update_time": "2025-09-02T12:00:00"
    }
}
```

#### 4.4.5 删除模板分类 (DELETE: /categories/{category_id})

用途：软删除模板分类（管理员）。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "category_id": 1
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "category_id": 1,
        "deleted": true
    }
}
```

### 4.5 Post 帖子模块

#### 4.5.1 发布帖子 (POST: /posts/)

用途: 发布悬赏帖。

说明：
- `title` 为必填字段。
- `description`、`price`、`category_id`、`template_filters`、`attachment_ids` 均为可选字段。
- `direction` 默认为 `SELL`，`urgency` 默认为 `NORMAL`，`max_accepters` 默认为 `1`。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "title": "代取外卖",
    "description": "帮我从食堂取一份外卖",
    "price": 10.5,
    "direction": "SELL",
    "urgency": "NORMAL",
    "max_accepters": 1,
    "category_id": 3,
    "template_filters": {"pickup_address": "教学楼A楼"},
    "attachment_ids": [123]
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "post_id": 1001,
        "category_id": 3,
        "title": "代取外卖",
        "description": "帮我从食堂取一份外卖",
        "price": 10.5,
        "direction": "SELL",
        "urgency": "NORMAL",
        "status": "OPEN",
        "template_data": {
            "pickup_address": "教学楼A楼",
            "max_accepters": 1
        },
        "max_accepters": 1,
        "publisher": {
            "user_id": 1001,
            "user_uuid": "...",
            "user_name": "测试用户",
            "avatar": "/static/avatar/avatar_1001.png",
            "sex": "未知",
            "credit_score": 100,
            "is_verified": false,
            "user_type": "user"
        },
        "publisher_id": 1001,
        "current_accepters": 0,
        "create_time": "2025-09-01T12:00:00",
        "attachment_urls": []
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 99 - 请求体校验失败。
- code: 301 - 发布帖子失败。

#### 4.5.2 获取帖子列表 (GET: /posts)

用途: 获取帖子列表，支持关键词、模板/分类ID、状态、价格、时间等筛选。

请求示例:

```json
{
    "keyword": "外卖",
    "category_id": 9201,
    "status": "OPEN",
    "page": 1,
    "page_size": 20
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "total": 42,
        "page": 1,
        "page_size": 20,
        "list": [
            {
                "post_id": 1001,
                "category_id": 9201,
                "title": "二手书转让",
                "description": "九成新教辅",
                "price": 15.0,
                "direction": "SELL",
                "urgency": "NORMAL",
                "status": "OPEN",
                "template_data": {},
                "max_accepters": 1,
                "publisher": {
                    "user_id": 2001,
                    "user_uuid": "...",
                    "user_name": "用户2001",
                    "avatar": "/static/avatar/avatar_2001.png",
                    "sex": "未知",
                    "credit_score": 50,
                    "is_verified": false,
                    "user_type": "user"
                },
                "publisher_id": 2001,
                "current_accepters": 0,
                "create_time": "2025-09-01T12:00:00",
                "attachment_urls": []
            }
        ]
    }
}
```

#### 4.5.3 我的发布 (GET: /posts/me)

用途: 获取当前用户的帖子列表。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "category_id": 3,
    "status": "OPEN",
    "page": 1,
    "size": 20
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "total": 1,
        "page": 1,
        "page_size": 20,
        "list": [
            {
                "post_id": 1001,
                "category_id": 3,
                "title": "二手书转让",
                "description": "九成新教辅",
                "price": 15.0,
                "direction": "SELL",
                "urgency": "NORMAL",
                "status": "OPEN",
                "template_data": {},
                "max_accepters": 1,
                "publisher": {
                    "user_id": 1001,
                    "user_uuid": "...",
                    "user_name": "测试用户",
                    "avatar": "/static/avatar/avatar_1001.png",
                    "sex": "未知",
                    "credit_score": 100,
                    "is_verified": false,
                    "user_type": "user"
                },
                "publisher_id": 1001,
                "current_accepters": 0,
                "create_time": "2025-09-01T12:00:00",
                "attachment_urls": []
            }
        ]
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 99 - 查询参数格式不合法。
- code: 301 - 列表查询失败。

#### 4.5.4 他人主页帖子 (GET: /posts/user/{user_id})

用途: 公共查看指定用户的公开帖子。

请求示例:

```json
{
    "user_id": 1002,
    "page": 1,
    "size": 20
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "total": 5,
        "page": 1,
        "page_size": 20,
        "list": [
            {
                "post_id": 1002,
                "category_id": 3,
                "title": "帮跑外卖",
                "description": "今天帮取外卖",
                "price": 8.0,
                "direction": "SELL",
                "urgency": "NORMAL",
                "status": "OPEN",
                "template_data": {},
                "max_accepters": 1,
                "publisher": {
                    "user_id": 1002,
                    "user_uuid": "...",
                    "user_name": "公开用户",
                    "avatar": "/static/avatar/avatar_1002.png",
                    "sex": "未知",
                    "credit_score": 80,
                    "is_verified": true,
                    "user_type": "user"
                },
                "publisher_id": 1002,
                "current_accepters": 0,
                "create_time": "2025-09-01T12:00:00",
                "attachment_urls": []
            }
        ]
    }
}
```

常见错误:

- code: 99 - 请求体或路径参数不合法。
- code: 103 - 指定用户不存在。
- code: 301 - 帖子列表查询失败。

#### 4.5.5 帖子详情 (GET: /posts/{post_id})

用途: 获取帖子详情，包含前 N 条评论，并返回模板/分类 ID。

请求示例:

```json
{
    "post_id": 1001,
    "comments_limit": 5
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "post_id": 1001,
        "category_id": 9201,
        "title": "二手书转让",
        "description": "九成新教辅",
        "price": 15.0,
        "direction": "SELL",
        "urgency": "NORMAL",
        "status": "OPEN",
        "template_data": {},
        "max_accepters": 1,
        "publisher": {
            "user_id": 1001,
            "user_uuid": "...",
            "user_name": "测试用户",
            "avatar": "/static/avatar/avatar_1001.png",
            "sex": "未知",
            "credit_score": 100,
            "is_verified": false,
            "user_type": "user"
        },
        "publisher_id": 1001,
        "current_accepters": 0,
        "create_time": "2025-09-01T12:00:00",
        "attachment_urls": ["/static/avatar/avatar_1001_1680000000.png"],
        "comments": [
            {
                "id": 2001,
                "username": "评论者",
                "avatar": "/static/avatar/avatar_2002.png",
                "content": "我想要这本书",
                "time": "2025-09-02T08:00:00"
            }
        ]
    }
}
```

说明：
- 已登录用户访问帖子详情时会自动将浏览记录异步写入 Redis 历史足迹（`user:history:{user_id}`），供 4.2.11 历史浏览足迹接口使用。未登录用户不会记录。
- 返回的 `comments` 仅包含最近若干条热评，按创建时间倒序排列，由 `comments_limit` 控制条数（默认 5 条）。

常见错误:

- code: 99 - 帖子 ID 或路径参数不合法。
- code: 103 - 帖子不存在或已被软删除。

#### 4.5.6 局部更新 (PATCH: /posts/{post_id})

用途: 帖子拥有者或管理员可对帖子进行局部更新，状态为 `OPEN` 时允许修改。若当前帖子存在待处理申请单（PENDING），普通发布者禁止修改，只有管理员可以继续更新。

说明：请求体字段均为可选，可只提交需要修改的字段。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "title": "2025版二手书低价出售",
    "price": 15.0
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "post_id": 1001,
        "category_id": 3,
        "title": "2025版二手书低价出售",
        "description": "九成新教辅",
        "price": 15.0,
        "direction": "SELL",
        "urgency": "NORMAL",
        "status": "OPEN",
        "template_data": {},
        "max_accepters": 1,
        "publisher": {
            "user_id": 1001,
            "user_uuid": "...",
            "user_name": "测试用户",
            "avatar": "/static/avatar/avatar_1001.png",
            "sex": "未知",
            "credit_score": 100,
            "is_verified": false,
            "user_type": "user"
        },
        "publisher_id": 1001,
        "current_accepters": 0,
        "create_time": "2025-09-01T12:00:00",
        "attachment_urls": []
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 102 - 权限不足。
- code: 99 - 请求体校验失败。
- code: 301 - 帖子当前状态不可修改。

#### 4.5.7 软删除 (DELETE: /posts/{post_id})

用途: 帖子拥有者或管理员执行软删除。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "post_id": 1001
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "post_id": 1001,
        "deleted": true
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 102 - 权限不足。
- code: 103 - 帖子不存在或已被删除。
- code: 301 - 删除操作失败。

#### 4.5.8 批量接单 (POST: /posts/batch-accept)

用途: 供顺路接单用户一次性申请多个 `BUY` 方向帖子。支持部分成功、部分失败返回。仅登录用户可操作。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "post_ids": [1, 2, 3]
}
```

说明：
- 仅支持 `BUY` 方向帖子。
- 单次最多提交 5 个帖子 ID，超过上限后端会直接返回业务错误。
- 若同一帖子重复提交，会在 `errors` 中返回 `ALREADY_ACCEPTED`。

成功响应:

```json
{
    "code": 0,
    "message": {
        "results": [
            {
                "post_id": 1,
                "order_id": 201,
                "status": "PENDING"
            }
        ],
        "errors": [
            {
                "post_id": 3,
                "error": "ALREADY_ACCEPTED",
                "message": "该帖子已申请过"
            }
        ]
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 99 - 请求帖子数量超过 5 个，或参数不合法。

#### 4.5.8.1 单个帖子接单 (POST: /posts/{post_id}/accept)

用途: 当前用户对指定帖子发起接单申请。

请求头: Authorization: Bearer <token>。

说明：
- `BUY` 方向帖子为申请制，申请后须等待发布者同意；该帖的 `PENDING` 订单不计入当前接单数。
- `SELL` 方向帖子为征集制，`PENDING` 订单即时计入当前接单数。
- 若用户刚刚取消过该帖子的申请，后端会返回 99 并提示冷静期。

成功响应:

```json
{
    "code": 0,
    "message": {
        "order_id": 1001,
        "post_id": 2001,
        "current_accepters": 1,
        "max_accepters": 3,
        "accepted": true,
        "status": "PENDING"
    }
}
```

#### 4.5.9 查看接单申请列表 (GET: /posts/{post_id}/applications)

用途: 帖子发布者查看当前帖子下的申请列表，用于同意/拒绝接单。仅帖子拥有者可访问。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "post_id": 1
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "applications": [
            {
                "application_id": 301,
                "post_id": 1,
                "applicant": {
                    "user_id": 201,
                    "user_name": "李同学",
                    "avatar": "https://...",
                    "credit_score": 98,
                    "is_verified": true,
                    "completed_order_count": 24
                },
                "note": "我现在就在南区菜鸟旁边，15 分钟内可以送到",
                "status": "PENDING",
                "created_at": "2026-05-25T16:08:00"
            }
        ]
    }
}
```

说明：
- 申请列表直接复用 `order` 表中的 `PENDING` 记录。
- `completed_order_count` 为申请人的历史已完成订单数，后端会一次性聚合返回。

常见错误:

- code: 105 - Token 失效或缺失。
- code: 102 - 仅帖子拥有者可查看申请列表。
- code: 103 - 帖子不存在。

### 4.6 Order 订单模块

#### 4.6.1 我的订单 (GET: /orders/me)

用途: 获取当前用户相关的订单列表。

请求头: Authorization: Bearer <token>。

请求示例:

JSON
{
    "role": "buyer",
    "status": "PENDING",
    "start_time": "2025-09-01T00:00:00Z",
    "end_time": "2025-09-30T23:59:59Z",
    "page": 1,
    "size": 20
}
成功响应:

```json
{
    "code": 0,
    "message": {
        "total": 3,
        "page": 1,
        "page_size": 20,
        "list": [
            {
                "order_id": 5001,
                "item_type": "POST",
                "item_id": 1001,
                "status": "PENDING",
                "buyer_id": 2001,
                "seller_id": 3001,
                "initiator_id": 2001,
                "trigger_type": "APPLY",
                "accepted_time": null,
                "create_time": "2025-09-05T10:00:00Z",
                "update_time": "2025-09-05T10:00:00Z",
                "meta_data": null,
                "buyer": {
                    "user_id": 2001,
                    "user_uuid": "...",
                    "user_name": "买家",
                    "avatar": "/static/avatar/avatar_2001.png",
                    "sex": "未知",
                    "credit_score": 80,
                    "is_verified": false,
                    "user_type": "user"
                },
                "seller": {
                    "user_id": 3001,
                    "user_uuid": "...",
                    "user_name": "卖家",
                    "avatar": "/static/avatar/avatar_3001.png",
                    "sex": "未知",
                    "credit_score": 90,
                    "is_verified": true,
                    "user_type": "user"
                }
            }
        ]
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 99 - 参数不合法。
- code: 301 - 订单列表查询失败。

#### 4.6.2 按项目查订单 (GET: /orders/by-item)

用途: 根据 `item_id` 与 `item_type` 查询关联订单。仅项目拥有者或管理员可查询该项目下的订单。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "item_id": 1001,
    "item_type": "POSTS"
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "item_id": 1001,
        "item_type": "POSTS",
        "list": [
            {
                "order_id": 5001,
                "item_type": "POSTS",
                "item_id": 1001,
                "status": "PENDING",
                "buyer_id": 2001,
                "seller_id": 3001,
                "initiator_id": 2001,
                "trigger_type": "APPLY",
                "accepted_time": null,
                "create_time": "2025-09-05T10:00:00Z",
                "update_time": "2025-09-05T10:00:00Z",
                "meta_data": null,
                "buyer": {
                    "user_id": 2001,
                    "user_uuid": "...",
                    "user_name": "买家",
                    "avatar": "/static/avatar/avatar_2001.png",
                    "sex": "未知",
                    "credit_score": 80,
                    "is_verified": false,
                    "user_type": "user"
                },
                "seller": {
                    "user_id": 3001,
                    "user_uuid": "...",
                    "user_name": "卖家",
                    "avatar": "/static/avatar/avatar_3001.png",
                    "sex": "未知",
                    "credit_score": 90,
                    "is_verified": true,
                    "user_type": "user"
                }
            }
        ]
    }
}
```

常见错误:

- code: 99 - 参数缺失或无效。
- code: 103 - 关联项目不存在。
- code: 301 - 关联订单查询失败。

#### 4.6.3 订单详情 (GET: /orders/{order_id})

用途: 获取指定订单的详细信息，仅订单相关方可查看。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "order_id": 5001
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "order_id": 5001,
        "item_type": "POST",
        "item_id": 1001,
        "status": "PENDING",
        "buyer_id": 2001,
        "seller_id": 3001,
        "initiator_id": 2001,
        "trigger_type": "APPLY",
        "accepted_time": null,
        "create_time": "2025-09-05T10:00:00Z",
        "update_time": "2025-09-05T10:00:00Z",
        "meta_data": null,
        "buyer": {
            "user_id": 2001,
            "user_uuid": "...",
            "user_name": "买家",
            "avatar": "/static/avatar/avatar_2001.png",
            "sex": "未知",
            "credit_score": 80,
            "is_verified": false,
            "user_type": "user"
        },
        "seller": {
            "user_id": 3001,
            "user_uuid": "...",
            "user_name": "卖家",
            "avatar": "/static/avatar/avatar_3001.png",
            "sex": "未知",
            "credit_score": 90,
            "is_verified": true,
            "user_type": "user"
        }
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 102 - 权限不足（非买家且非卖家）。
- code: 103 - 订单不存在。
- code: 301 - 订单详情查询失败。

#### 4.6.4 订单操作：同意 / 拒绝 / 完成 / 取消

用途: 对订单执行状态变更操作。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{}
```

可用接口:

- POST /orders/{order_id}/approve
- POST /orders/{order_id}/reject
- POST /orders/{order_id}/complete
- POST /orders/{order_id}/submit-delivery
- POST /orders/{order_id}/accept-delivery
- POST /orders/{order_id}/cancel

权限说明:

- `approve` 和 `reject` 仅限卖家（帖子发布者）操作。
- `submit-delivery` 由卖家提交已交付状态。
- `accept-delivery` 由买家确认收货并完成订单；`complete` 为兼容接口，内部等价于 `accept-delivery`。
- `cancel` 仅限买家或卖家取消，若订单为 PENDING，则发起人也可取消。
- `cancel` 接口会额外返回 `curr_accepters`，用于客户端更新当前帖子接单数。

成功响应示例:

```json
{
    "code": 0,
    "message": {
        "order_id": 5001,
        "item_type": "POST",
        "item_id": 1001,
        "status": "APPROVED",
        "buyer_id": 2001,
        "seller_id": 3001,
        "initiator_id": 2001,
        "trigger_type": "APPROVE",
        "accepted_time": "2025-09-05T12:00:00Z",
        "create_time": "2025-09-05T10:00:00Z",
        "update_time": "2025-09-05T12:00:00Z",
        "meta_data": null,
        "buyer": null,
        "seller": null
    }
}
```



#### 4.6.5 发布订单评价 (POST: /orders/reviews)

用途: 对已完成订单发起双盲评价。仅订单相关方可操作。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "order_id": 5001,
    "reviewee_id": 3001,
    "review_type": "INITIAL",
    "rating": 5,
    "content": "对方响应很快，沟通顺畅",
    "is_anonymous": true,
    "attachment_ids": [456],
    "parent_id": null
}
```

说明：
- `review_type` 支持 `INITIAL`、`ADDITIONAL`、`REPLY`。
- `is_anonymous=true` 表示评价内容在双盲期内匿名展示。
- `attachment_ids` 可选，先上传附件后再将附件绑定到当前评价。
- `parent_id` 为可选，用于追评/回评关联上一条评价。

成功响应:

```json
{
    "code": 0,
    "message": {
        "review_id": 9001,
        "order_id": 5001,
        "reviewer_id": 2001,
        "reviewee_id": 3001,
        "review_type": "FIRST",
        "parent_id": null,
        "rating": 5,
        "content": "对方响应很快，沟通顺畅",
        "is_anonymous": true,
        "is_visible": false,
        "attachment_urls": [],
        "create_time": "2025-09-05T12:00:00Z",
        "update_time": "2025-09-05T12:00:00Z"
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 99 - 请求参数不合法，或评价内容不完整。
- code: 102 - 仅订单相关方可评价。
- code: 301 - 订单不存在或订单未完成。

#### 4.6.6 获取订单评价列表 (GET: /orders/{order_id}/reviews)

用途: 查看某个订单下的评价树。仅订单相关方可查看。

请求头: Authorization: Bearer <token>。

请求示例:

```json
{
    "order_id": 5001
}
```

说明：
- 返回结果按订单维度组织为树状结构。
- 双盲期到期后，系统会自动解封可见性。

成功响应:

```json
{
    "code": 0,
    "message": {
        "items": [
            {
                "review_id": 9001,
                "order_id": 5001,
                "reviewer_id": 2001,
                "reviewee_id": 3001,
                "review_type": "FIRST",
                "parent_id": null,
                "rating": 5,
                "content": "对方响应很快，沟通顺畅",
                "is_anonymous": true,
                "is_visible": false,
                "create_time": "2025-09-05T12:00:00Z",
                "update_time": "2025-09-05T12:00:00Z"
            }
        ]
    }
}
```

常见错误:

- code: 105 - Token 失效或缺失。
- code: 102 - 仅订单相关方可查看评价。
- code: 301 - 订单不存在、订单未完成，或评价查询失败。


### 4.7 评论模块 (Comments)

说明：评论模块支持对帖子/商品/订单的多层回复（盖楼）机制。所有响应遵循统一返回格式 `{ "code": int, "message": ... }`。

#### 4.7.1 发布评论/回复 (POST: /comments)

用途：发布根评论或对已有评论进行回复（盖楼）。

请求头：Authorization: Bearer <token>（必须登录）

请求示例:

```json
{
    "target_type": "POST",     
    "target_id": 1001,
    "parent_id": null,          
    "content": "这是一个评论内容",
    "attachment_ids": [123]
}
```

说明：
- `parent_id` 可选，传 `null` 或不传表示发布根评论；传入 `parent_id` 表示回复该父评论（请确保父评论存在）。
- `attachment_ids` 可选，评论图片会先上传到附件接口，再在评论落库后自动绑定到当前评论。

成功响应:

```json
{
    "code": 0,
    "message": {
        "comment_id": 2001,
        "user_id": 1001,
        "target_type": "POST",
        "target_id": 1001,
        "parent_id": null,
        "content": "这是一个评论内容",
        "is_deleted": false,
        "create_time": "2026-05-21T12:00:00",
        "update_time": "2026-05-21T12:00:00",
        "attachment_urls": ["/static/comment/comment-1.png"]
    }
}
```

常见错误:
- code: 105 - Token 无效或已失效（未登录）。
- code: 99  - 请求参数校验失败（例如 `target_id` 类型不正确、`content` 为空）。
- code: 301 - 父评论不存在或已被删除（当 `parent_id` 指向的评论不可用时）。

#### 4.7.2 软删除评论 (DELETE: /comments/{comment_id})

用途：对指定评论执行软删除。仅评论所有者或管理员可操作。

请求头：Authorization: Bearer <token>（必须登录）

请求示例:

```
(请求不需要 body)
```

成功响应:

```json
{
    "code": 0,
    "message": { "message": "评论已删除" }
}
```

说明：删除操作不会物理删除记录，而是将 `is_deleted` 置为 `true` 并将被删除评论的 `content` 替换为 `"该评论已由用户删除"`，以保留树状结构和回复上下文。

常见错误:
- code: 105 - Token 无效或已失效。
- code: 102 - 权限不足（非所有者且非管理员）。
- code: 301 - 评论不存在（无法找到指定 `comment_id`）。

#### 4.7.3 获取目标的根评论列表（游标分页） (GET: /comments/{target_type}/{target_id})

用途：获取指定目标（帖子/商品/订单）的顶级根评论列表（不含被软删除的根评论），按 `comment_id` 倒序返回并支持游标分页。

请求头：无（公开接口）

查询参数（示例用 JSON 表示）：

```json
{
    "cursor": null,   
    "size": 20
}
```

SQL 过滤核心：
```
WHERE target_type = :target_type
    AND target_id = :target_id
    AND parent_id IS NULL
    AND is_deleted = FALSE
    AND comment_id < :cursor  -- 可选
ORDER BY comment_id DESC
LIMIT :size
```

返回项说明：每个根评论节点同时携带 `reply_count`（该楼层的回复总数）和 `preview_replies`（最新 2~3 条子回复预览，供前端展示）。

成功响应示例:

```json
{
    "code": 0,
    "message": {
        "items": [
            {
                "comment_id": 2001,
                "user_id": 1001,
                "target_type": "POST",
                "target_id": 1001,
                "content": "楼主评论内容",
                "is_deleted": false,
                "create_time": "2026-05-21T12:00:00",
                "update_time": "2026-05-21T12:00:00",
                "attachment_urls": ["/static/comment/comment-1.png"],
                "reply_count": 5,
                "preview_replies": [
                    {"comment_id": 2005, "user_id":1002, "content":"最新回复1", "create_time":"2026-05-21T12:05:00"},
                    {"comment_id": 2004, "user_id":1003, "content":"最新回复2", "create_time":"2026-05-21T12:03:00"}
                ]
            }
        ],
        "next_cursor": 1990
    }
}
```

常见错误:
- code: 99  - 请求参数校验失败（`size` 范围或 `target_type` 非允许值）。

#### 4.7.4 获取单条根评论下的回复流（平铺、正序，游标分页） (GET: /comments/{comment_id}/replies)

用途：当用户点击“查看全部回复”时，平铺拉取该根评论下的所有子回复，按创建时间正序返回，支持游标分页。

请求头：无（公开接口）

查询参数（示例用 JSON 表示）：

```json
{
    "cursor": null,
    "size": 20
}
```

SQL 过滤核心：
```
WHERE parent_id = :comment_id
    AND is_deleted = FALSE
    AND comment_id > :cursor  -- 可选
ORDER BY create_time ASC
LIMIT :size
```

成功响应示例:

```json
{
    "code": 0,
    "message": {
        "items": [
            {"comment_id": 2002, "user_id":1002, "parent_id":2001, "content":"回复1", "create_time":"2026-05-21T12:01:00"},
            {"comment_id": 2003, "user_id":1003, "parent_id":2001, "content":"回复2", "create_time":"2026-05-21T12:02:00"}
        ],
        "next_cursor": 2003
    }
}
```

常见错误:
- code: 301 - 根评论不存在（`comment_id` 无对应记录）。
- code: 99  - 请求参数校验失败。

### 4.8 CHAT 私信模块

说明：私信模块为双人会话制，支持会话初始化、消息发送、游标历史、一键已读、2 分钟撤回和单边本地删除。路由前缀固定为 `/chats`。消息体支持 `context_type` / `context_id`，可把聊天挂到帖子等业务上下文上。

#### 4.8.1 会话初始化 (POST: /chats/sessions/init)

用途：按用户 ID 自动排序创建或复用双人会话，可选附带业务上下文（例如帖子）。

请求头：Authorization: Bearer <token>（必须登录）

请求示例:

```json
{
    "peer_id": 1002,
    "context_type": "POST",
    "context_id": 9202
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "session_id": 3001,
        "user_one_id": 1001,
        "user_two_id": 1002,
        "peer_id": 1002,
        "context_type": "POST",
        "context_id": 9202,
        "last_message_content": null,
        "last_message_time": null,
        "unread_count": 0
    }
}
```

#### 4.8.2 会话列表 (GET: /chats/sessions)

用途：获取当前用户的收件箱列表，并返回每个会话的未读数。

请求头：Authorization: Bearer <token>（必须登录）

成功响应:

```json
{
    "code": 0,
    "message": {
        "items": [
            {
                "session_id": 3001,
                "user_one_id": 1001,
                "user_two_id": 1002,
                "peer_id": 1002,
                "context_type": "POST",
                "context_id": 9202,
                "last_message_content": "你好",
                "last_message_time": "2026-05-21T12:10:00",
                "unread_count": 1
            }
        ]
    }
}
```

#### 4.8.3 发送消息 (POST: /chats/messages)

用途：发送文本消息，可选携带附件、引用消息和业务上下文。

请求头：Authorization: Bearer <token>（必须登录）

请求示例:

```json
{
    "session_id": 3001,
    "content": "我收到了",
    "attachment_ids": [456],
    "quote_message_id": 455,
    "context_type": "POST",
    "context_id": 9202
}
```

成功响应:

```json
{
    "code": 0,
    "message": {
        "message_id": 4001,
        "session_id": 3001,
        "sender_id": 1001,
        "content": "我收到了",
        "context_type": "POST",
        "context_id": 9202,
        "is_read": false,
        "is_recalled": false,
        "is_deleted_by_sender": false,
        "is_deleted_by_receiver": false,
        "quote_message_id": 455,
        "create_time": "2026-05-21T12:11:00",
        "attachment_urls": ["/static/chat/chat-1.png"]
    }
}
```

#### 4.8.4 历史消息拉取 (GET: /chats/sessions/{session_id}/messages)

用途：按游标分页拉取消息历史，支持消息单边删除过滤，并返回消息上下文字段。

请求头：Authorization: Bearer <token>（必须登录）

请求示例:

```json
{
    "cursor": null,
    "size": 20
}
```

#### 4.8.5 一键已读 (PATCH: /chats/sessions/{session_id}/read)

用途：清除对方发来的未读红点。

请求头：Authorization: Bearer <token>（必须登录）

#### 4.8.6 撤回消息 (PATCH: /chats/messages/{message_id}/recall)

用途：2 分钟内撤回自己发送的消息。

请求头：Authorization: Bearer <token>（必须登录）

#### 4.8.7 单边删除 (DELETE: /chats/messages/{message_id}/local)

用途：仅删除当前用户一侧可见的消息，不影响对方视图。

请求头：Authorization: Bearer <token>（必须登录）

---

文件位置：评论接口实现位于项目代码中：`app/api/comment.py`、`app/services/comment_service.py` 与 `app/schemas/comment.py`；聊天接口位于 `app/api/chat.py`、`app/services/chat_service.py` 与 `app/schemas/chat.py`。文档和实现保持一致。

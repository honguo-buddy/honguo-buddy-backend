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

#### 4.2.1 获取当前用户信息 (GET: /user/info)

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

说明：
- `avatar` 字段不再直接由客户端提交 URL 填写，而是由 `user.avatar_id`（外键指向 `attachment.attachment_id`）提供，接口返回的 `avatar` 为 `/static/...` 相对路径，可直接在前端加载。

#### 4.2.3 修改本人资料 (PATCH: /users/me)

用途: 修改当前登录用户资料。`avatar_id` 用于指定头像附件记录，不再直接提交头像 URL。若已上传头像附件，接口返回中的 `avatar` 仍为可直接访问的静态资源 URL。

说明：
- 请求体中 `avatar_id` 必须为 `attachment` 表中已存在且 `target_type=USER` 的附件 ID，且该附件的 `creator_id` 必须等于当前用户（即只能使用自己上传的图片作为头像）。
- 管理员（`is_admin=true`）通过管理员接口 (`PUT /user/{user_id}`) 可为任意用户设置 `avatar_id`（管理员无须是附件 uploader）。
- 上传附件的接口会在 `target_type=USER` 并且 `target_id` 指定为某用户时，自动把该附件回填为该用户的 `avatar_id`。

#### 4.2.4 获取他人公开资料 (GET: /users/{user_id})

用途: 获取他人公开资料。返回中的 `avatar` 同样来自 `attachment` 关联记录，前端可直接通过 `/static/...` 访问。

### 4.3 附件上传模块

#### 4.3.1 上传附件 (POST: /attachments/upload)

用途: 上传图片附件并返回附件 ID 与可直接访问的静态资源 URL。若 `target_type=USER`，系统会自动把该附件回填到对应用户的 `avatar_id`。

返回示例:

```json
{
    "code": 0,
    "message": {
        "id": 123,
        "url": "/static/avatar/avatar_123.png"
    }
}

额外说明：
- 文件存储：上传后文件保存在服务端 `app/static/` 子目录下（例如 `app/static/avatar/`、`app/static/goods/`），返回的 `url` 为相对路径（以 `/static/` 开头）。
- 文件命名：为避免冲突，保存时会使用格式 `{type}_{user_id}_{timestamp}{ext}`（例如 `avatar_1001_1680000000.png`），其中 `type` 对应 `target_type`（`avatar` 或 `goods` 等）。
- 附件记录字段：`attachment` 表包含 `attachment_id、target_type、target_id、url、creator_id、is_deleted` 等字段。
- 权限规则：只有附件的上传者（`creator_id`）本人可以把该附件设为自己的 `avatar_id`；管理员接口允许为任意用户回填 `avatar_id`。
```

### 4.4 Category 模板分类模块

说明：

- 创建/更新/删除 接口为管理员权限（需要 `is_admin=true`）。
- 获取列表与详情对外开放，供前端在发布页面选择模板使用。

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

#### 4.4.2 获取模板分类列表 (GET: /categories/)

用途：获取模板分类列表，供前端展示分类供选择。

查询参数：

- `type`（可选）：按业务类型过滤，取值 `POST` 或 `GOODS`，示例：`GET /categories?type=POST`。

#### 4.4.3 获取模板分类详情 (GET: /categories/{category_id})

用途：按 ID 获取模板分类详情，供前端展示单个模板定义。

#### 4.4.4 更新模板分类 (PUT: /categories/{category_id})

用途：更新模板分类信息（管理员）。

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

约束说明：

- `config_json` 更新时同样必填，且不能为空对象。
- `icon` 可选，传 `null` 可清空图标。

#### 4.4.5 删除模板分类 (DELETE: /categories/{category_id})

用途：软删除模板分类（管理员）。

成功响应示例：

```json
{
    "code": 0,
    "message": {
        "category_id": 1,
        "deleted": true
    }
}
```
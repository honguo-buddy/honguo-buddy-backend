from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "HONGUO-BUDDY"    
    
    # 开发模式配置（仅用于本地/测试环境）
    DEBUG: bool
    DEBUG_MASTER_PASSWORD: str
    DEBUG_SKIP_PASSWORD_CHECK: bool = False
    WX_APP_ID: str | None = None
    WX_APP_SECRET: str | None = None
    WX_CODE_TO_SESSION_URL: str = "https://api.weixin.qq.com/sns/jscode2session"
    
    # 数据库配置
    DATABASE_URL: str
    
    #Token过期时间
    TOKEN_EXPIRE_TIME: int = 60*24
    #密钥(Token)
    SECRET_KEY: str = "HAJIMI"
    #加密方式(Token) HS256对称加密,RS256非对称加密
    TOKEN_ALGORITHM: str = "HS256"
    
    # 图像验证码配置
    CAPTCHA_EXPIRE_SECONDSl: int =300      # 验证码有效期
    CAPTCHA_LENGTH: int =4                # 验证码字符长度

    #对比天数
    COMPARE_DAYS: int = 3 
    
    EMAIL_VERIFY_EXPIRE_MINUTES: int = 30  # 邮箱验证链接有效期（分钟）
    LOGIN_EXPIRE_DAYS: int = 30  # 登录超时时间（天）
    
    # 邮箱配置
    EMAIL_FROM: str
    SMTP_SERVER: str
    SMTP_PORT: int
    SMTP_USER: str
    SMTP_PASSWORD: str  # QQ邮箱授权码
    
    # Redis配置
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_PASSWORD: str = ""

    # 短信/阿里云配置
    ALI_ACCESS_KEY_ID: str | None = None
    ALI_ACCESS_KEY_SECRET: str | None = None
    SMS_TEMPLATE_CODE: str | None = None
    SMS_SIGN_NAME: str | None = None
    SMS_CODE_TTL_SECONDS: int = 300
    USER_PROFILE_CACHE_TTL: int = 3600  # user profile Read-Through cache TTL (seconds)
    SMS_RATE_LIMIT_SECONDS: int = 60
    SMS_VERIFIED_WINDOW_SECONDS: int = 900

    # 业务常数配置 - 信用与订单管理
    USER_INITIAL_CREDIT_SCORE: int = 60  # 用户初始信用分
    ORDER_COMPLETE_CREDIT: int = 10      # 订单完成后卖家获得的积分奖励
    ORDER_AUTO_CONFIRM_HOURS: int = 12    # CONFIRMED 状态超时自动完结时限（小时）
    ORDER_ACCEPT_COOLDOWN_SECONDS: int = 300  # 申请取消后冷静期（秒）
    ORDER_ACCEPT_CANCEL_DAILY_LIMIT: int = 3  # 同一用户同一帖子每天允许取消次数
    REVIEW_DOUBLE_BLIND_DAYS: int = 1    # 评价双盲期（天）
    HISTORY_TTL_SECONDS: int = 30 * 86400 # 历史记录过期时间（秒），默认30天
    HISTORY_MAX_SIZE: int = 100 # 历史记录最大条数
    MAX_OPEN_POSTS_PER_USER: int = 3  # 用户同时开启的帖子/商品上限
    GLOBAL_CANCEL_DAILY_LIMIT: int = 10 # 全局取消申请每日限制次数（已弃用，保留兼容）
    LIGHTNING_CANCEL_LIMIT_SECONDS: int = 600  # 闪电退单分水岭阈值（秒），订单创建后此时限内取消视为闪电退单
    LIGHTNING_CANCEL_DAILY_LIMIT: int = 10  # 闪电退单每人每日上限次数
    
    # 业务常数配置 - 错误码
    
    #正确返回码
    SUCCESS_CODE: int = 0 #正确返回码
    #错误码
    
    #主
    UNKNOWN_ERROR_CODE: int = 97 #未知错误
    HTTP_ERROR_CODE: int = 98 #HTTP错误
    REQ_ERROR_CODE: int = 99 #请求参数错误
    
    #auth
    REGISTER_FAILED_CODE: int = 100 #注册失败
    LOGIN_FAILED_CODE: int = 101 #登入失败
    INSUFFICIENT_AUTHORITY_CODE: int = 102 #权限不足
    USER_GET_FAILED_CODE: int = 103 #用户获取失败
    UPDATEPROFILE_FAILED_CODE: int = 104 #用户个人信息更新失败
    TOKEN_INVALID_CODE: int = 105 #Token失效
    CAPTCHA_GEN_FAILED_CODE: int = 106    # 验证码生成失败
    CAPTCHA_INVALID_CODE: int = 107       # 验证码ID无效
    CAPTCHA_MISMATCH_CODE: int = 108      # 验证码不匹配
    CAPTCHA_REQ_NEEDED_CODE: int = 109 #验证码请求
    EMAIL_VERIFIED_NEEDED_CODE: int = 110 #需要邮箱验证
    
    #data
    DATA_GET_FAILED_CODE: int = 301 #数据获取失败

    # 微信订阅消息配置
    WX_APP_ID: str = ""
    WX_APP_SECRET: str = ""
    WX_ACCESS_TOKEN_URL: str = "https://api.weixin.qq.com/cgi-bin/token"
    WX_SUBSCRIBE_SEND_URL: str = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"
    WX_TEMPLATE_ORDER_STATUS: str = "Q3HbZnyQcTQgN61DJPfzEQGGeWfwjx19sE9MavHhyBI"  # 发货状态提醒·万能订单流转模板
    WX_TEMPLATE_NEW_APPLICATION: str = "FDpm9NoLGRMa0LlE6beYuhh2vTKl541qHvauNW0smZY"  # 购买申请通知·万能前置审批互动
    WX_ACCESS_TOKEN_CACHE_KEY: str = "wx:access_token:cache"  # Redis 缓存键
    WX_ACCESS_TOKEN_CACHE_TTL: int = 6600  # access_token 缓存有效期（秒），110分钟


settings = Settings()


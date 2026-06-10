from typing import Any

from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.dynamic_config import DEFAULT_DYNAMIC_CONFIGS, DynamicConfigManager

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    _dynamic_overrides: dict[str, Any] = PrivateAttr(default_factory=dict)

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

    GLOBAL_CANCEL_DAILY_LIMIT: int = 10 # 全局取消申请每日限制次数（已弃用，保留兼容）
    
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

    @staticmethod
    def _get_dynamic_default(name: str) -> Any:
        meta = DEFAULT_DYNAMIC_CONFIGS[name]
        return DynamicConfigManager._convert_value(meta["config_type"], meta["config_value"])

    def __getattr__(self, name: str) -> Any:
        if name in DEFAULT_DYNAMIC_CONFIGS:
            try:
                overrides = object.__getattribute__(self, "_dynamic_overrides")
            except AttributeError:
                overrides = {}
            if name in overrides:
                return overrides[name]
            return DynamicConfigManager().get(name, self._get_dynamic_default(name))
        raise AttributeError(f"{self.__class__.__name__!s} object has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in DEFAULT_DYNAMIC_CONFIGS:
            try:
                overrides = object.__getattribute__(self, "_dynamic_overrides")
            except AttributeError:
                overrides = {}
                object.__setattr__(self, "_dynamic_overrides", overrides)
            overrides[name] = value
            return
        super().__setattr__(name, value)


settings = Settings()


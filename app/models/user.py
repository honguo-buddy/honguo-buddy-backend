from sqlalchemy import Column, Integer, String, Boolean, BigInteger, Text, Enum as SAEnum, DateTime
from sqlalchemy.dialects.mysql import BINARY
from sqlalchemy.orm import relationship
from app.db.base import Base
import enum

# 定义用户类型枚举
class UserType(enum.Enum):
    USER = "user"
    ADMIN = "admin"             # 管理员

# USER数据库表类-模型
class User(Base):
    __tablename__ = "user"
    
    # id
    user_id = Column(BigInteger, primary_key=True, index=True) # 内部主键
    
    # 用户UUID
    user_uuid = Column(BINARY(16), unique=True, index=True, nullable=False, comment="用户UUID，外部唯一标识")
    
    # 昵称
    user_name = Column(String(255), unique=True, nullable=True, comment="用户名唯一")
    
    # 头像
    avatar = Column(String(255), nullable=True, comment="用户头像 URL")
    
    # 性别
    sex = Column(SAEnum("男", "女", "未知", name="user_sex"), default="未知", nullable=False, comment="用户性别：男/女/其他")
    
    # email 邮箱用于认证学生身份
    email = Column(String(255), unique=True, index=True, nullable=True) 
    
    # phonenumber :手机号可选
    phonenumber = Column(String(25), unique=True, index=True, nullable=True, comment="手机号") 
    
    # 将 Enum 存储为枚举的 value（小写字符串）
    user_type = Column(
        SAEnum(
            UserType,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            name="usertype",
            native_enum=False,
        ),
        default=UserType.USER,
        comment="用户身份类型：普通用户/管理员",
    )
    
    # 状态和权限字段
    is_active = Column(Boolean, default=True, comment="用户是否有效(可被封禁)")
    is_deleted = Column(Boolean, default=False, comment="用户是否被删除")
    
    is_admin = Column(Boolean, default=False, comment="用户是否是超级管理员") 
    
    is_verified = Column(Boolean, default=False, comment="校园身份信息是否通过验证")
    
    # 登录信息字段
    last_login_ip = Column(String(64), nullable=True) # 最近登录IP
     
    last_login_time = Column(BigInteger, nullable=True) # 最近登录时间（时间戳）
    
    # 微信小程序相关字段
    wechat_openid = Column(
        String(128), 
        unique=True,  # 每个 openid 只能绑定一个用户
        index=True, 
        nullable=False,
        comment="微信小程序 openid，用于小程序免密登录(唯一指定一个用户)"
    )
    
    wechat_session_key = Column(
        String(256),
        nullable=True,
        comment="微信 session_key (加密存储)，用于解密用户敏感数据"
    )
    
    wechat_unionid = Column(
        String(128),
        unique=False,  # 允许多个用户绑定同一个 unionid
        nullable=True,
        comment="微信 UnionID（如果小程序绑定了开放平台，非唯一）"
    )
    
    wechat_bind_time = Column(
        DateTime,
        nullable=True,
        comment="微信绑定时间"
    )
    
    # 创建时间字段
    create_time = Column(DateTime, default=None, comment="创建时间")
    
    # 与用户访问日志表的关系
    user_access_logs = relationship("UserAccessLog", back_populates="user")

# 运行时兼容性帮助：将传入的字符串（如来自 API 的 user_type）映射到 UserType
def parse_user_type(value: str) -> UserType:
    """把可能的字符串值（大小写不确定）解析为 UserType 成员。

    使用示例：
        user_type = parse_user_type(request.json().get('user_type'))
    如果无法解析，将返回 UserType.USER 作为默认值。
    """
    if not value:
        return UserType.USER
    # 先尝试匹配 value 本身（通常是小写存储值）
    for member in UserType:
        if value == member.value:
            return member
    # 再尝试按 name 大小写匹配（如 'ADMIN'）
    try:
        return UserType[value.upper()]
    except Exception:
        return UserType.EXTERNAL
    

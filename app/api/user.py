
from typing import Union

from fastapi import APIRouter, Depends

from app.api import get_current_user
from app.core import settings
from app.schemas import AuthErrorResponse, ResponseModel, user as UserSchema

router = APIRouter()


@router.get("/info", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def get_profile(current_user: UserSchema = Depends(get_current_user)):
	"""获取当前登录用户基础信息。"""
	return ResponseModel(
		code=settings.SUCCESS_CODE,
		message={
            "userUuid": current_user.user_uuid,
			"userName": current_user.user_name,
			"isAdmin": current_user.is_admin,
			"isVerified": current_user.is_verified,
			"userType": current_user.user_type,
		},
	)
from fastapi import APIRouter, HTTPException, status, Response, Request
from pydantic import BaseModel
from middleware.auth import create_token
from user_store import authenticate

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginRequest, response: Response):
    user = authenticate(body.username, body.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid username or password")
    token = create_token(user["id"], user["username"], user["role"])
    # also set httpOnly cookie for browser navigation
    response.set_cookie("token", token, httponly=True, samesite="lax", max_age=28800)
    return {
        "access_token": token,
        "token_type":   "bearer",
        "user": {
            "id":       user["id"],
            "username": user["username"],
            "role":     user["role"],
            "projects": user.get("projects", []),
        },
    }


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("token")
    return {"message": "Logged out"}


@router.get("/me")
def me(request: Request):
    from middleware.auth import get_current_user
    import asyncio
    # sync wrapper
    import inspect
    loop = asyncio.new_event_loop()
    user = loop.run_until_complete(get_current_user(request))
    loop.close()
    return user

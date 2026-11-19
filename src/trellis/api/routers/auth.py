"""
src/trellis/api/routers/auth.py
Lightweight HMAC-SHA256 JWT issuance and role verification for student, parent, and admin roles.
Uses Python standard library only (zero native C extension dependencies).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

router = APIRouter(prefix="/auth", tags=["Authentication"])

RoleType = Literal["student", "parent", "admin"]
_BEARER_SCHEME = HTTPBearer(auto_error=False)


def _get_jwt_secret() -> bytes:
    return os.getenv("TRELLIS_JWT_SECRET", "trellis-dev-secret-change-in-prod").encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


def create_access_token(
    subject: str,
    role: RoleType,
    expires_in_seconds: int = 3600,
) -> str:
    """Signs a compact HS256 JWT token."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))}."
        f"{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))}"
    )
    signature = hmac.new(
        _get_jwt_secret(),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict:
    """Verifies HS256 signature and expiration of a JWT token."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT token.")

    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    expected_sig = hmac.new(_get_jwt_secret(), signing_input, hashlib.sha256).digest()
    actual_sig = _b64url_decode(parts[2])
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid token signature.")

    payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Token has expired.")
    return payload


class TokenRequest(BaseModel):
    subject: str = Field(..., min_length=1, description="Student, parent, or admin ID")
    role: RoleType = "student"
    expires_in_seconds: int = Field(default=3600, ge=60, le=86400)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: RoleType
    subject: str


class PrincipalClaims(BaseModel):
    sub: str
    role: RoleType


def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_BEARER_SCHEME),
) -> PrincipalClaims:
    """FastAPI dependency validating Bearer JWT tokens."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer authorization token.",
        )
    try:
        claims = decode_access_token(credentials.credentials)
        return PrincipalClaims(sub=claims["sub"], role=claims["role"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


@router.post("/token", response_model=TokenResponse)
def issue_token(payload: TokenRequest) -> TokenResponse:
    """Issues an HS256 JWT Bearer token for student, parent, or admin roles."""
    token = create_access_token(
        subject=payload.subject,
        role=payload.role,
        expires_in_seconds=payload.expires_in_seconds,
    )
    return TokenResponse(
        access_token=token,
        role=payload.role,
        subject=payload.subject,
    )


@router.get("/me", response_model=PrincipalClaims)
def read_me(principal: PrincipalClaims = Depends(get_current_principal)) -> PrincipalClaims:
    """Verifies the caller's Bearer JWT token and returns principal claims."""
    return principal

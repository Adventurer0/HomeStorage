"""Household identities, password hashing and revocable server-side sessions."""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import closing
from contextvars import ContextVar

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from typing import Literal

from .db import transaction

COOKIE_NAME = "homestorage_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ROUNDS = 600_000
request_actor = ContextVar("homestorage_actor", default=None)


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ROUNDS)
    return f"pbkdf2_sha256${PASSWORD_ROUNDS}${salt.hex()}${digest.hex()}"


# Unknown and disabled accounts still pay the normal password-verification cost.
_DUMMY_HASH = password_hash(secrets.token_urlsafe(32))


def password_matches(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        rounds = int(rounds)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= rounds <= 1_000_000:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), rounds)
        return hmac.compare_digest(digest.hex(), expected)
    except (TypeError, ValueError, AttributeError):
        return False


def public_user(row):
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
            "role": row["role"], "active": bool(row["active"])}


class UserInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class UserCreate(UserInput):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=8, max_length=4096)
    role: Literal["admin", "member", "viewer"] = "member"

    @field_validator("username")
    @classmethod
    def valid_username(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value):
            raise ValueError("用户名只能包含英文字母、数字、点、下划线和短横线")
        return value

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value):
        return value.strip()


class UserPatch(UserInput):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=8, max_length=4096)
    role: Literal["admin", "member", "viewer"] | None = None
    active: StrictBool | None = None

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("显示名称不能为空")
        return value


class PasswordChange(UserInput):
    current_password: str = Field(max_length=4096)
    password: str = Field(min_length=8, max_length=4096)


class LoginLimiter:
    """Bounded, per-client failed-login window; never stores passwords or tokens."""

    def __init__(self):
        self.failures = OrderedDict()
        self.lock = threading.Lock()

    def check(self, key):
        with self.lock:
            stamp, count = self.failures.get(key, (0, 0))
            if time.monotonic() - stamp < 300 and count >= 10:
                raise HTTPException(429, "登录尝试过多，请 5 分钟后重试", headers={"Retry-After": "300"})

    def fail(self, key):
        with self.lock:
            stamp, count = self.failures.pop(key, (0, 0))
            current = time.monotonic()
            self.failures[key] = (stamp, count + 1) if current - stamp < 300 else (current, 1)
            while len(self.failures) > 4096:
                self.failures.popitem(last=False)


class HouseholdSecurity:
    def __init__(self, connect, configured_password, clock):
        self.connect = connect
        self.configured_password = configured_password
        self.enabled = bool(configured_password)
        self.now = clock
        self.limiter = LoginLimiter()
        self.session_key = None

    def initialize(self, key):
        self.session_key = key
        with closing(self.connect()) as db, transaction(db):
            has_users = bool(db.execute("SELECT 1 FROM users LIMIT 1").fetchone())
            # Bootstrap is optional only for a fresh development database. Once
            # household identities exist, losing the environment variable must
            # never turn their private inventory into an anonymous workspace.
            self.enabled = bool(self.configured_password) or has_users
            if self.configured_password and not has_users:
                stamp = self.now()
                db.execute("INSERT INTO users(username,display_name,password_hash,role,active,created_at,updated_at) "
                           "VALUES('admin','管理员',?,'admin',1,?,?)",
                           (password_hash(self.configured_password), stamp, stamp))

    def session_digest(self, token):
        return hmac.new(self.session_key, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def user_for_request(self, request):
        if not self.enabled:
            return {"id": None, "username": "admin", "display_name": "本地开发", "role": "admin", "active": True}
        token = request.cookies.get(COOKIE_NAME, "")
        if not re.fullmatch(r"hs3\.[A-Za-z0-9_-]{43}", token):
            return None
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT users.* FROM household_sessions JOIN users ON users.id=household_sessions.user_id "
                "WHERE household_sessions.token_hash=? AND household_sessions.expires_at>? "
                "AND household_sessions.session_version=users.session_version AND users.active=1",
                (self.session_digest(token), time.time()),
            ).fetchone()
            return public_user(row) if row else None

    def current_user(self, request):
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "请先登录")
        return user

    def require_admin(self, request):
        user = self.current_user(request)
        if not self.enabled or user["role"] != "admin":
            raise HTTPException(403, "此功能需要家庭管理员权限并设置登录密码")
        return user

    def require_write(self, request):
        user = self.current_user(request)
        if user["role"] not in {"admin", "member"}:
            raise HTTPException(403, "只读成员不能修改库存")
        return user

    def login(self, username, password, request):
        if not self.enabled:
            return self.user_for_request(request), None
        client_key = request.client.host if request.client else "unknown"
        self.limiter.check(client_key)
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)).fetchone()
            valid = password_matches(password, row["password_hash"] if row else _DUMMY_HASH)
            if not row or not row["active"] or not valid:
                self.limiter.fail(client_key)
                raise HTTPException(401, "用户名或密码不正确")
            token = "hs3." + secrets.token_urlsafe(32)
            with transaction(db):
                # Recheck after BEGIN IMMEDIATE in case the administrator revoked
                # this account during password verification.
                current = db.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
                if not current["active"] or current["session_version"] != row["session_version"]:
                    raise HTTPException(401, "用户名或密码不正确")
                db.execute("DELETE FROM household_sessions WHERE expires_at<=?", (time.time(),))
                db.execute("INSERT INTO household_sessions(token_hash,user_id,session_version,expires_at) VALUES(?,?,?,?)",
                           (self.session_digest(token), row["id"], row["session_version"], time.time() + SESSION_SECONDS))
                # Retain at most 20 active browser sessions for each identity.
                db.execute("DELETE FROM household_sessions WHERE user_id=? AND token_hash NOT IN "
                           "(SELECT token_hash FROM household_sessions WHERE user_id=? ORDER BY expires_at DESC LIMIT 20)",
                           (row["id"], row["id"]))
            return public_user(current), token

    def logout(self, request):
        token = request.cookies.get(COOKIE_NAME, "")
        if token and self.enabled:
            with closing(self.connect()) as db, transaction(db):
                db.execute("DELETE FROM household_sessions WHERE token_hash=?", (self.session_digest(token),))


def build_household_router(security, audit):
    router = APIRouter(tags=["家庭成员"])

    @router.get("/api/account")
    def account(request: Request):
        return security.current_user(request)

    @router.get("/api/users")
    def users(request: Request):
        security.require_admin(request)
        with closing(security.connect()) as db:
            return [public_user(row) for row in db.execute("SELECT * FROM users ORDER BY id")]

    @router.post("/api/users", status_code=201)
    def create_user(body: UserCreate, request: Request):
        security.require_admin(request)
        encoded = password_hash(body.password)
        with closing(security.connect()) as db, transaction(db):
            stamp = security.now()
            try:
                result = db.execute("INSERT INTO users(username,display_name,password_hash,role,active,created_at,updated_at) "
                                    "VALUES(?,?,?,?,1,?,?)",
                                    (body.username, body.display_name or body.username, encoded, body.role, stamp, stamp))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "用户名已存在")
            user = public_user(db.execute("SELECT * FROM users WHERE id=?", (result.lastrowid,)).fetchone())
            audit(db, "user_create", note=json.dumps({"id": user["id"], "username": user["username"], "role": user["role"]}, ensure_ascii=False))
            return user

    @router.patch("/api/users/{user_id}")
    def update_user(user_id: int, body: UserPatch, request: Request):
        security.require_admin(request)
        changes = body.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise HTTPException(422, "成员信息不能设为空值")
        password = changes.pop("password", None)
        if password is not None:
            changes["password_hash"] = password_hash(password)
        with closing(security.connect()) as db, transaction(db):
            original = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if original is None:
                raise HTTPException(404, "家庭成员不存在")
            next_role = changes.get("role", original["role"])
            next_active = changes.get("active", original["active"])
            if original["active"] and original["role"] == "admin" and (not next_active or next_role != "admin"):
                admins = db.execute("SELECT COUNT(*) FROM users WHERE active=1 AND role='admin'").fetchone()[0]
                if admins <= 1:
                    raise HTTPException(409, "家庭必须保留至少一位启用的管理员")
            revoke = bool(set(changes) & {"password_hash", "role", "active"})
            if changes:
                changes["updated_at"] = security.now()
                assignments = ",".join(f"{key}=?" for key in changes)
                if revoke:
                    assignments += ",session_version=session_version+1"
                db.execute(f"UPDATE users SET {assignments} WHERE id=?", (*changes.values(), user_id))
                if revoke:
                    db.execute("DELETE FROM household_sessions WHERE user_id=?", (user_id,))
                audit(db, "user_update", note=json.dumps({"id": user_id, "fields": [
                    "password" if key == "password_hash" else key for key in changes if key != "updated_at"
                ]}, ensure_ascii=False))
            return public_user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    @router.post("/api/account/password")
    def change_password(body: PasswordChange, request: Request):
        user = security.current_user(request)
        if not security.enabled or user["id"] is None:
            raise HTTPException(403, "本地开发模式不支持修改成员密码")
        client_key = request.client.host if request.client else "unknown"
        security.limiter.check("password:" + client_key)
        with closing(security.connect()) as db, transaction(db):
            row = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            if not row or not row["active"] or not password_matches(body.current_password, row["password_hash"]):
                security.limiter.fail("password:" + client_key)
                raise HTTPException(401, "当前密码不正确")
            db.execute("UPDATE users SET password_hash=?,session_version=session_version+1,updated_at=? WHERE id=?",
                       (password_hash(body.password), security.now(), user["id"]))
            db.execute("DELETE FROM household_sessions WHERE user_id=?", (user["id"],))
            audit(db, "user_password_change", note=json.dumps({"id": user["id"]}))
        return {"ok": True, "reauthenticate": True}

    return router

"""Admin dashboard routes (Phase 6).

Operator-facing user management, usage analytics, and tier overrides.
Every route is gated by the `require_admin` dependency (users.is_admin=True);
non-admins receive 403 without information about the resource's existence.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import psycopg2

import db
from core.security import csrf_token, validate_csrf_token

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(request: Request) -> dict:
    """Resolve the session user and enforce the is_admin flag. Raises 403."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=403, detail="Administrator access required")
    user = await asyncio.to_thread(db.get_user_by_id, user_id)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(request: Request, page: int = 1, q: str | None = None):
    """Paginated, searchable user list with tier and subscription summary."""
    await require_admin(request)
    data = await asyncio.to_thread(db.admin_list_users, page=max(1, page), per_page=25, query=q)

    total = data["total"]
    per_page = data["per_page"]
    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse(request, "admin_users.html", {
        "users": data["users"],
        "total": total,
        "page": data["page"],
        "per_page": per_page,
        "total_pages": total_pages,
        "search_query": q or "",
        "csrf_token": csrf_token(request),
    })


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail_page(request: Request, user_id: int):
    """User detail view: profile, usage ledger, subscriptions, sessions."""
    await require_admin(request)
    detail = await asyncio.to_thread(db.admin_get_user_detail, user_id)
    if not detail:
        raise HTTPException(status_code=404, detail="User not found")

    return templates.TemplateResponse(request, "admin_user_detail.html", {
        "detail": detail,
        "user": detail["user"],
        "subscriptions": detail["subscriptions"],
        "usage_events": detail["usage_events"],
        "sessions": detail["sessions"],
        "csrf_token": csrf_token(request),
    })


@router.post("/users/{user_id}/tier")
async def admin_set_tier(request: Request, user_id: int, tier: str = Form(...), csrf_token_value: str | None = Form(None, alias="csrf_token")):
    """Manually override a user's tier. Persists immediately, then redirects."""
    await require_admin(request)
    validate_csrf_token(request, csrf_token_value)
    try:
        updated = await asyncio.to_thread(db.admin_set_user_tier, user_id, tier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except psycopg2.Error as exc:
        logger.error("admin_db_error", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Database service temporarily unavailable")
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    logger.info("Admin tier override: user_id=%s new_tier=%s", user_id, tier)
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.get("/usage", response_class=HTMLResponse)
async def admin_usage_page(request: Request, days: int = 14):
    """Aggregate token usage by plan over time."""
    await require_admin(request)
    rows = await asyncio.to_thread(db.admin_usage_summary, days=max(1, min(days, 90)))
    totals: dict[str, dict] = {}
    for row in rows:
        bucket = totals.setdefault(row["plan_key"], {"requests": 0, "total_tokens": 0})
        bucket["requests"] += int(row.get("requests") or 0)
        bucket["total_tokens"] += int(row.get("total_tokens") or 0)

    return templates.TemplateResponse(request, "admin_usage.html", {
        "rows": rows,
        "totals": totals,
        "days": max(1, min(days, 90)),
        "csrf_token": csrf_token(request),
    })

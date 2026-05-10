"""Per-user notification preferences (read / partial update / test send).

The UI on the Account page edits a single `UserNotificationPref` row per
user. A missing row is treated by the dispatcher as "all on with no
usage cap" — see `daedalus.notifications.dispatcher._default_pref` —
so this route lazily inserts a row on the first PATCH instead of forcing
a separate "create" step.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import NotificationPrefsOut, NotificationPrefsPatch
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import User, UserNotificationPref
from daedalus.notifications.channels import EmailChannel
from daedalus.notifications.events import NotificationEvent, NotificationKind

router = APIRouter()


_PREF_DEFAULTS: dict[str, object] = {
    "email_task_completed": True,
    "email_task_failed": True,
    "email_task_needs_fixes": True,
    "email_usage_threshold": True,
    "in_app_task_completed": True,
    "in_app_task_failed": True,
    "in_app_task_needs_fixes": True,
    "in_app_usage_threshold": True,
    "usage_threshold_micros": None,
}


async def _load_pref(db: AsyncSession, user_id) -> UserNotificationPref | None:
    res = await db.execute(
        select(UserNotificationPref).where(UserNotificationPref.user_id == user_id)
    )
    return res.scalar_one_or_none()


def _serialize(pref: UserNotificationPref | None) -> dict[str, object]:
    """Return the wire payload — defaults filled in when the row is absent."""
    if pref is None:
        return dict(_PREF_DEFAULTS)
    return {key: getattr(pref, key) for key in _PREF_DEFAULTS}


@router.get("/notification-prefs", response_model=NotificationPrefsOut)
async def get_notification_prefs(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    pref = await _load_pref(db, user.id)
    return _serialize(pref)


@router.patch("/notification-prefs", response_model=NotificationPrefsOut)
async def patch_notification_prefs(
    body: NotificationPrefsPatch,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        # No-op: just return the current state.
        return _serialize(await _load_pref(db, user.id))

    pref = await _load_pref(db, user.id)
    if pref is None:
        # Lazy insert: start from defaults, overlay the patch.
        seed = dict(_PREF_DEFAULTS)
        seed.update(fields)
        pref = UserNotificationPref(user_id=user.id, **seed)
        db.add(pref)
    else:
        for key, value in fields.items():
            setattr(pref, key, value)

    await db.flush()
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="notification_prefs.update",
        target_kind="user",
        target_id=str(user.id),
        payload=fields,
    )
    await db.commit()
    await db.refresh(pref)
    return _serialize(pref)


@router.post("/notification-prefs/test-email", status_code=status.HTTP_202_ACCEPTED)
async def send_test_email(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Sends a sample email to the signed-in user via the standard channel.

    Lets the operator verify SMTP is reachable and that mail isn't being
    filtered before they wait for a real run to finish. The send is
    best-effort: any SMTP failure is logged by `EmailChannel` itself but
    surfaced here as a 502 so the SPA can flash an error.
    """
    import uuid as _uuid

    event = NotificationEvent(
        kind=NotificationKind.task_completed,
        title="Test notification",
        body=(
            "This is a test message from Daedalus. If you got this, your "
            "email notifications are configured correctly."
        ),
        # The test path doesn't bind to a real project — the email
        # channel only references this for the body line.
        project_id=_uuid.UUID(int=0),
        metadata={"test": True},
    )
    channel = EmailChannel()
    try:
        await channel.send(event, user)
    except Exception as exc:  # noqa: BLE001 - surface SMTP failure to the user
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"failed to send test email: {exc}",
        ) from exc

    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="notification_prefs.test_email",
        target_kind="user",
        target_id=str(user.id),
    )
    await db.commit()
    return {"status": "sent", "to": user.email}

from __future__ import annotations

from typing import Any

from config import Config


DJ_DENIED_MESSAGE = "التحكم بالمشغّل متاح لدور DJ أو للإدارة فقط."


def has_dj_access(member: Any, configured_role: str | int | None = None) -> bool:
    """Return whether a guild member is an administrator or has the configured DJ role."""
    permissions = getattr(member, "guild_permissions", None)
    if permissions and (
        getattr(permissions, "administrator", False)
        or getattr(permissions, "manage_guild", False)
    ):
        return True

    role_value = str(configured_role or Config.DJ_ROLE).strip()
    if not role_value:
        return False

    roles = getattr(member, "roles", ())
    if role_value.isdecimal():
        expected_id = int(role_value)
        return any(getattr(role, "id", None) == expected_id for role in roles)

    expected_name = role_value.casefold()
    return any(
        str(getattr(role, "name", "")).casefold() == expected_name
        for role in roles
    )

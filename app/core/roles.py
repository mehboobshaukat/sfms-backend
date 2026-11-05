"""Role names for the admin layer. Stored in users.role (String(20))."""

ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"

# Roles allowed through the /admin router. The role is ALWAYS read from the
# database on each request (never from the JWT), so removing adminship or
# blocking an account takes effect immediately.
ADMIN_ROLES = frozenset({ROLE_ADMIN, ROLE_SUPER_ADMIN})

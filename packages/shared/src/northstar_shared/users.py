"""Seeded-user naming convention shared by the generator, dev tools and tests.

User ids look like ``"a.ops"``: the prefix is the tenant letter (``a`` -> ``broker_a``),
the suffix maps to a role via ``ROLE_SUFFIX``.
"""

ROLE_SUFFIX = {
    "ops": "ops_rep",
    "sales": "carrier_sales",
    "pricing": "pricing",
    "manager": "ops_manager",
    "admin": "broker_admin",
}


def tenant_role_for(user_id: str) -> tuple[str, str]:
    """``"a.ops"`` -> ``("broker_a", "ops_rep")``. ValueError on anything else."""
    prefix, sep, suffix = user_id.partition(".")
    if not sep or len(prefix) != 1 or not prefix.isalpha() or not prefix.islower():
        raise ValueError(f"bad user id {user_id!r}: expected '<tenant letter>.<role suffix>'")
    if suffix not in ROLE_SUFFIX:
        raise ValueError(f"bad user id {user_id!r}: unknown role suffix {suffix!r}")
    return f"broker_{prefix}", ROLE_SUFFIX[suffix]

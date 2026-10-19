import pytest
from northstar_shared.users import ROLE_SUFFIX, tenant_role_for


def test_tenant_role_for():
    assert tenant_role_for("a.ops") == ("broker_a", "ops_rep")
    assert tenant_role_for("c.admin") == ("broker_c", "broker_admin")
    assert set(ROLE_SUFFIX.values()) == {
        "ops_rep", "carrier_sales", "pricing", "ops_manager", "broker_admin"
    }


@pytest.mark.parametrize("bad", ["", "a", "a.", ".ops", "ab.ops", "a.root", "A.ops", "a.ops.x"])
def test_tenant_role_for_rejects_bad_ids(bad):
    with pytest.raises(ValueError):
        tenant_role_for(bad)

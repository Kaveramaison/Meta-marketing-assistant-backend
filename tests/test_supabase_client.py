from core.supabase_client import RestQuery


def test_rest_query_supports_team_feature_filters():
    query = RestQuery(client=None, table_name="workspace_invitations")

    query.ilike("email", "Member@Example.com").gt(
        "expires_at", "2026-06-19T00:00:00+00:00"
    ).in_("role", ["owner", "admin"])

    assert query.params == {
        "email": "ilike.Member@Example.com",
        "expires_at": "gt.2026-06-19T00:00:00+00:00",
        "role": "in.(owner,admin)",
    }

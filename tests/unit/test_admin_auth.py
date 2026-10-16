from mockazure.admin.auth import verify_admin_token


def test_only_exact_token_matches_and_missing_file_fails_closed(tmp_path):
    path = tmp_path / "admin.token"
    assert not verify_admin_token("anything", path)
    path.write_text("A" * 43 + "\n")
    assert verify_admin_token("A" * 43, path)
    assert not verify_admin_token("B" * 43, path)
    assert not verify_admin_token("", path)
    path.write_text("")
    assert not verify_admin_token("", path)

import pytest


def test_secret_verification_is_salted_and_rejects_wrong_input():
    from mockazure.identity.credentials import hash_secret, verify_secret

    first = hash_secret("local-password-123")
    second = hash_secret("local-password-123")
    assert first != second
    assert "local-password" not in first
    assert verify_secret("local-password-123", first)
    assert not verify_secret("wrong-password-123", first)
    assert not verify_secret("local-password-123", "scrypt$malformed")
    assert not verify_secret("X" * 4097, first)


@pytest.mark.parametrize(
    "fields",
    [
        {"upn": "name\x00@contoso.test", "display_name": "Name", "password": "password-123"},
        {"upn": "name@contoso.test", "display_name": "\x00", "password": "password-123"},
        {"upn": "name@contoso.test", "display_name": "Name", "password": "short"},
    ],
)
def test_invalid_user_input_is_rejected(fields):
    from mockazure.identity.types import UserCreate

    with pytest.raises(ValueError):
        UserCreate(**fields)


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example/callback",
        "http://callback.test/return",
        "https://localhost/callback#fragment",
        "https://user@localhost/callback",
    ],
)
def test_application_cannot_register_external_or_ambiguous_redirect(uri):
    from mockazure.identity.types import ApplicationCreate

    with pytest.raises(ValueError):
        ApplicationCreate(display_name="Lab", client_mode="public", redirect_uris=[uri])

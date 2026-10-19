import pytest

from mockazure.console.passwords import hash_password, verify_password


def test_scrypt_passwords_are_salted_and_never_truncated():
    password = "local lab password " + "é" * 20
    encoded = hash_password(password)
    assert encoded != hash_password(password)
    assert "32768:8:3" in encoded
    assert password not in encoded
    assert verify_password(password, encoded)
    assert not verify_password(password + "x", encoded)
    assert not verify_password(password, "broken")
    for bad in ("short", "é" * 513):
        with pytest.raises(ValueError):
            hash_password(bad)

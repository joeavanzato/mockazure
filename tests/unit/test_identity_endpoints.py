import pytest
from pydantic import ValidationError


def test_resource_alias_is_not_a_destination():
    from mockazure.endpoints import EndpointProfile, resolve_resource

    profile = EndpointProfile(
        login="https://login.mockazure.test:28443",
        graph="https://graph.mockazure.test:28443",
        exchange="https://outlook.mockazure.test:28443",
        arm="https://management.mockazure.test:28443",
        browser_login="https://localhost:28443",
    )
    resource = resolve_resource("https://graph.microsoft.com", profile)
    assert resource.service == "graph"
    assert resource.audience == "00000003-0000-0000-c000-000000000000"
    assert resolve_resource(str(profile.graph), profile) == resource
    with pytest.raises(ValueError, match="Unknown resource"):
        resolve_resource("https://attacker.test", profile)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8443",
        "https://user:password@localhost",
        "https://localhost/path",
        "https://localhost?next=evil",
        "https://localhost#fragment",
        "https://login.microsoftonline.com",
        "https://graph.microsoft.com",
        "https://8.8.8.8",
        "https://localhost.evil.com",
    ],
)
def test_service_origins_reject_untrusted_destinations(url):
    from mockazure.endpoints import EndpointProfile

    with pytest.raises(ValidationError):
        EndpointProfile(login=url)


def test_profile_uses_configured_origin_and_issuer():
    from mockazure.config import Settings

    settings = Settings(
        data_dir=".",
        database_url="postgresql://u:p@localhost/test",
        admin_base_url="https://localhost:28443",
    )
    assert str(settings.endpoints.login) == "https://localhost:28443"
    assert settings.endpoints.issuer("11111111-1111-1111-1111-111111111111") == (
        "https://localhost:28443/11111111-1111-1111-1111-111111111111/v2.0"
    )

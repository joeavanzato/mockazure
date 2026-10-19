import pytest

from tools import verify_graph_workflows


def test_callback_uses_shared_private_network_instead_of_container_loopback():
    assert (
        verify_graph_workflows.callback_address(
            {
                "lab_internal": {"IPAddress": "172.28.0.3"},
                "lab_ingress": {"IPAddress": "172.29.0.3"},
            }
        )
        == "172.28.0.3"
    )


@pytest.mark.parametrize(
    "networks",
    [
        {},
        {"lab_ingress": {"IPAddress": "172.29.0.3"}},
        {"lab_internal": {"IPAddress": "127.0.0.1"}},
        {"lab_internal": {"IPAddress": "8.8.8.8"}},
        {"lab_internal": {"IPAddress": "0.0.0.0"}},
        {
            "first_internal": {"IPAddress": "172.28.0.2"},
            "second_internal": {"IPAddress": "172.28.1.2"},
        },
    ],
)
def test_callback_network_must_be_unambiguous_and_private(networks):
    with pytest.raises(ValueError):
        verify_graph_workflows.callback_address(networks)

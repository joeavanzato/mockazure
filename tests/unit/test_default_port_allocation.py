from tools import verify_default


def test_default_ports_checks_every_adjacent_service_port(monkeypatch):
    ports = iter([65500, 64000, 63000])
    monkeypatch.setattr(verify_default, "free_port", lambda: next(ports))
    checked = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            checked.append(address[1])
            if address[1] == 65501:
                raise OSError("Windows excluded port")

    monkeypatch.setattr(verify_default.socket, "socket", lambda *args: Socket())
    assert verify_default.default_ports() == (64000, 63000)
    assert all(port in checked for port in range(64000, 64006))

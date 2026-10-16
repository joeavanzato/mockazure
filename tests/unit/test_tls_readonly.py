import mockazure.tls as tls


def test_runtime_validation_does_not_write_or_lock_credentials(tmp_path, monkeypatch):
    files = tls.ensure_tls(tmp_path, ())
    before = {path: path.read_bytes() for path in vars(files).values()}

    def refuse_write(*args, **kwargs):
        raise AssertionError("Runtime certificate validation must be read-only")

    monkeypatch.setattr(tls, "file_lock", refuse_write)
    assert tls.validate_tls(tmp_path, ()) == files
    assert before == {path: path.read_bytes() for path in before}

from cgn.node.identity import NodeIdentity, verify


def test_create_writes_private_key_locally_and_roundtrips(tmp_path):
    ident = NodeIdentity.create(tmp_path)
    assert (tmp_path / "node_key.pem").exists()
    sig = ident.sign("hello")
    assert verify(ident.public_key_hex, "hello", sig)


def test_load_reuses_same_identity(tmp_path):
    a = NodeIdentity.create(tmp_path)
    b = NodeIdentity.load(tmp_path)
    assert a.public_key_hex == b.public_key_hex


def test_verify_rejects_tampered_message(tmp_path):
    ident = NodeIdentity.create(tmp_path)
    sig = ident.sign("job1|answer")
    assert not verify(ident.public_key_hex, "job1|forged", sig)


def test_verify_rejects_wrong_key(tmp_path):
    a = NodeIdentity.create(tmp_path / "a")
    b = NodeIdentity.create(tmp_path / "b")
    sig = a.sign("m")
    assert not verify(b.public_key_hex, "m", sig)

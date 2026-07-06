"""TR-23: the node generates its keypair locally; the private key never
leaves the machine. Only `public_key_hex` appears in enrollment traffic."""

from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

_KEY_FILE = "node_key.pem"


class NodeIdentity:
    def __init__(self, private_key: ed25519.Ed25519PrivateKey) -> None:
        self._private = private_key

    @classmethod
    def create(cls, key_dir: Path) -> "NodeIdentity":
        key_dir = Path(key_dir)
        key_dir.mkdir(parents=True, exist_ok=True)
        key = ed25519.Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        path = key_dir / _KEY_FILE
        path.write_bytes(pem)
        path.chmod(0o600)
        return cls(key)

    @classmethod
    def load(cls, key_dir: Path) -> "NodeIdentity":
        pem = (Path(key_dir) / _KEY_FILE).read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
        return cls(key)

    @property
    def public_key_hex(self) -> str:
        return self._private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def sign(self, message: str) -> str:
        return self._private.sign(message.encode()).hex()


def verify(public_key_hex: str, message: str, signature_hex: str) -> bool:
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(signature_hex), message.encode())
        return True
    except (InvalidSignature, ValueError):
        return False

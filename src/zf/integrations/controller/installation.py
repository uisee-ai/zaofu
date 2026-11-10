"""Installation-bound enrollment; only ciphertext and a private installation key persist."""

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


def private_read(path: Path, *, limit: int = 65536) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "r") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("installation input must be an owner-only regular file")
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("installation input is too large")
    return value


class Installation:
    def __init__(self, config):
        self.config = config
        self.path = config.state_dir / "installation"

    @contextmanager
    def locked(self):
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or self.path.resolve() != self.path.absolute()):
            raise ValueError("installation directory must be private and contain no symlinks")
        lock = self.path / "state.lock"
        if lock.is_symlink():
            raise ValueError("invalid installation lock")
        with locked_path(self.path / "state", timeout_seconds=15):
            target = self.path / "target.json"
            if target.exists():
                if json.loads(private_read(target)) != self.config.target():
                    raise ValueError("installation target changed; explicit migration is required")
            else:
                if any((self.path / name).exists() for name in ("private.pem", "binding.json")):
                    raise ValueError("installation target is missing")
                self.write("target.json", self.config.target())
            yield

    def write(self, name: str, value: object) -> None:
        destination = self.path / name
        if destination.is_symlink():
            raise ValueError("invalid installation file")
        atomic_write_text(destination, value if isinstance(value, str) else json.dumps(value))
        # atomic_write_text creates 0600 temporary files. Persist the rename as well.
        descriptor = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def key(self, *, create: bool = False):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        path = self.path / "private.pem"
        if not path.exists():
            if not create or (self.path / "binding.json").exists():
                raise ValueError("installation key missing; cannot regenerate an enrolled identity")
            key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            self.write("private.pem", key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
        key = serialization.load_pem_private_key(private_read(path).encode(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or not 2048 <= key.key_size <= 4096:
            raise ValueError("invalid installation key")
        return key

    def public(self) -> dict:
        from cryptography.hazmat.primitives import serialization

        with self.locked():
            key = self.key(create=True).public_key()
            pem = key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            return {"public_key": pem.decode(), "installation_key": hashlib.sha256(der).hexdigest(),
                    "workspace_id": self.config.workspace_id, "management": self.config.management}

    def decode(self, result: dict) -> dict:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        if not isinstance(result, dict) or result.get("protocol") != "zfc.control.v1":
            raise ValueError("unsupported enrollment protocol")
        delivery = result["delivery"]
        if delivery.get("algorithm") != "RSA-OAEP-SHA256+Fernet":
            raise ValueError("unsupported credential envelope")
        wrapped = base64.b64decode(delivery["key"], validate=True)
        symmetric = self.key().decrypt(wrapped, padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        body = json.loads(Fernet(symmetric).decrypt(delivery["body"].encode()))
        if (not isinstance(body, dict) or set(body) != {"issuer", "controller", "workspace_id",
                "deployment_id", "generation", "client_id", "client_secret"}
                or body["controller"] != self.config.endpoint or body["issuer"] != self.config.issuer
                or body["workspace_id"] != self.config.workspace_id
                or body["deployment_id"] != result.get("deployment_id")
                or type(body["generation"]) is not int or body["generation"] < 1):
            raise ValueError("credential target mismatch")
        for field in ("deployment_id", "client_id"):
            if not isinstance(body[field], str) or not re.fullmatch(r"[\w.:/@+-]{1,160}", body[field]):
                raise ValueError("invalid machine identity")
        secret = body["client_secret"]
        if not isinstance(secret, str) or not 1 <= len(secret) <= 4096:
            raise ValueError("invalid machine credential")
        if result.get("client_id", body["client_id"]) != body["client_id"]:
            raise ValueError("credential client mismatch")
        if result.get("generation", body["generation"]) != body["generation"]:
            raise ValueError("credential generation mismatch")
        return body

    def credentials(self) -> dict:
        with self.locked():
            return self.decode(json.loads(private_read(self.path / "binding.json")))

    def enroll(self, code_file: Path) -> dict:
        from cryptography.hazmat.primitives import serialization
        import httpx2

        code = private_read(code_file, limit=256).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", code):
            raise ValueError("invalid enrollment code")
        with self.locked():
            key = self.key().public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            with httpx2.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
                response = client.post(self.config.endpoint + "/enrollment/v1/consume", json={
                    "protocol": "zfc.control.v1", "code": code, "public_key": key})
                response.raise_for_status()
                result = response.json()
            body = self.decode(result)
            binding = self.path / "binding.json"
            if binding.exists():
                previous = self.decode(json.loads(private_read(binding)))
                if any(body[key] != previous[key] for key in ("deployment_id", "generation")):
                    raise ValueError("existing deployment cannot be replaced by enrollment")
            self.write("binding.json", result)
            return self.summary(body)

    def summary(self, body: dict) -> dict:
        return {"workspace_id": body["workspace_id"], "deployment_id": body["deployment_id"],
                "generation": body["generation"], "management": self.config.management, "status": "enrolled",
                "online_verified": False}

    def accept_credentials(self, delivery_file: Path) -> dict:
        from zf.integrations.controller.workspace_transport import WorkspaceTransport

        result = json.loads(private_read(delivery_file))
        with self.locked():
            previous = self.decode(json.loads(private_read(self.path / "binding.json")))
            body = self.decode(result)
            if any(body[key] != previous[key] for key in ("deployment_id", "generation")):
                raise ValueError("credential rotation cannot replace a deployment")
            # A superseded delivery must never roll back the local machine credential.
            with WorkspaceTransport(self.config, body) as transport:
                transport.handshake()
            self.write("binding.json", result)
            return {**self.summary(body), "status": "credentials_updated", "online_verified": True}

    def bootstrap(self) -> dict:
        """Import an authenticated launcher identity; endpoint/workspace come from zf.yaml."""
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from zf.integrations.controller.workspace_transport import WorkspaceTransport

        body = {'issuer': os.environ.get('ZF_CONTROLLER_ISSUER'),
                'controller': os.environ.get('ZF_CONTROLLER_URL'),
                'workspace_id': os.environ.get('ZFC_WORKSPACE_ID'),
                'deployment_id': os.environ.get('ZF_CONTROLLER_DEPLOYMENT_ID'),
                'client_id': os.environ.get('ZF_CONTROLLER_CLIENT_ID'),
                'client_secret': os.environ.get('ZF_CONTROLLER_CLIENT_SECRET')}
        if (body['issuer'] != self.config.issuer or body['controller'] != self.config.endpoint
                or body['workspace_id'] != self.config.workspace_id
                or not all(isinstance(value, str) and value for value in body.values())):
            raise ValueError('trusted launcher identity is incomplete or targets a different installation')
        with self.locked():
            key = self.key(create=True)
            with WorkspaceTransport(self.config, body) as transport:
                hello = transport.request('GET', '/internal/v1/me')
                body['generation'] = hello.get('generation')
                transport.handshake()
            if (self.path / 'binding.json').exists():
                old = self.decode(json.loads(private_read(self.path / 'binding.json')))
                if any(old[name] != body[name] for name in ('deployment_id', 'generation')):
                    if self.config.hosting != 'cloud' or body['generation'] <= old['generation']:
                        raise ValueError('new generation requires an explicit installation migration')
                    self.write('retired-generation-' + str(old['generation']) + '.json',
                               json.loads(private_read(self.path / 'binding.json')))
            symmetric = Fernet.generate_key()
            result = {'protocol': 'zfc.control.v1', 'deployment_id': body['deployment_id'], 'delivery': {
                'algorithm': 'RSA-OAEP-SHA256+Fernet',
                'key': base64.b64encode(key.public_key().encrypt(symmetric, padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))).decode(),
                'body': Fernet(symmetric).encrypt(json.dumps(body).encode()).decode()}}
            self.decode(result)
            self.write('binding.json', result)
        # Provider subprocesses do not inherit a raw Controller machine secret.
        os.environ.pop('ZF_CONTROLLER_CLIENT_SECRET', None)
        return {**self.summary(body), 'online_verified': True}

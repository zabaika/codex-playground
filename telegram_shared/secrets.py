import subprocess
from urllib import parse

from .errors import SecretResolutionError


OP_REFERENCE_PREFIX = "op://"
KEYCHAIN_REFERENCE_PREFIX = "keychain://"
_SECRET_CACHE: dict[str, str] = {}


def resolve_keychain_secret(reference: str, label: str) -> str:
    cached = _SECRET_CACHE.get(reference)
    if cached is not None:
        return cached
    parsed = parse.urlparse(reference)
    service = parse.unquote(parsed.netloc.strip())
    account = parse.unquote(parsed.path.lstrip("/").strip())
    if not service or not account:
        raise SecretResolutionError(
            f"Invalid Keychain reference for {label}. Use keychain://<service>/<account>."
        )
    try:
        completed = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SecretResolutionError(
            f"macOS 'security' CLI is required to resolve {label} from Keychain."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SecretResolutionError(f"Timed out while resolving {label} from Keychain.") from exc
    if completed.returncode != 0:
        raise SecretResolutionError(
            f"Failed to resolve {label} from Keychain. Make sure the generic password exists and the reference is valid."
        )
    value = completed.stdout.strip()
    _SECRET_CACHE[reference] = value
    return value


def resolve_onepassword_secret(reference: str, label: str) -> str:
    cached = _SECRET_CACHE.get(reference)
    if cached is not None:
        return cached
    try:
        completed = subprocess.run(
            ["op", "read", reference],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SecretResolutionError(
            f"1Password CLI 'op' is required to resolve {label}. Install 1Password CLI and sign in first."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SecretResolutionError(f"Timed out while resolving {label} from 1Password.") from exc
    if completed.returncode != 0:
        raise SecretResolutionError(
            f"Failed to resolve {label} from 1Password. Make sure 'op' is signed in and the secret reference is valid."
        )
    value = completed.stdout.strip()
    _SECRET_CACHE[reference] = value
    return value


def resolve_secret_value(raw_value: str, label: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value.startswith(KEYCHAIN_REFERENCE_PREFIX):
        return resolve_keychain_secret(value, label)
    if value.startswith(OP_REFERENCE_PREFIX):
        return resolve_onepassword_secret(value, label)
    return value

import hashlib
import hmac

from agent_core.services.github.security import verify_webhook_signature


def test_valid_webhook_signature() -> None:
    secret = "my_super_secret_key"
    payload = b'{"action": "opened", "issue": {"number": 1}}'

    # Compute valid signature
    computed_hash = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    valid_header = f"sha256={computed_hash}"

    assert verify_webhook_signature(payload, secret, valid_header) is True


def test_tampered_payload_fails() -> None:
    secret = "my_super_secret_key"
    original_payload = b'{"action": "opened"}'
    tampered_payload = b'{"action": "opened", "malicious_injection": true}'

    # Compute signature for original
    computed_hash = hmac.new(secret.encode(), original_payload, hashlib.sha256).hexdigest()
    header = f"sha256={computed_hash}"

    # Verifying tampered payload against original signature MUST fail
    assert verify_webhook_signature(tampered_payload, secret, header) is False


def test_missing_or_malformed_header_fails() -> None:
    secret = "my_secret"
    payload = b"hello"

    assert verify_webhook_signature(payload, secret, None) is False
    assert verify_webhook_signature(payload, secret, "invalid_prefix_hash") is False

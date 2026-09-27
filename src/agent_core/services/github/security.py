import hashlib
import hmac


def verify_webhook_signature(
    payload_body: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    """Verifies that the incoming webhook payload was sent by GitHub.

    Uses HMAC-SHA256 with constant-time comparison to prevent timing attacks.
    """
    if not signature_header:
        return False

    # GitHub signature header format is: "sha256=<hex_digest>"
    if not signature_header.startswith("sha256="):
        return False

    expected_signature = signature_header.removeprefix("sha256=")

    # Calculate HMAC-SHA256 of the raw payload bytes
    computed_hmac = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256,
    )
    computed_signature = computed_hmac.hexdigest()

    # Constant-time comparison to protect against timing attacks
    return hmac.compare_digest(computed_signature, expected_signature)

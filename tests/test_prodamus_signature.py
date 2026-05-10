from app.services.prodamus import create_signature, verify_signature


def test_valid_signature_true():
    secret = "test_secret"
    payload = {"order_id": "abc123", "order_sum": "990", "currency": "rub"}
    sign = create_signature(payload, secret)
    assert verify_signature(payload, secret, sign) is True


def test_invalid_signature_false():
    secret = "test_secret"
    payload = {"order_id": "abc123", "order_sum": "990", "currency": "rub"}
    assert verify_signature(payload, secret, "invalid") is False


def test_changed_amount_false():
    secret = "test_secret"
    payload = {"order_id": "abc123", "order_sum": "990", "currency": "rub"}
    sign = create_signature(payload, secret)
    tampered = dict(payload)
    tampered["order_sum"] = "991"
    assert verify_signature(tampered, secret, sign) is False


def test_empty_signature_false():
    secret = "test_secret"
    payload = {"order_id": "abc123", "order_sum": "990", "currency": "rub"}
    assert verify_signature(payload, secret, "") is False
    assert verify_signature(payload, secret, None) is False


"""Razorpay hosted Payment Link integration."""
import hashlib
import hmac
import os
from urllib.parse import urlparse

import requests
from config import get_setting

API_URL = "https://api.razorpay.com/v1/payment_links/"


class PaymentGatewayError(RuntimeError):
    pass


def configuration():
    default_env = "production" if get_setting("BUSGO_DB_ENGINE", "sqlite").strip().lower() == "mysql" else "development"
    values = {
        "key_id": get_setting("RAZORPAY_KEY_ID", "").strip(),
        "key_secret": get_setting("RAZORPAY_KEY_SECRET", "").strip(),
        "public_url": get_setting("BUSGO_PUBLIC_URL", "").strip().rstrip("/"),
    }
    if not all(values.values()):
        raise PaymentGatewayError(
            "Set RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, and BUSGO_PUBLIC_URL to enable payments."
        )
    parsed_url = urlparse(values["public_url"])
    if parsed_url.scheme not in ("https", "http") or not parsed_url.netloc:
        raise PaymentGatewayError("BUSGO_PUBLIC_URL must be an absolute HTTP or HTTPS URL.")
    if get_setting("BUSGO_ENV", default_env).strip().lower() == "production" and parsed_url.scheme != "https":
        raise PaymentGatewayError("Production payment callbacks require an HTTPS BUSGO_PUBLIC_URL.")
    return values


def create_payment_link(booking_id, amount, passenger, expires_at, method):
    config = configuration()
    payload = {
        "amount": int(round(float(amount) * 100)),
        "currency": "INR",
        "accept_partial": False,
        "expire_by": int(expires_at),
        "reference_id": booking_id,
        "description": f"BusGo bus booking {booking_id}",
        "customer": {
            "name": passenger["name"],
            "email": passenger["email"],
            "contact": "+91" + passenger["phone"],
        },
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "callback_url": f"{config['public_url']}/?payment_return=1",
        "callback_method": "get",
        "notes": {"booking_id": booking_id},
    }
    if method == "UPI":
        payload["upi_link"] = True

    try:
        response = requests.post(
            API_URL,
            json=payload,
            auth=(config["key_id"], config["key_secret"]),
            timeout=(5, 20),
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentGatewayError("Razorpay could not create checkout. Check the keys and try again.") from exc

    link_id = result.get("id")
    checkout_url = result.get("short_url")
    if not link_id or not checkout_url:
        raise PaymentGatewayError("Razorpay returned an incomplete checkout response.")
    return link_id, checkout_url


def verify_callback(params):
    config = configuration()
    payment_id = str(params.get("razorpay_payment_id", ""))
    link_id = str(params.get("razorpay_payment_link_id", ""))
    reference_id = str(params.get("razorpay_payment_link_reference_id", ""))
    link_status = str(params.get("razorpay_payment_link_status", ""))
    signature = str(params.get("razorpay_signature", ""))
    if not all((payment_id, link_id, reference_id, signature)) or link_status.lower() != "paid":
        return None

    signed_payload = f"{link_id}|{reference_id}|{link_status}|{payment_id}".encode()
    expected = hmac.new(config["key_secret"].encode(), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return {"booking_id": reference_id, "link_id": link_id, "payment_id": payment_id}


def verify_paid_payment(params, expected_amount):
    callback = verify_callback(params)
    if not callback:
        return None
    config = configuration()
    try:
        response = requests.get(
            f"https://api.razorpay.com/v1/payments/{callback['payment_id']}",
            auth=(config["key_id"], config["key_secret"]),
            timeout=(5, 20),
        )
        response.raise_for_status()
        payment = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentGatewayError("Razorpay payment could not be verified. Please check My Bookings shortly.") from exc

    if (payment.get("id") != callback["payment_id"] or payment.get("status") != "captured"
            or payment.get("currency") != "INR" or payment.get("amount") != int(round(float(expected_amount) * 100))):
        return None
    method = {"upi": "UPI", "card": "Debit / Credit Card"}.get(payment.get("method"), "Razorpay Checkout")
    callback["method"] = method
    return callback

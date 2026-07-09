"""Webhook server for receiving payment provider notifications.

This server receives real-time payment notifications from supported
providers and applies the same normalized transaction finalization flow.

Setup:
1. Install dependencies: pip install -r requirements.txt
2. For local testing, use ngrok: ngrok http 5000
3. For CryptoBot webhook:
   - Set webhook URL: https://your-domain.com/webhook/cryptobot
4. For DANA QRIS callback:
   - Set required DANA env vars in .env
   - Set callback URL: https://your-domain.com/webhook/dana
   - Register that URL in the DANA dashboard
5. For production, deploy this on a server with HTTPS
"""

from flask import Flask, request, jsonify
import hmac
import hashlib
import json
import re
from datetime import datetime
import requests
from database.db import get_db_session
from config.settings import settings
from services.payments import get_provider
from services.payments.common import complete_transaction, is_manual_qris_expired, parse_provider_metadata
from services.payments.dana_client import verify_callback_signature
from database import PaymentMethod, Transaction, TransactionStatus
from utils import format_price

app = Flask(__name__)

RUPIAH_AMOUNT_PATTERN = re.compile(r"\bRp\s*([0-9][0-9.\s]*(?:,[0-9]{1,2})?)", re.IGNORECASE)


def verify_signature(body: bytes, signature: str) -> bool:
    """
    Verify CryptoBot webhook signature.

    Args:
        body: Raw request body bytes
        signature: Signature from crypto-pay-api-signature header

    Returns:
        True if signature is valid, False otherwise
    """
    # Create secret key from SHA256 hash of API token
    secret_key = hashlib.sha256(settings.CRYPTO_BOT_API_KEY.encode()).digest()

    # Calculate HMAC-SHA256 signature
    calculated_signature = hmac.new(
        secret_key,
        body,
        hashlib.sha256
    ).hexdigest()

    # Compare signatures
    return hmac.compare_digest(calculated_signature, signature)


def process_provider_webhook(payment_method: PaymentMethod, payload: dict):
    """Dispatch a webhook payload to the matching provider."""
    provider = get_provider(payment_method)
    with get_db_session() as session:
        result = provider.process_webhook(session, payload)

    if not result.handled:
        return False

    print("✅ Payment processed via webhook")
    if result.notification:
        print(f"   Transaction #{result.notification.transaction_id}")
        print(f"   Amount: {format_price(result.notification.amount)}")
        print(f"   Method: {result.notification.payment_method}")

    return True


def build_payment_notification_messages(notification):
    """Build Telegram messages for a completed payment notification."""
    user_message = f"""✅ Payment Confirmed!

💳 Method: {notification.payment_method}
💰 Amount: {format_price(notification.amount)}
🔄 Your new wallet balance: {format_price(notification.new_balance)}

Thank you for your payment!"""

    admin_message = f"""💰 New Payment Received

👤 User ID: {notification.user_telegram_id}
💰 Amount: {format_price(notification.amount)}
📝 Transaction ID: #{notification.transaction_id}
🔄 Payment Method: {notification.payment_method}"""

    return user_message, admin_message


def send_telegram_message(chat_id: int, text: str) -> bool:
    """Send a Telegram message from the webhook process."""
    if not settings.BOT_TOKEN:
        print("⚠️ Telegram notification skipped: BOT_TOKEN is not configured")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        if response.ok:
            return True

        print(
            "⚠️ Telegram notification failed "
            f"chat_id={chat_id}, status={response.status_code}, body={response.text}"
        )
    except requests.RequestException as exc:
        print(f"⚠️ Telegram notification failed chat_id={chat_id}: {exc}")

    return False


def send_payment_notifications(notification) -> None:
    """Notify buyer and admin after a webhook-completed payment."""
    if not notification:
        return

    user_message, admin_message = build_payment_notification_messages(notification)

    user_sent = send_telegram_message(notification.user_telegram_id, user_message)
    if user_sent:
        print(f"✅ Buyer notified for transaction #{notification.transaction_id}")

    if settings.ADMIN_TELEGRAM_ID:
        admin_sent = send_telegram_message(settings.ADMIN_TELEGRAM_ID, admin_message)
        if admin_sent:
            print(f"✅ Admin notified for transaction #{notification.transaction_id}")


def parse_payment_deka_amount(payload: dict) -> int | None:
    """Extract integer Rupiah amount from payment.deka.dev notification text."""
    if not isinstance(payload, dict):
        return None

    text = str(payload.get("bigText") or payload.get("text") or "")
    match = RUPIAH_AMOUNT_PATTERN.search(text)
    if not match:
        return None

    amount_text = match.group(1).replace(".", "").replace(" ", "")
    amount_text = amount_text.split(",", 1)[0]
    try:
        amount = int(amount_text)
    except ValueError:
        return None

    return amount if amount > 0 else None


def is_manual_qris_transaction(transaction) -> bool:
    return (
        transaction.payment_method == PaymentMethod.QRIS
        and transaction.provider_name != "dana_qris"
    )


def metadata_int(metadata: dict, key: str) -> int | None:
    try:
        value = metadata.get(key)
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def find_payment_deka_duplicate(session, webhook_id: str | None):
    if not webhook_id:
        return None

    transactions = session.query(Transaction).filter_by(
        payment_method=PaymentMethod.QRIS,
        status=TransactionStatus.COMPLETED,
    ).filter((Transaction.provider_name == None) | (Transaction.provider_name != "dana_qris")).all()

    for transaction in transactions:
        metadata = parse_provider_metadata(transaction.provider_metadata)
        if str(metadata.get("auto_confirm_webhook_id") or "") == str(webhook_id):
            return transaction

    return None


def find_payment_deka_pending_match(session, *, unique_code: int, amount_received: int):
    now = datetime.utcnow()
    expired = False
    transactions = session.query(Transaction).filter_by(
        payment_method=PaymentMethod.QRIS,
        status=TransactionStatus.PENDING,
    ).filter((Transaction.provider_name == None) | (Transaction.provider_name != "dana_qris")).order_by(Transaction.created_at.asc()).all()

    for transaction in transactions:
        if not is_manual_qris_transaction(transaction):
            continue

        if is_manual_qris_expired(transaction, now=now):
            transaction.status = TransactionStatus.EXPIRED
            expired = True
            continue

        metadata = parse_provider_metadata(transaction.provider_metadata)
        transaction_unique_code = metadata_int(metadata, "unique_code")
        if transaction_unique_code != unique_code:
            continue

        payable_amount = metadata_int(metadata, "payable_amount")
        if payable_amount is not None and payable_amount != amount_received:
            continue

        if expired:
            session.commit()
        return transaction

    if expired:
        session.commit()

    return None


@app.route('/webhook/cryptobot', methods=['POST'])
def cryptobot_webhook():
    """
    Webhook endpoint for CryptoBot payment notifications.

    CryptoBot sends POST requests to this endpoint when invoices are paid.
    """
    try:
        # Get signature from header
        signature = request.headers.get('crypto-pay-api-signature')

        if not signature:
            print("❌ No signature in webhook request")
            return jsonify({'error': 'No signature'}), 401

        # Get raw request body
        body = request.get_data()

        # Verify signature
        if not verify_signature(body, signature):
            print("❌ Invalid webhook signature")
            return jsonify({'error': 'Invalid signature'}), 401

        # Parse JSON
        data = request.get_json()

        print(f"📩 CryptoBot Webhook received:")
        print(json.dumps(data, indent=2))

        # Extract update info
        update_type = data.get('update_type')
        request_date = data.get('request_date')
        payload = data.get('payload')

        # Check update type
        if update_type != 'invoice_paid':
            print(f"⚠️ Unknown update type: {update_type}")
            return jsonify({'ok': True}), 200

        handled = process_provider_webhook(PaymentMethod.CRYPTO_WALLET, payload)
        if not handled:
            print("⚠️ No pending transaction matched this CryptoBot webhook")

        return jsonify({'ok': True}), 200

    except Exception as e:
        print(f"❌ Webhook error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/webhook/dana', methods=['POST'])
def dana_webhook():
    try:
        body = request.get_data()
        headers = {k: v for k, v in request.headers.items()}

        if not verify_callback_signature(headers=headers, body_bytes=body):
            print("❌ Invalid DANA webhook signature")
            return jsonify({'error': 'Invalid signature'}), 401

        data = request.get_json(force=True, silent=True) or {}
        print("📩 DANA Webhook received:")
        print(json.dumps(data, indent=2))

        payload = dict(data)
        payload['__headers'] = headers
        payload['__raw'] = body

        handled = process_provider_webhook(PaymentMethod.QRIS, payload)
        if not handled:
            print("⚠️ No pending transaction matched this DANA webhook")

        return jsonify({'responseCode': '2000000', 'responseMessage': 'OK'}), 200

    except Exception as e:
        print(f"❌ DANA webhook error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/webhook/payment-deka', methods=['POST'])
def payment_deka_webhook():
    """Webhook endpoint for payment.deka.dev Android payment notifications."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({
                'ok': False,
                'error': 'invalid_payload',
                'message': 'Expected a JSON object',
            }), 400

        print("📩 payment.deka.dev Webhook received:")
        print(json.dumps(data, indent=2))

        amount_received = parse_payment_deka_amount(data)
        if amount_received is None:
            print("❌ payment.deka.dev webhook amount could not be parsed")
            return jsonify({
                'ok': False,
                'error': 'invalid_amount',
                'message': 'Could not parse Rupiah amount from bigText or text',
            }), 400

        unique_code = amount_received % 1000
        webhook_id = data.get("id")

        with get_db_session() as session:
            duplicate = find_payment_deka_duplicate(session, str(webhook_id) if webhook_id else None)
            if duplicate:
                print(
                    "ℹ️ Duplicate payment.deka.dev webhook acknowledged "
                    f"(webhook_id={webhook_id}, transaction_id={duplicate.id})"
                )
                return jsonify({
                    'ok': True,
                    'duplicate': True,
                    'transaction_id': duplicate.id,
                    'received_amount': amount_received,
                    'unique_code': unique_code,
                }), 200

            transaction = find_payment_deka_pending_match(
                session,
                unique_code=unique_code,
                amount_received=amount_received,
            )
            if not transaction:
                print(
                    "⚠️ No pending manual QRIS transaction matched "
                    f"received_amount={amount_received}, unique_code={unique_code:03d}"
                )
                return jsonify({
                    'ok': False,
                    'error': 'transaction_not_found',
                    'received_amount': amount_received,
                    'unique_code': unique_code,
                }), 404

            notification = complete_transaction(
                session,
                transaction,
                provider_name=transaction.provider_name or "qris",
                provider_metadata={
                    "auto_confirm_source": "payment.deka.dev",
                    "auto_confirm_webhook_id": str(webhook_id) if webhook_id else None,
                    "auto_confirm_received_amount": amount_received,
                    "auto_confirm_unique_code": unique_code,
                    "auto_confirm_payload": data,
                },
            )

            print(
                "✅ Manual QRIS auto-confirmed "
                f"transaction_id={transaction.id}, "
                f"received_amount={amount_received}, "
                f"unique_code={unique_code:03d}"
            )
            if notification:
                print(f"   Credited: {format_price(notification.amount)}")
                print(f"   New balance: {format_price(notification.new_balance)}")
                send_payment_notifications(notification)

            return jsonify({
                'ok': True,
                'transaction_id': transaction.id,
                'received_amount': amount_received,
                'unique_code': unique_code,
                'credited_amount': int(transaction.confirmed_amount or transaction.amount),
            }), 200

    except Exception as e:
        print(f"❌ payment.deka.dev webhook error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'service': 'Payment Webhook Receiver',
        'timestamp': datetime.utcnow().isoformat()
    }), 200


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with setup instructions."""
    return """
    <h1>Payment Webhook Receiver</h1>
    <p>This server is running and ready to receive payment provider notifications.</p>

    <h2>Setup Instructions:</h2>
    <ol>
        <li>Go to <a href="https://t.me/CryptoBot">@CryptoBot</a> in Telegram</li>
        <li>Navigate to: Crypto Pay → My Apps → Select your app</li>
        <li>Tap "Webhooks..." and then "Enable Webhooks"</li>
        <li>Enter your webhook URL: <code>https://your-domain.com/webhook/cryptobot</code></li>
        <li>Save and start receiving real-time payment notifications!</li>
    </ol>

    <h2>Endpoints:</h2>
    <ul>
        <li><code>POST /webhook/cryptobot</code> - CryptoBot webhook endpoint</li>
        <li><code>POST /webhook/dana</code> - DANA QRIS callback endpoint</li>
        <li><code>POST /webhook/payment-deka</code> - Manual QRIS auto-confirm endpoint</li>
        <li><code>GET /health</code> - Health check</li>
    </ul>

    <p><strong>Note:</strong> For local testing, use ngrok to create a public HTTPS URL.</p>
    """, 200


if __name__ == '__main__':
    print("=" * 60)
    print("Payment Webhook Server")
    print("=" * 60)
    print(f"Server starting on http://0.0.0.0:5000")
    print(f"Webhook endpoint: /webhook/cryptobot")
    print(f"Webhook endpoint: /webhook/dana")
    print(f"Webhook endpoint: /webhook/payment-deka")
    print()
    print("For local testing with ngrok:")
    print("  1. Run: ngrok http 5000")
    print("  2. Copy the HTTPS URL (e.g., https://abc123.ngrok.io)")
    print("  3. Set webhook in CryptoBot to: https://abc123.ngrok.io/webhook/cryptobot")
    print()
    print("Waiting for webhooks...")
    print("=" * 60)

    # Run Flask server
    app.run(host='0.0.0.0', port=5000, debug=False)

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
from datetime import datetime
from database.db import get_db_session
from config.settings import settings
from services.payments import get_provider
from services.payments.dana_client import verify_callback_signature
from database import PaymentMethod

app = Flask(__name__)


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
        print(f"   Amount: ${result.notification.amount:.2f}")
        print(f"   Method: {result.notification.payment_method}")

    return True


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

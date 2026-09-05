/* Shared RazorPay checkout helper.
 *
 * The backend creates the order (POST /api/billing/checkout → order payload)
 * and this helper opens the RazorPay modal, then verifies the payment via
 * POST /api/billing/verify before reporting success.
 */
window.CloudGPTCheckout = (function () {
  'use strict';

  let scriptPromise = null;

  function loadRazorpayScript() {
    if (window.Razorpay) return Promise.resolve();
    if (!scriptPromise) {
      scriptPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://checkout.razorpay.com/v1/checkout.js';
        script.onload = () => resolve();
        script.onerror = () => {
          scriptPromise = null;
          reject(new Error('Could not load the RazorPay checkout script. Check your network connection.'));
        };
        document.head.appendChild(script);
      });
    }
    return scriptPromise;
  }

  async function openRazorpayCheckout(orderPayload, onSuccess) {
    await loadRazorpayScript();

    const options = {
      key: orderPayload.key_id,
      amount: orderPayload.amount,
      currency: orderPayload.currency || 'INR',
      name: orderPayload.name || 'CloudGPT',
      description: orderPayload.description || '',
      order_id: orderPayload.order_id,
      prefill: orderPayload.prefill || {},
      notes: orderPayload.notes || {},
      theme: { color: orderPayload.theme_color || '#09090b' },
      handler: async function (response) {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
        const verifyResponse = await fetch('/api/billing/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({
            order_id: response.razorpay_order_id,
            payment_id: response.razorpay_payment_id,
            signature: response.razorpay_signature,
            plan: orderPayload._plan,
            interval: orderPayload._interval || 'month',
          }),
        });
        const data = await verifyResponse.json().catch(() => ({}));
        if (!verifyResponse.ok) {
          throw new Error(data.detail || 'Payment verification failed');
        }
        if (typeof onSuccess === 'function') onSuccess(data);
      },
      modal: {
        ondismiss: function () {
          if (typeof options._onDismiss === 'function') options._onDismiss();
        },
      },
    };

    const rzp = new window.Razorpay(options);
    return rzp;
  }

  /* Entry point used by billing.js / pricing.js.
   * result: the /api/billing/checkout response.
   * Returns true when the provider flow was handled here. */
  async function startCheckout(result, opts) {
    const options = opts || {};
    if (result && result.provider === 'razorpay') {
      result._plan = options.plan;
      result._interval = options.interval || 'month';
      result._onDismiss = options.onDismiss;
      const rzp = await openRazorpayCheckout(result, options.onSuccess);
      rzp.open();
      return true;
    }
    return false; // Stripe (or other) flow: caller keeps handling result.url
  }

  return { startCheckout, loadRazorpayScript };
})();

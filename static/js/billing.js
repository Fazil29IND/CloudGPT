(() => {
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const paymentProvider = document.querySelector('meta[name="payment-provider"]')?.content || 'stripe';
  const notificationBox = document.getElementById('billing-notification');
  const headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': token };

  function showNotification(message, type = 'error') {
    if (!notificationBox) return;
    notificationBox.className = type;
    notificationBox.textContent = message;
    notificationBox.style.display = 'block';
    notificationBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function hideNotification() {
    if (!notificationBox) return;
    notificationBox.style.display = 'none';
  }

  async function post(path, body = {}) {
    const response = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Request failed');
    return data;
  }

  // Setup checkout button handlers
  document.querySelectorAll('.btn-checkout').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      hideNotification();
      const plan = btn.getAttribute('data-plan') || 'pro';
      const originalText = btn.textContent;
      btn.textContent = 'Opening secure checkout…';
      btn.disabled = true;

      try {
        const result = await post('/api/billing/checkout', { plan, interval: 'month' });
        if (result && result.url) {
          window.location.assign(result.url);
        } else {
          showNotification('Checkout session initiated.', 'success');
          btn.textContent = originalText;
          btn.disabled = false;
        }
      } catch (err) {
        showNotification(err.message || 'Unable to initiate checkout. Please ensure payment keys are configured or check local settings.', 'error');
        btn.textContent = originalText;
        btn.disabled = false;
      }
    });
  });

  // Setup customer portal button handlers (Stripe only)
  document.querySelectorAll('.btn-portal').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      hideNotification();
      const originalText = btn.textContent;
      btn.textContent = 'Opening portal…';
      btn.disabled = true;

      try {
        const result = await post('/api/billing/portal', { return_url: '/billing' });
        if (result.url) {
          window.location.assign(result.url);
        } else {
          showNotification('Customer portal requested.', 'success');
          btn.textContent = originalText;
          btn.disabled = false;
        }
      } catch (err) {
        showNotification(err.message || 'Unable to load customer portal. Please check your active subscription or payment configuration.', 'error');
        btn.textContent = originalText;
        btn.disabled = false;
      }
    });
  });
})();


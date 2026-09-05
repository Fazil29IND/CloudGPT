(() => {
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': token };

  let billingPeriod = 'month';

  async function post(path, body = {}) {
    const response = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) {
        window.location.assign('/?auth_required=true');
        return {};
      }
      throw new Error(data.detail || 'Request failed');
    }
    return data;
  }

  // Monthly / annual billing toggle: swap the displayed amounts and the
  // interval passed to checkout. Annual = monthly price × 10 (2 months free).
  document.querySelectorAll('.period-option').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.dataset.period === billingPeriod) return;
      billingPeriod = button.dataset.period;
      document.querySelectorAll('.period-option').forEach((other) => {
        other.classList.toggle('active', other === button);
      });
      document.querySelectorAll('.price .amount').forEach((amountEl) => {
        const monthly = Number(amountEl.dataset.monthly || 0);
        const annual = Number(amountEl.dataset.annual || monthly * 10);
        const value = billingPeriod === 'year' ? annual : monthly;
        amountEl.textContent = Number(value).toLocaleString('en-IN');
        const card = amountEl.closest('.pricing-card');
        const periodEl = card?.querySelector('.price .period');
        if (periodEl) {
          periodEl.textContent = billingPeriod === 'year' ? '/mo billed annually' : '/month';
        }
      });
    });
  });

  // Handle upgrade button clicks
  document.querySelectorAll('.upgrade-button').forEach(button => {
    button.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      const plan = btn.dataset.plan;
      if (!plan) return;
      const interval = billingPeriod === 'year' ? 'year' : 'month';

      const originalText = btn.textContent;
      try {
        btn.textContent = 'Processing...';
        btn.disabled = true;

        const result = await post('/api/billing/checkout', { plan, interval });
        const handled = await window.CloudGPTCheckout.startCheckout(result, {
          plan,
          interval,
          onSuccess: () => {
            window.location.assign('/billing?checkout=success');
          },
          onDismiss: () => {
            btn.textContent = originalText;
            btn.disabled = false;
          },
        });
        if (!handled && result.url) {
          window.location.assign(result.url);
        } else if (!handled) {
          btn.textContent = originalText;
          btn.disabled = false;
        }
      } catch (error) {
        btn.textContent = originalText;
        btn.disabled = false;
        alert('Unable to process upgrade: ' + (error.message || 'Please try again later.'));
        console.error(error);
      }
    });
  });

  // Smooth accordion toggle for FAQ items
  document.querySelectorAll('.faq-question').forEach(button => {
    button.addEventListener('click', () => {
      const item = button.closest('.faq-item');
      if (!item) return;
      const isExpanded = item.classList.contains('active');

      // Close all other items for a clean accordion experience
      document.querySelectorAll('.faq-item').forEach(other => {
        if (other !== item) {
          other.classList.remove('active');
          const otherBtn = other.querySelector('.faq-question');
          if (otherBtn) otherBtn.setAttribute('aria-expanded', 'false');
        }
      });

      if (isExpanded) {
        item.classList.remove('active');
        button.setAttribute('aria-expanded', 'false');
      } else {
        item.classList.add('active');
        button.setAttribute('aria-expanded', 'true');
      }
    });
  });

  // Add entrance animations
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.style.opacity = '1';
          entry.target.style.transform = 'translateY(0)';
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08 });

    document.querySelectorAll('.pricing-card, .trust-item').forEach(card => {
      card.style.opacity = '0';
      card.style.transform = 'translateY(16px)';
      card.style.transition = 'opacity 0.5s cubic-bezier(0.16, 1, 0.3, 1), transform 0.5s cubic-bezier(0.16, 1, 0.3, 1)';
      observer.observe(card);
    });
  }
})();

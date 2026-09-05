import re
from jinja2 import Environment, FileSystemLoader


def get_template():
    env = Environment(loader=FileSystemLoader("templates"))
    return env.get_template("chat.html")


def render_with_tier(allowed_thinking_levels, allowed_models, default_thinking):
    template = get_template()
    return template.render(
        allowed_thinking_levels=allowed_thinking_levels,
        allowed_models=allowed_models,
        default_thinking=default_thinking,
        user_tier="Free",
        csrf_token="test",
        sessions=[],
        current_session_id=None,
        token_usage={},
        token_limits={},
        limit_day=None,
        limit_month=None,
        tokens_day=0,
        tokens_month=0,
        user_name="Test User",
        is_unlimited=False,
    )


def test_free_tier_rendering():
    html = render_with_tier(("Low", "Medium", "High"), ["Lite", "Core"], "Medium")
    assert 'data-model="Lite"' in html
    assert 'data-model="Core"' in html
    assert 'data-model="Apex"' in html
    assert 'data-thinking="Low"' in html
    assert 'data-thinking="Medium"' in html
    assert 'data-thinking="High"' in html
    assert 'data-thinking="Max"' in html

    # Core and Lite allowed, Apex disabled for Free
    assert re.search(r'data-model="Core"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-model="Core"', html)
    assert re.search(r'data-model="Lite"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-model="Lite"', html)
    assert re.search(r'data-model="Apex"[^>]*data-tier-allowed="false"', html) or re.search(r'data-tier-allowed="false"[^>]*data-model="Apex"', html)

    # Max thinking disabled for Free
    assert re.search(r'data-thinking="Max"[^>]*data-tier-allowed="false"', html) or re.search(r'data-tier-allowed="false"[^>]*data-thinking="Max"', html)


def test_pro_tier_rendering():
    html = render_with_tier(("Low", "Medium", "High", "Max"), ["Apex", "Core", "Lite"], "High")
    # Apex, Core, and Lite allowed for Pro
    assert re.search(r'data-model="Apex"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-model="Apex"', html)
    assert re.search(r'data-model="Core"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-model="Core"', html)
    assert re.search(r'data-model="Lite"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-model="Lite"', html)

    # All thinking levels defined for tier
    assert re.search(r'data-thinking="Max"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-thinking="Max"', html)


def test_max_tier_rendering():
    html = render_with_tier(("Low", "Medium", "High", "Max"), ["Apex", "Core", "Lite"], "High")
    # All models allowed
    assert re.search(r'data-model="Apex"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-model="Apex"', html)
    assert re.search(r'data-model="Core"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-model="Core"', html)
    assert re.search(r'data-model="Lite"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-model="Lite"', html)

    # All thinking levels allowed
    assert re.search(r'data-thinking="High"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-thinking="High"', html)
    assert re.search(r'data-thinking="Max"[^>]*data-tier-allowed="true"', html) or re.search(r'data-tier-allowed="true"[^>]*data-thinking="Max"', html)


def test_user_profile_avatar_and_name():
    template = get_template()

    # Test with email prefix containing numbers
    html = template.render(
        allowed_thinking_levels=["Low"],
        allowed_models=["Lite"],
        default_thinking="Low",
        user_tier="Pro",
        csrf_token="test",
        user_name=None,
        user_email="fazilprojects9@gmail.com",
    )
    assert '<div class="avatar">F</div>' in html
    assert '<div class="name">fazilprojects9</div>' in html

    # Test with explicit name
    html_with_name = template.render(
        allowed_thinking_levels=["Low"],
        allowed_models=["Lite"],
        default_thinking="Low",
        user_tier="Pro",
        csrf_token="test",
        user_name="Fazil S",
        user_email="fazilprojects9@gmail.com",
    )
    assert '<div class="avatar">F</div>' in html_with_name
    assert '<div class="name">Fazil S</div>' in html_with_name


def test_chatgpt_greeting_rendering():
    template = get_template()

    # 1. With user_name (full username displayed)
    html = template.render(
        allowed_thinking_levels=["Low"],
        allowed_models=["Lite"],
        default_thinking="Low",
        user_tier="Pro",
        csrf_token="test",
        user_name="Fazil S",
        user_email="fazilprojects9@gmail.com",
    )
    assert 'id="chat-empty-state"' in html
    assert 'class="chatgpt-greeting-title"' in html
    assert 'Hello, Fazil S' in html
    assert 'What can I help with today?' in html
    # Ensure prompt pills are completely removed
    assert 'chatgpt-prompt-pills' not in html
    assert 'chatgpt-pill' not in html
    # Ensure legacy badge and grid are removed
    assert 'empty-state-badge' not in html
    assert 'starter-cards-grid' not in html

    # 2. With email only (username falls back to email prefix)
    html_email = template.render(
        allowed_thinking_levels=["Low"],
        allowed_models=["Lite"],
        default_thinking="Low",
        user_tier="Pro",
        csrf_token="test",
        user_name=None,
        user_email="fazilprojects9@gmail.com",
    )
    assert 'Hello, fazilprojects9' in html_email
    assert 'What can I help with today?' in html_email
    assert 'chatgpt-prompt-pills' not in html_email
    assert 'chatgpt-pill' not in html_email

    # 3. Without user_name or email
    html_anon = template.render(
        allowed_thinking_levels=["Low"],
        allowed_models=["Lite"],
        default_thinking="Low",
        user_tier="Lite",
        csrf_token="test",
        user_name=None,
        user_email=None,
    )
    assert 'What can I help with today?' in html_anon
    assert 'Ask anything about cloud architecture, pricing, troubleshooting, and security.' in html_anon
    assert 'chatgpt-prompt-pills' not in html_anon
    assert 'chatgpt-pill' not in html_anon


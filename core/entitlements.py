"""Server-owned plan and entitlement policy.

Public clients may request a UI mode, but this module is the only authority for
model selection and feature access. Database subscription state is folded into
the public `lite`/`pro` plans; `max` is strictly an internal override.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import Settings, get_settings


PUBLIC_PLAN_KEYS = {"lite", "pro", "max"}
ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing", "past_due"}

# Thinking levels permitted per plan:
# Free / Lite: Low, Medium, High (Max disabled on Lite)
# Pro / Core: Low, Medium, High (Max reasoning is an Apex differentiator)
# Max / Apex / Developer: Low, Medium, High, Max
THINKING_LITE = ("Low", "Medium", "High")
THINKING_CORE = ("Low", "Medium", "High")
THINKING_APEX = ("Low", "Medium", "High", "Max")

# Model-specific thinking limits per tier:
TIER_MODEL_ALLOWED_THINKING: dict[str, dict[str, tuple[str, ...]]] = {
    "Free": {
        "Lite": ("Low", "Medium", "High"),
        "Core": ("Low", "Medium"),
        "Apex": ("Low",),
    },
    "Pro": {
        "Lite": ("Low", "Medium", "High"),
        "Core": ("Low", "Medium", "High"),
        "Apex": ("Low", "Medium"),
    },
    "Max": {
        "Lite": ("Low", "Medium", "High", "Max"),
        "Core": ("Low", "Medium", "High", "Max"),
        "Apex": ("Low", "Medium", "High", "Max"),
    },
    "Developer": {
        "Lite": ("Low", "Medium", "High", "Max"),
        "Core": ("Low", "Medium", "High", "Max"),
        "Apex": ("Low", "Medium", "High", "Max"),
    },
}


def get_allowed_thinking_for_model(tier: str | None, model: str | None) -> tuple[str, ...]:
    """Return the allowed thinking levels for a specific tier and selected model."""
    tier_norm = (tier or "Free").strip().capitalize()
    if tier_norm in ("Developer", "Admin"):
        tier_norm = "Developer"
    elif tier_norm in ("Max", "Apex"):
        tier_norm = "Max"
    elif tier_norm in ("Pro", "Core"):
        tier_norm = "Pro"
    elif tier_norm in ("Lite", "Free"):
        tier_norm = "Free"
    else:
        tier_norm = "Free"

    model_norm = (model or "Lite").strip().capitalize()
    if model_norm in ("Lite", "Free"):
        model_key = "Lite"
    elif model_norm in ("Core", "Pro"):
        model_key = "Core"
    else:
        model_key = "Apex"

    tier_map = TIER_MODEL_ALLOWED_THINKING.get(tier_norm, TIER_MODEL_ALLOWED_THINKING["Free"])
    return tier_map.get(model_key, ("Low", "Medium", "High"))


def normalize_plan(value: str | None) -> str:
    """Normalize plan names and aliases (including Apex, Core, Lite)."""
    value = (value or "lite").strip().lower()
    if value in {"max", "developer", "admin", "apex"}:
        return "max"
    if value in {"pro", "core"}:
        return "pro"
    return "lite"


@dataclass(frozen=True)
class Entitlements:
    plan_key: str
    model_tier: str
    tokens_day: int | None
    tokens_month: int | None
    tokens_5h: int | None
    tokens_week: int | None
    web_search: bool
    rag: bool
    cloud_api: bool
    max_file_bytes: int
    max_files: int
    allowed_models: list[str] = field(default_factory=lambda: ["Lite"])
    allowed_thinking_levels: tuple[str, ...] = THINKING_LITE
    max_image_bytes: int = 5_242_880
    max_audio_bytes: int = 10_485_760
    tts_chars_per_day: int = 10_000
    artifact_storage_bytes: int = 10_485_760
    has_user_memory: bool = False

    @property
    def unlimited(self) -> bool:
        return self.tokens_day is None and self.tokens_month is None


def resolve_entitlements(
    stored_tier: str | None,
    subscription: dict | None = None,
    *,
    user_email: str | None = None,
    settings: Settings | None = None,
) -> Entitlements:
    """Resolve plan access from trusted user/subscription records only."""
    settings = settings or get_settings()
    is_developer = False
    if user_email and (user_email.strip().lower() in settings.developer_email_set or user_email.strip().lower() == settings.admin_email.lower()):
        is_developer = True
    if (stored_tier or "").strip().lower() in ("developer", "admin"):
        is_developer = True

    if is_developer:
        return Entitlements(
            plan_key="Developer",
            model_tier="Max",
            tokens_day=None,
            tokens_month=None,
            tokens_5h=None,
            tokens_week=None,
            web_search=True,
            rag=True,
            cloud_api=settings.enable_cloud_api_tools,
            max_file_bytes=20_971_520,
            max_files=20,
            allowed_models=["Apex", "Core", "Lite"],
            allowed_thinking_levels=THINKING_APEX,
            max_image_bytes=52_428_800,
            max_audio_bytes=104_857_600,
            tts_chars_per_day=1_000_000,
            artifact_storage_bytes=10_737_418_240,
            has_user_memory=True,
        )

    tier = normalize_plan(stored_tier)
    if subscription and subscription.get("status") in ACTIVE_SUBSCRIPTION_STATUSES:
        tier = normalize_plan(subscription.get("plan_key"))

    if tier == "max":
        return Entitlements(
            plan_key="max",
            model_tier="Max",
            tokens_day=settings.max_tokens_day,
            tokens_month=settings.max_tokens_month,
            tokens_5h=getattr(settings, "max_tokens_5h", 500_000),
            tokens_week=getattr(settings, "max_tokens_week", 15_000_000),
            web_search=True,
            rag=True,
            cloud_api=settings.enable_cloud_api_tools,
            max_file_bytes=10_485_760,
            max_files=10,
            allowed_models=["Apex", "Core", "Lite"],
            allowed_thinking_levels=THINKING_APEX,
            max_image_bytes=20_971_520,
            max_audio_bytes=52_428_800,
            tts_chars_per_day=200_000,
            artifact_storage_bytes=1_073_741_824,
            has_user_memory=True,
        )
    if tier == "pro":
        return Entitlements(
            plan_key="pro",
            model_tier="Pro",
            tokens_day=settings.pro_tokens_day,
            tokens_month=settings.pro_tokens_month,
            tokens_5h=settings.pro_tokens_5h,
            tokens_week=settings.pro_tokens_week,
            web_search=True,
            rag=True,
            cloud_api=settings.enable_cloud_api_tools,
            max_file_bytes=5_242_880,
            max_files=5,
            allowed_models=["Core", "Lite"],
            allowed_thinking_levels=THINKING_CORE,
            max_image_bytes=10_485_760,
            max_audio_bytes=26_214_400,
            tts_chars_per_day=50_000,
            artifact_storage_bytes=524_288_000,
            has_user_memory=True,
        )
    return Entitlements(
        plan_key="lite",
        model_tier="Free",
        tokens_day=settings.lite_tokens_day,
        tokens_month=settings.lite_tokens_month,
        tokens_5h=settings.lite_tokens_5h,
        tokens_week=settings.lite_tokens_week,
        web_search=True,
        rag=True,
        cloud_api=False,
        max_file_bytes=2_097_152,
        max_files=2,
        allowed_models=["Lite"],
        allowed_thinking_levels=THINKING_LITE,
        max_image_bytes=5_242_880,
        max_audio_bytes=10_485_760,
        tts_chars_per_day=10_000,
        artifact_storage_bytes=104_857_600,
        has_user_memory=False,
    )

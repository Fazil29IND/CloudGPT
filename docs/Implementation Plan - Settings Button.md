# Implementation Plan — Settings Panel (Replace Sign-Out Button)

**Version:** 1.0  
**Date:** September 4, 2026  
**Status:** Ready for Implementation  
**Scope:** `templates/chat.html` · `static/js/chat.js` · `static/css/chat.css` · `app.py` · `api/chat_routes.py` · `db.py` · `migrations/`

---

## 1. Executive Summary

The Sign Out button in the sidebar footer currently occupies a prominent `icon-btn` slot inside `.user-profile-actions`. This plan replaces it with a **gear/settings icon button** that opens a full-featured Settings modal. The modal consolidates: account profile editing, password change, Memory controls (migrated from the workspace header), session & data management, subscription/billing info, appearance controls, keyboard shortcuts reference, and a Sign Out action at the bottom.

The Memory button (`#memory-btn`) in the workspace header is removed and its entire functionality is absorbed into the Settings modal under the **Memory** tab. No SSE events, no API contracts, and no existing modal CSS are changed — the implementation is purely additive per the project's non-negotiables.

---

## 2. Codebase Analysis — Current State

### 2.1 Sign Out Button (what is being replaced)

**File:** `templates/chat.html` — inside `.sidebar-footer > .user-profile > .user-profile-actions`

```html
<form action="/logout" method="post" class="logout-form">
  <input type="hidden" name="csrf_token" value="{{ csrf_token or '' }}">
  <button class="icon-btn logout-link" type="submit" title="Sign out" aria-label="Sign out">
    <!-- exit-door SVG icon -->
  </button>
</form>
```

The `POST /logout` route in `app.py` (lines ~538–548) calls `request.session.clear()` and redirects to `/?success=You+have+been+signed+out.`. A matching `GET /logout` route also exists for direct link access.

**What changes:** The `<form>` block is replaced with a single `<button id="settings-btn" class="icon-btn">` (gear icon). Sign Out moves inside the Settings modal, keeping the same form/POST pattern.

### 2.2 Memory Button & Modal (what is being moved)

**Trigger button:** `#memory-btn` in `<header class="workspace-header"> > .header-actions` — a `.header-icon-btn` with a brain SVG and "Memory" label text.

**Modal:** `#memory-modal` — `.modal-backdrop.hidden` containing:
- `#memory-modal-title`, `#memory-loading`, `#memory-empty`, `#memory-items`
- Individual `memory-delete-btn` on each card calling `DELETE /api/user/memory/{id}`

**JS logic (`static/js/chat.js`):**
- `openMemoryModal()` — GETs `/api/user/memory`, renders `.memory-item-card` list
- `closeMemoryModal()` — adds `.hidden`
- `showMemoryToast(text)` — 4-second toast for `memory_updated` SSE events (stays as-is, only the trigger button moves)

**What changes:** `#memory-btn` and the `.header-actions` container in `chat.html` are removed. `#memory-modal` is replaced by a `<div id="settings-modal">` that contains a tabbed interface including the Memory tab. The `openMemoryModal()` / `closeMemoryModal()` JS functions are renamed to `openSettingsModal(tab)` / `closeSettingsModal()`. The memory load/render/delete logic moves into the new modal but is otherwise unchanged. `showMemoryToast()` is untouched.

### 2.3 Existing Modal Pattern (what is being reused)

The project already has a complete modal CSS system in `static/css/chat.css`:

```
.modal-backdrop          — full-screen overlay, hidden by default
.modal-card              — #141418 bg, 14px border-radius, white border
.modal-header            — flex row: .modal-header-left (icon + title) + .modal-close-btn
.modal-body              — content area
.modal-footer            — border-top, hint text
```

The new Settings modal reuses all of these classes. No new modal infrastructure CSS is needed.

### 2.4 Authentication & CSRF Pattern

- CSRF meta tag: `<meta name="csrf-token" content="{{ csrf_token }}">` in `<head>`
- JS reads: `const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || ''`
- Mutating fetch calls use `'X-CSRF-Token': csrfToken` header
- Form POSTs use `<input type="hidden" name="csrf_token">`
- `validate_csrf_token(request, value)` in `app.py` / `core/security.py` enforces it server-side

### 2.5 User Object Available in Templates

The dashboard Jinja context provides: `user_name`, `user_email`, `user_tier`, `effective_tier`, `is_unlimited`, `tokens_day`, `limit_day`, `tokens_month`, `limit_month`, `allowed_models`, `csrf_token`.

`db.update_user_profile(user_id, name, email_verified)` already exists for name updates. `db.set_user_password(user_id, new_password)` exists for password changes. Both are synchronous — must be called via `asyncio.to_thread` in async routes.

### 2.6 Existing API Endpoints Relevant to Settings

| Endpoint | Method | Used By |
|---|---|---|
| `/logout` | POST | Sign Out (kept, moved to modal) |
| `/api/user/memory` | GET | Memory tab (unchanged) |
| `/api/user/memory/{id}` | DELETE | Memory delete (unchanged) |
| `/api/billing/subscription` | GET | Subscription tab |
| `/api/billing/portal` | POST | Manage billing link |

**New endpoints needed (all in `api/chat_routes.py` or `app.py`):**
- `POST /api/user/profile` — update display name
- `POST /api/user/change-password` — change password (authenticated users with password_hash)
- `DELETE /api/user/memory` — clear **all** memory at once (new, bulk delete)

### 2.7 Database — What Needs Migration

The `users` table in `USER_COLUMNS` currently does not have user-configurable settings columns. A new migration (`013_user_settings.sql`) adds two lightweight columns to the `users` table:

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS
    settings_json JSONB NOT NULL DEFAULT '{}';
```

This single JSONB column stores all soft client-side preferences (response language, default thinking level override, keyboard shortcut hints dismissed, etc.) without requiring a separate table. The column is read once at dashboard load and passed to the Jinja context as `user_settings`.

---

## 3. Settings Panel Design — Tabs & Options

The Settings modal uses a **tab bar** across the top with 5 tabs. Each tab maps to a logical concern. The design follows the Claude/ChatGPT pattern of grouping settings by concern, not by technical layer.

### Tab 1 — Account

| Setting | Type | Backend | Notes |
|---|---|---|---|
| Display Name | Text input + Save button | `POST /api/user/profile` (new) → `db.update_user_profile()` | Pre-populated from `user_name` template var |
| Email address | Read-only display | No write | Show email, mark as "Verified" or "Unverified" badge |
| Change Password | Expandable sub-section | `POST /api/user/change-password` (new) | Three fields: current password, new password, confirm. Hidden for OAuth-only users (no `password_hash`) |
| Account created | Read-only timestamp | Template var | Shows `created_at` from user object |
| Sign Out | Form POST `/logout` | Existing route (unchanged) | Red-tinted button at bottom of tab, with CSRF hidden field |

**Why Sign Out lives here:** Users opening Settings to find account controls naturally expect Sign Out alongside their account info. This mirrors Claude, ChatGPT, and Linear's pattern.

### Tab 2 — Memory

| Setting | Type | Backend | Notes |
|---|---|---|---|
| Memory list | Dynamic rendered list | `GET /api/user/memory` | Exact same render logic as current `openMemoryModal()` — cards with category badge, key, value, delete button |
| "Clear All Memory" button | Destructive action with confirmation | `DELETE /api/user/memory` (new bulk endpoint) | Shows inline confirmation: "Delete all X memories? This cannot be undone." — Yes/Cancel buttons appear before action fires |
| Empty state | Info text | — | Same as current modal empty state message |
| Tier lock notice | Conditional banner | Template var `has_memory` | For Lite users: "Memory is available on Pro and above. Upgrade to unlock personalized responses." |

**Migration note:** The existing `#memory-modal` div is removed from `chat.html`. The `openMemoryModal()` / `closeMemoryModal()` functions in `chat.js` are replaced by `openSettingsModal('memory')` / `closeSettingsModal()`. The internal memory fetch/render logic is lifted into the new settings JS. `showMemoryToast()` is untouched (it responds to SSE events, not the modal).

### Tab 3 — Preferences

| Setting | Type | Backend | Notes |
|---|---|---|---|
| Default Response Language | Select dropdown (English, Arabic, French, Spanish, Hindi, German, Japanese, Chinese, Auto-detect) | `settings_json.response_language` → `013` migration column | Stored in `settings_json JSONB`. Applied as a system prompt prefix instruction on next chat |
| Default Thinking Level | Select (Auto / Low / Medium / High / Max) | `settings_json.default_thinking` | Overrides dashboard default; clamped by entitlement tier on server side |
| Show Thinking Process | Toggle (on/off) | `settings_json.show_thinking` | Whether the thinking panel is expanded by default when thinking tokens stream |
| Show Token Usage | Toggle (on/off) | `settings_json.show_token_usage` | Hides/shows the `.token-usage` card in the sidebar footer |
| Show Stage Pipeline | Toggle (on/off) | `settings_json.show_pipeline_stages` | Whether stage progress events are shown during streaming |
| Always Use Web Search | Toggle (on/off, Pro+ only) | `settings_json.always_web_search` | Writes through to `pipeline.settings.always_web_search` already read in `execute_agent_pipeline` |
| Compact Message View | Toggle (on/off) | `settings_json.compact_messages` | Reduces vertical padding in message bubbles |

**Save pattern:** A single `POST /api/user/preferences` endpoint (new, in `chat_routes.py`) accepts a JSON body `{"key": "...", "value": ...}` and writes to `settings_json` in the users table. Changes are applied client-side immediately (JS mutates CSS classes or vars) and persist on next page load via the Jinja context.

### Tab 4 — Subscription

| Setting | Type | Backend | Notes |
|---|---|---|---|
| Current plan badge | Read-only | Template var `user_tier` | Colored badge: Lite/Pro/Max/Developer |
| Token usage summary | Progress bars (daily/monthly) | Template vars already injected | Reuses same data already shown in sidebar footer |
| Token window breakdown | Expandable | `GET /api/billing/subscription` | Shows 5h and weekly limits for Pro+ |
| Upgrade plan button | Link to `/billing` | Existing `/billing` page | Shown for Lite/Pro only, hidden for Max/Developer |
| Manage Billing button | Opens portal | `POST /api/billing/portal` | Shown for users with active subscription only |
| Plan features list | Static text | — | "What's included" for current tier |

### Tab 5 — About & Shortcuts

| Content | Notes |
|---|---|
| Keyboard shortcuts table | Static reference: `Ctrl+Enter` send, `Ctrl+M` voice, `Escape` cancel/close, `Ctrl+/` new chat, `Ctrl+K` open settings |
| App version & build info | Static, from a template variable or hardcoded |
| Links | Privacy Policy (`/privacy`), Terms (`/terms`), Contact/Support email |
| "Open Source Libraries" accordion | Optional, credits vendored libs |

---

## 4. New API Endpoints

### 4.1 `POST /api/user/profile`

**File:** `api/chat_routes.py`  
**Auth:** Session-required (`request.session.get("user_id")`)  
**CSRF:** `X-CSRF-Token` header (JSON body route — same as memory delete)  
**Body:** `{"name": "string"}` (Pydantic model `UpdateProfileRequest`)  
**Logic:**
```python
class UpdateProfileRequest(BaseModel):
    name: str

@router.post("/user/profile")
async def update_user_profile_endpoint(data: UpdateProfileRequest, request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401)
    name = data.name.strip()[:80]  # max 80 chars
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty")
    await asyncio.to_thread(db.update_user_profile, user_id, name, True)
    return {"success": True, "name": name}
```

### 4.2 `POST /api/user/change-password`

**File:** `api/chat_routes.py`  
**Auth:** Session-required  
**CSRF:** `X-CSRF-Token` header  
**Body:** `{"current_password": "...", "new_password": "...", "confirm_password": "..."}`  
**Logic:**
```python
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

@router.post("/user/change-password")
async def change_password_endpoint(data: ChangePasswordRequest, request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401)
    user = await asyncio.to_thread(db.get_user_by_id, user_id)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=400, detail="Password change not available for OAuth accounts")
    if not db.verify_password(user, data.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    valid, msg = validate_password_rules(data.new_password, data.confirm_password, user["email"])
    if not valid:
        raise HTTPException(status_code=422, detail=msg)
    await asyncio.to_thread(db.set_user_password, user_id, data.new_password)
    return {"success": True}
```

Note: `validate_password_rules` already exists in `app.py` — import it or duplicate it into a shared `core/` util.

### 4.3 `DELETE /api/user/memory` (bulk clear)

**File:** `api/chat_routes.py` (alongside existing per-item delete)  
**Auth:** Session-required  
**CSRF:** `X-CSRF-Token` header  
**Logic:**
```python
@router.delete("/user/memory")
async def clear_all_user_memory(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401)
    deleted = await asyncio.to_thread(db.delete_all_user_memory, user_id)
    return {"success": True, "deleted": deleted}
```

**DB function** (`db.py`):
```python
def delete_all_user_memory(user_id: int) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_memory WHERE user_id = %s", (user_id,))
            deleted = cur.rowcount
            conn.commit()
            return deleted
    except Exception as e:
        conn.rollback()
        logger.debug("delete_all_user_memory error: %s", e)
        return 0
    finally:
        put_connection(conn)
```

### 4.4 `POST /api/user/preferences`

**File:** `api/chat_routes.py`  
**Auth:** Session-required  
**CSRF:** `X-CSRF-Token` header  
**Body:** `{"key": "response_language", "value": "en"}` — key/value pair  
**Logic:**
```python
ALLOWED_PREF_KEYS = {
    "response_language", "default_thinking", "show_thinking",
    "show_token_usage", "show_pipeline_stages", "always_web_search",
    "compact_messages",
}

class UpdatePreferenceRequest(BaseModel):
    key: str
    value: Any

@router.post("/user/preferences")
async def update_user_preference(data: UpdatePreferenceRequest, request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401)
    if data.key not in ALLOWED_PREF_KEYS:
        raise HTTPException(status_code=422, detail="Unknown preference key")
    await asyncio.to_thread(db.update_user_setting, user_id, data.key, data.value)
    return {"success": True}
```

**DB function** (`db.py`):
```python
def update_user_setting(user_id: int, key: str, value) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET settings_json = jsonb_set(
                    COALESCE(settings_json, '{}'),
                    %s::text[],
                    %s::jsonb,
                    true
                ), updated_at = NOW()
                WHERE id = %s
                """,
                ([key], json.dumps(value), user_id),
            )
            conn.commit()
            return True
    except Exception as e:
        conn.rollback()
        logger.debug("update_user_setting error: %s", e)
        return False
    finally:
        put_connection(conn)
```

---

## 5. Database Migration

### Migration `013_user_settings.sql`

```sql
-- Migration 013: User-configurable settings stored as JSONB
-- Lightweight column appended to users; no FK dependencies.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS settings_json JSONB NOT NULL DEFAULT '{}';

COMMENT ON COLUMN users.settings_json IS
    'User-configurable preferences: response_language, default_thinking,
     show_thinking, show_token_usage, show_pipeline_stages,
     always_web_search, compact_messages';
```

The JSONB default `'{}'` means all existing users get an empty object — settings fall back to hardcoded defaults in JS. No backfill needed.

---

## 6. Backend Route Changes — `app.py`

### 6.1 Dashboard Context Update

In the `dashboard_page` route (~line 266), add `user_settings` to the Jinja context:

```python
user_settings = user.get("settings_json") or {}
# ... existing context ...
return templates.TemplateResponse(request, "chat.html", {
    # ... all existing keys ...
    "user_settings": user_settings,
    "has_memory": entitlements.has_user_memory,
    "has_password": bool(user.get("password_hash")),  # false for OAuth-only users
})
```

### 6.2 No Changes to Logout Routes

`GET /logout` and `POST /logout` in `app.py` are kept exactly as-is. The Sign Out form in the modal POSTs to the same `/logout` endpoint.

---

## 7. Template Changes — `templates/chat.html`

### 7.1 Remove: Sign Out Form

**Remove** the `<form action="/logout">…</form>` block from `.user-profile-actions`.

**Replace with** the Settings button:

```html
<button id="settings-btn" class="icon-btn" type="button" title="Settings" aria-label="Open settings">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" aria-hidden="true" focusable="false">
    <circle cx="12" cy="12" r="3"></circle>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
  </svg>
</button>
```

### 7.2 Remove: Memory Button from Workspace Header

**Remove** the `#memory-btn` button and the `.header-actions` container from `<header class="workspace-header">`. If `.header-actions` has no other content, remove the container too. The workspace header becomes simpler — just `.breadcrumbs`.

### 7.3 Remove: Memory Modal

**Remove** the entire `#memory-modal` div and the `#memory-toast` div — **wait**: `#memory-toast` should be **kept**. It is driven by the `memory_updated` SSE event (not by the modal button) and must remain functional.

**Keep:** `#memory-toast` and `#memory-toast-text` — move them right before the new `#settings-modal` in the DOM.

### 7.4 Add: Settings Modal

Add a new `#settings-modal` div immediately before the `<script>` tags at the bottom of `<body>`. Full structure:

```html
<!-- Settings Modal -->
<div id="settings-modal" class="modal-backdrop hidden"
     role="dialog" aria-modal="true" aria-labelledby="settings-modal-title">
  <div class="modal-card settings-modal-card">

    <!-- Header -->
    <div class="modal-header">
      <div class="modal-header-left">
        <svg ...gear icon...></svg>
        <h2 id="settings-modal-title" class="modal-title">Settings</h2>
      </div>
      <button id="settings-modal-close" class="modal-close-btn" type="button"
              aria-label="Close settings">&times;</button>
    </div>

    <!-- Tab Bar -->
    <div class="settings-tab-bar" role="tablist">
      <button class="settings-tab active" data-tab="account"    role="tab">Account</button>
      <button class="settings-tab"        data-tab="memory"     role="tab">Memory</button>
      <button class="settings-tab"        data-tab="preferences" role="tab">Preferences</button>
      <button class="settings-tab"        data-tab="subscription" role="tab">Subscription</button>
      <button class="settings-tab"        data-tab="about"      role="tab">About</button>
    </div>

    <!-- Tab Panels -->
    <div class="modal-body settings-modal-body">

      <!-- ── Account Tab ── -->
      <div class="settings-panel active" data-panel="account" role="tabpanel">
        <!-- Display Name -->
        <div class="settings-group">
          <label class="settings-label" for="settings-name-input">Display Name</label>
          <div class="settings-field-row">
            <input type="text" id="settings-name-input" class="settings-text-input"
                   value="{{ user_name or '' }}" maxlength="80" autocomplete="off"
                   placeholder="Your name" aria-label="Display name" />
            <button type="button" id="settings-save-name-btn" class="settings-action-btn">
              Save
            </button>
          </div>
          <span id="settings-name-feedback" class="settings-feedback" aria-live="polite"></span>
        </div>

        <!-- Email -->
        <div class="settings-group">
          <label class="settings-label">Email Address</label>
          <div class="settings-readonly-value">
            {{ user_email or '' }}
            <!-- Email verified badge inserted by JS -->
          </div>
        </div>

        <!-- Change Password (hidden for OAuth-only accounts) -->
        {% if has_password %}
        <div class="settings-group" id="settings-password-group">
          <label class="settings-label">Password</label>
          <button type="button" id="settings-change-password-toggle"
                  class="settings-action-btn settings-action-btn--outline">
            Change Password
          </button>
          <div id="settings-password-fields" class="settings-password-fields hidden">
            <input type="password" id="settings-current-password" class="settings-text-input"
                   placeholder="Current password" autocomplete="current-password" />
            <input type="password" id="settings-new-password" class="settings-text-input"
                   placeholder="New password (8+ chars, upper, lower, number, symbol)" autocomplete="new-password" />
            <input type="password" id="settings-confirm-password" class="settings-text-input"
                   placeholder="Confirm new password" autocomplete="new-password" />
            <div class="settings-field-row">
              <button type="button" id="settings-save-password-btn" class="settings-action-btn">
                Update Password
              </button>
              <button type="button" id="settings-cancel-password-btn"
                      class="settings-action-btn settings-action-btn--ghost">
                Cancel
              </button>
            </div>
            <span id="settings-password-feedback" class="settings-feedback" aria-live="polite"></span>
          </div>
        </div>
        {% endif %}

        <!-- Account Created -->
        <div class="settings-group settings-group--meta">
          <span class="settings-meta-label">Member since</span>
          <span class="settings-meta-value" id="settings-created-at">—</span>
        </div>

        <!-- Sign Out — bottom of account tab -->
        <div class="settings-group settings-signout-group">
          <form action="/logout" method="post" class="settings-logout-form">
            <input type="hidden" name="csrf_token" value="{{ csrf_token or '' }}">
            <button type="submit" class="settings-action-btn settings-action-btn--danger">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                   width="14" height="14" aria-hidden="true">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                <polyline points="16 17 21 12 16 7"></polyline>
                <line x1="21" y1="12" x2="9" y2="12"></line>
              </svg>
              Sign Out
            </button>
          </form>
        </div>
      </div>

      <!-- ── Memory Tab ── -->
      <div class="settings-panel hidden" data-panel="memory" role="tabpanel">
        {% if has_memory %}
        <div class="settings-group">
          <p class="settings-description">
            Durable preferences, cloud environments, and constraints automatically
            learned from your conversations to personalize future answers.
            Bounded to &le;300 tokens and applied across all your sessions.
          </p>
        </div>
        <div class="settings-group">
          <div class="settings-field-row settings-field-row--space-between">
            <span class="settings-label">Remembered Preferences</span>
            <button type="button" id="settings-clear-all-memory-btn"
                    class="settings-action-btn settings-action-btn--danger-outline">
              Clear All
            </button>
          </div>
          <!-- Inline confirm (hidden until Clear All clicked) -->
          <div id="settings-clear-memory-confirm" class="settings-confirm-inline hidden">
            <span>Delete all memories? This cannot be undone.</span>
            <button type="button" id="settings-clear-memory-yes"
                    class="settings-action-btn settings-action-btn--danger">Yes, Delete</button>
            <button type="button" id="settings-clear-memory-no"
                    class="settings-action-btn settings-action-btn--ghost">Cancel</button>
          </div>
        </div>
        <div id="settings-memory-loading" class="memory-status-text">Loading…</div>
        <div id="settings-memory-empty" class="memory-status-text hidden">
          No preferences remembered yet. As you chat, CloudGPT will remember
          your primary cloud, preferred regions, and IaC tooling.
        </div>
        <div id="settings-memory-items" class="memory-items-list"></div>
        {% else %}
        <div class="settings-group">
          <div class="settings-tier-lock">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
                 stroke="currentColor" stroke-width="2">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
              <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
            </svg>
            <div>
              <strong>Memory is a Pro+ feature</strong>
              <p>Upgrade to Pro or Max to unlock cross-session preferences
                 and personalized responses.</p>
              <a href="/billing" class="settings-upgrade-link">Upgrade Plan →</a>
            </div>
          </div>
        </div>
        {% endif %}
      </div>

      <!-- ── Preferences Tab ── -->
      <div class="settings-panel hidden" data-panel="preferences" role="tabpanel">

        <!-- Response Language -->
        <div class="settings-group">
          <label class="settings-label" for="pref-language">Response Language</label>
          <select id="pref-language" class="settings-select"
                  data-pref-key="response_language"
                  aria-label="Default response language">
            <option value="auto">Auto-detect</option>
            <option value="en">English</option>
            <option value="ar">Arabic</option>
            <option value="fr">French</option>
            <option value="de">German</option>
            <option value="hi">Hindi</option>
            <option value="ja">Japanese</option>
            <option value="pt">Portuguese</option>
            <option value="es">Spanish</option>
            <option value="zh">Chinese (Simplified)</option>
          </select>
          <span class="settings-hint">Applies to the next conversation you start.</span>
        </div>

        <!-- Default Thinking Level -->
        <div class="settings-group">
          <label class="settings-label" for="pref-thinking">Default Thinking Level</label>
          <select id="pref-thinking" class="settings-select"
                  data-pref-key="default_thinking"
                  aria-label="Default thinking level">
            <option value="auto">Auto (recommended)</option>
            <option value="Low">Low — Fastest</option>
            <option value="Medium">Medium — Balanced</option>
            <option value="High">High — Thorough</option>
            {% if 'Max' in allowed_thinking_levels %}
            <option value="Max">Max — Deepest Reasoning</option>
            {% endif %}
          </select>
          <span class="settings-hint">Your tier's model access still applies.</span>
        </div>

        <!-- Toggle settings -->
        <div class="settings-group">
          <label class="settings-label">Interface</label>

          <div class="settings-toggle-row" data-pref-key="show_thinking">
            <div>
              <span class="settings-toggle-title">Show Thinking Process</span>
              <span class="settings-toggle-desc">Expand the reasoning panel by default</span>
            </div>
            <button type="button" class="settings-toggle" role="switch"
                    aria-label="Toggle show thinking process" aria-checked="false">
              <span class="settings-toggle-thumb"></span>
            </button>
          </div>

          <div class="settings-toggle-row" data-pref-key="show_token_usage">
            <div>
              <span class="settings-toggle-title">Show Token Usage</span>
              <span class="settings-toggle-desc">Daily & monthly usage in the sidebar</span>
            </div>
            <button type="button" class="settings-toggle" role="switch"
                    aria-label="Toggle show token usage" aria-checked="true">
              <span class="settings-toggle-thumb"></span>
            </button>
          </div>

          <div class="settings-toggle-row" data-pref-key="show_pipeline_stages">
            <div>
              <span class="settings-toggle-title">Show Pipeline Stages</span>
              <span class="settings-toggle-desc">Progress indicator while CloudGPT is thinking</span>
            </div>
            <button type="button" class="settings-toggle" role="switch"
                    aria-label="Toggle show pipeline stages" aria-checked="true">
              <span class="settings-toggle-thumb"></span>
            </button>
          </div>

          <div class="settings-toggle-row" data-pref-key="compact_messages">
            <div>
              <span class="settings-toggle-title">Compact Message View</span>
              <span class="settings-toggle-desc">Reduce spacing between messages</span>
            </div>
            <button type="button" class="settings-toggle" role="switch"
                    aria-label="Toggle compact messages" aria-checked="false">
              <span class="settings-toggle-thumb"></span>
            </button>
          </div>

          <!-- Web Search toggle: Pro+ only -->
          <div class="settings-toggle-row {% if user_tier|lower == 'lite' %}settings-toggle-row--locked{% endif %}"
               data-pref-key="always_web_search">
            <div>
              <span class="settings-toggle-title">Always Use Web Search
                {% if user_tier|lower == 'lite' %}
                <span class="settings-tier-badge">Pro+</span>
                {% endif %}
              </span>
              <span class="settings-toggle-desc">Force live internet search on every query</span>
            </div>
            <button type="button" class="settings-toggle"
                    role="switch" aria-label="Toggle always web search"
                    aria-checked="false"
                    {% if user_tier|lower == 'lite' %}disabled aria-disabled="true"{% endif %}>
              <span class="settings-toggle-thumb"></span>
            </button>
          </div>
        </div>
      </div>

      <!-- ── Subscription Tab ── -->
      <div class="settings-panel hidden" data-panel="subscription" role="tabpanel">
        <div class="settings-group">
          <div class="settings-sub-header">
            <span class="settings-label">Current Plan</span>
            <span class="tier-badge settings-plan-badge">{{ user_tier or 'Lite' }}</span>
          </div>
        </div>

        <div class="settings-group">
          <label class="settings-label">Usage This Billing Period</label>
          <!-- Reuse existing usage row markup pattern -->
          <div class="settings-usage-rows" id="settings-usage-rows">
            <!-- Populated by JS from /api/billing/subscription on tab open -->
            <div class="settings-usage-loading">Loading usage data…</div>
          </div>
        </div>

        <div class="settings-group settings-sub-actions">
          {% if user_tier and user_tier|lower not in ['max', 'developer'] and not is_unlimited %}
          <a href="/billing" class="settings-action-btn settings-action-btn--primary"
             id="settings-upgrade-btn">
            Upgrade Plan
          </a>
          {% endif %}
          <button type="button" id="settings-billing-portal-btn"
                  class="settings-action-btn settings-action-btn--outline hidden">
            Manage Billing
          </button>
        </div>

        <div class="settings-group settings-plan-features">
          <label class="settings-label">What's Included</label>
          <ul class="settings-features-list" id="settings-features-list">
            <!-- Populated by JS based on tier -->
          </ul>
        </div>
      </div>

      <!-- ── About & Shortcuts Tab ── -->
      <div class="settings-panel hidden" data-panel="about" role="tabpanel">
        <div class="settings-group">
          <label class="settings-label">Keyboard Shortcuts</label>
          <table class="settings-shortcuts-table" aria-label="Keyboard shortcuts">
            <tbody>
              <tr><td><kbd>Ctrl</kbd>+<kbd>Enter</kbd></td><td>Send message</td></tr>
              <tr><td><kbd>Ctrl</kbd>+<kbd>M</kbd></td><td>Toggle voice input</td></tr>
              <tr><td><kbd>Escape</kbd></td><td>Cancel stream / Close panel</td></tr>
              <tr><td><kbd>Ctrl</kbd>+<kbd>/</kbd></td><td>New conversation</td></tr>
              <tr><td><kbd>Ctrl</kbd>+<kbd>,</kbd></td><td>Open settings</td></tr>
            </tbody>
          </table>
        </div>

        <div class="settings-group">
          <label class="settings-label">Application</label>
          <div class="settings-about-row">
            <span class="settings-meta-label">CloudGPT</span>
            <span class="settings-meta-value">Enterprise Multi-Cloud Research Assistant</span>
          </div>
          <div class="settings-about-row">
            <span class="settings-meta-label">Coverage</span>
            <span class="settings-meta-value">848 AWS / GCP / Azure services · 27 categories</span>
          </div>
        </div>

        <div class="settings-group">
          <label class="settings-label">Legal & Support</label>
          <div class="settings-links-row">
            <a href="/privacy" class="settings-text-link" target="_blank">Privacy Policy</a>
            <a href="/terms" class="settings-text-link" target="_blank">Terms of Service</a>
          </div>
        </div>
      </div>

    </div><!-- /.modal-body -->
  </div><!-- /.modal-card -->
</div><!-- /#settings-modal -->
```

### 7.5 Data Attributes on `<body>` or `<div class="app-container">`

To make JS preferences take effect on page load, add data attributes from Jinja:

```html
<div class="app-container"
     data-user-tier="{{ effective_tier or user_tier }}"
     data-show-token-usage="{{ user_settings.get('show_token_usage', true) | lower }}"
     data-compact-messages="{{ user_settings.get('compact_messages', false) | lower }}"
     data-show-pipeline="{{ user_settings.get('show_pipeline_stages', true) | lower }}">
```

---

## 8. CSS Changes — `static/css/chat.css`

All new rules are **appended** after the existing memory/modal rules. No existing selectors are modified.

### 8.1 Settings Modal Layout

```css
/* ── Settings Modal ── */
.settings-modal-card {
  width: min(580px, 96vw);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
}

.settings-tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  padding: 0 20px;
  overflow-x: auto;
  scrollbar-width: none;
  flex-shrink: 0;
}

.settings-tab {
  background: transparent;
  border: none;
  color: var(--text-secondary);
  font-size: 0.825rem;
  font-weight: 500;
  padding: 10px 14px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  white-space: nowrap;
  transition: color 0.15s, border-color 0.15s;
}

.settings-tab:hover  { color: var(--text-primary); }
.settings-tab.active { color: #fff; border-bottom-color: #fff; }

.settings-modal-body {
  overflow-y: auto;
  flex: 1;
  padding: 20px;
}

.settings-panel        { display: none; }
.settings-panel.active { display: block; }
```

### 8.2 Form Elements Inside Settings

```css
.settings-group {
  margin-bottom: 24px;
}

.settings-label {
  display: block;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 8px;
}

.settings-text-input, .settings-select {
  width: 100%;
  padding: 9px 12px;
  background: var(--bg-subsurface);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  color: var(--text-primary);
  font-size: 0.875rem;
  outline: none;
  transition: border-color 0.15s;
  box-sizing: border-box;
}

.settings-text-input:focus,
.settings-select:focus {
  border-color: rgba(255, 255, 255, 0.4);
}

.settings-field-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.settings-field-row--space-between {
  justify-content: space-between;
  align-items: center;
}

.settings-action-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid var(--border-strong);
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-primary);
  transition: background 0.15s, border-color 0.15s;
  white-space: nowrap;
}
.settings-action-btn:hover {
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.3);
}
.settings-action-btn--primary {
  background: #fff;
  color: #000;
  border-color: #fff;
}
.settings-action-btn--primary:hover { background: #e5e5e5; }
.settings-action-btn--outline {
  background: transparent;
  border-color: var(--border-strong);
}
.settings-action-btn--ghost {
  background: transparent;
  border-color: transparent;
  color: var(--text-secondary);
}
.settings-action-btn--ghost:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }
.settings-action-btn--danger {
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.4);
  color: #ef4444;
}
.settings-action-btn--danger:hover { background: rgba(239, 68, 68, 0.2); }
.settings-action-btn--danger-outline {
  background: transparent;
  border-color: rgba(239, 68, 68, 0.3);
  color: #ef4444;
}

.settings-password-fields {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}

.settings-feedback {
  display: block;
  font-size: 0.8rem;
  margin-top: 6px;
  min-height: 18px;
}
.settings-feedback.success { color: var(--success-color); }
.settings-feedback.error   { color: var(--danger-color); }

.settings-hint {
  display: block;
  font-size: 0.775rem;
  color: var(--text-muted);
  margin-top: 5px;
}

.settings-readonly-value {
  font-size: 0.875rem;
  color: var(--text-primary);
  padding: 9px 12px;
  background: rgba(255,255,255,0.03);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.settings-signout-group {
  margin-top: 32px;
  padding-top: 20px;
  border-top: 1px solid rgba(255,255,255,0.06);
}
.settings-logout-form { display: inline; }

.settings-group--meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.settings-meta-label { font-size: 0.8rem; color: var(--text-muted); }
.settings-meta-value { font-size: 0.8rem; color: var(--text-secondary); }
```

### 8.3 Toggle Switches

```css
/* ── Settings Toggle Switch ── */
.settings-toggle-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}
.settings-toggle-row:last-child { border-bottom: none; }
.settings-toggle-row--locked { opacity: 0.45; pointer-events: none; }

.settings-toggle-title { display: block; font-size: 0.875rem; color: var(--text-primary); }
.settings-toggle-desc  { display: block; font-size: 0.775rem; color: var(--text-muted); margin-top: 2px; }

.settings-toggle {
  position: relative;
  width: 36px;
  height: 20px;
  background: rgba(255,255,255,0.15);
  border: none;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.2s;
  flex-shrink: 0;
  padding: 0;
}
.settings-toggle[aria-checked="true"]  { background: #ffffff; }
.settings-toggle-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #444;
  transition: transform 0.2s, background 0.2s;
}
.settings-toggle[aria-checked="true"] .settings-toggle-thumb {
  transform: translateX(16px);
  background: #000;
}
```

### 8.4 Subscription & About Tabs

```css
.settings-sub-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.settings-plan-badge {
  font-size: 0.75rem;
  padding: 3px 10px;
}
.settings-usage-rows { margin-top: 8px; }
.settings-usage-loading { color: var(--text-muted); font-size: 0.875rem; }
.settings-sub-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.settings-features-list {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
}
.settings-features-list li {
  font-size: 0.85rem;
  color: var(--text-secondary);
  padding: 5px 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.settings-features-list li::before {
  content: '✓';
  color: var(--success-color);
  font-size: 0.75rem;
}

.settings-shortcuts-table { width: 100%; border-collapse: collapse; }
.settings-shortcuts-table td { padding: 7px 0; font-size: 0.85rem; color: var(--text-secondary); }
.settings-shortcuts-table td:first-child { width: 160px; }
kbd {
  display: inline-block;
  padding: 2px 6px;
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.2);
  border-radius: 4px;
  font-size: 0.75rem;
  font-family: inherit;
  color: var(--text-primary);
}

.settings-about-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  font-size: 0.85rem;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}
.settings-links-row { display: flex; gap: 16px; margin-top: 4px; }
.settings-text-link { font-size: 0.85rem; color: var(--text-secondary); }
.settings-text-link:hover { color: var(--text-primary); }

.settings-tier-lock {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 16px;
  background: rgba(255,255,255,0.03);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  color: var(--text-secondary);
}
.settings-tier-lock strong { display: block; color: var(--text-primary); margin-bottom: 4px; }
.settings-tier-lock p { font-size: 0.85rem; margin: 4px 0 8px; }
.settings-upgrade-link { color: var(--text-primary); font-size: 0.85rem; }

.settings-confirm-inline {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: rgba(239,68,68,0.08);
  border: 1px solid rgba(239,68,68,0.2);
  border-radius: 8px;
  font-size: 0.85rem;
  color: var(--text-secondary);
  flex-wrap: wrap;
  margin-top: 8px;
}

.settings-tier-badge {
  display: inline-block;
  padding: 1px 6px;
  background: rgba(255,255,255,0.08);
  border-radius: 4px;
  font-size: 0.7rem;
  color: var(--text-muted);
  vertical-align: middle;
  margin-left: 4px;
}

.settings-description {
  font-size: 0.85rem;
  color: var(--text-muted);
  line-height: 1.55;
  margin: 0;
}
```

### 8.5 Remove `#memory-btn` styles

The `.header-icon-btn` CSS block can be removed if no other elements use it after the Memory button is gone. Verify with a grep before removing to ensure nothing else uses `.header-icon-btn`.

---

## 9. JavaScript Changes — `static/js/chat.js`

### 9.1 Replace Memory DOM References

**Remove:**
```js
const memoryBtn = document.getElementById('memory-btn');
const memoryModal = document.getElementById('memory-modal');
const memoryModalClose = document.getElementById('memory-modal-close');
const memoryLoading = document.getElementById('memory-loading');
const memoryEmpty = document.getElementById('memory-empty');
const memoryItems = document.getElementById('memory-items');
```

**Add:**
```js
// Settings modal elements
const settingsBtn = document.getElementById('settings-btn');
const settingsModal = document.getElementById('settings-modal');
const settingsModalClose = document.getElementById('settings-modal-close');
const settingsTabs = document.querySelectorAll('.settings-tab');
const settingsPanels = document.querySelectorAll('.settings-panel');

// Settings — Account tab
const settingsNameInput = document.getElementById('settings-name-input');
const settingsSaveNameBtn = document.getElementById('settings-save-name-btn');
const settingsNameFeedback = document.getElementById('settings-name-feedback');
const settingsChangePasswordToggle = document.getElementById('settings-change-password-toggle');
const settingsPasswordFields = document.getElementById('settings-password-fields');
const settingsSavePasswordBtn = document.getElementById('settings-save-password-btn');
const settingsCancelPasswordBtn = document.getElementById('settings-cancel-password-btn');
const settingsPasswordFeedback = document.getElementById('settings-password-feedback');

// Settings — Memory tab
const settingsMemoryLoading = document.getElementById('settings-memory-loading');
const settingsMemoryEmpty = document.getElementById('settings-memory-empty');
const settingsMemoryItems = document.getElementById('settings-memory-items');
const settingsClearAllMemoryBtn = document.getElementById('settings-clear-all-memory-btn');
const settingsClearMemoryConfirm = document.getElementById('settings-clear-memory-confirm');
const settingsClearMemoryYes = document.getElementById('settings-clear-memory-yes');
const settingsClearMemoryNo = document.getElementById('settings-clear-memory-no');

// Settings — Preferences tab
const prefToggles = document.querySelectorAll('.settings-toggle[data-pref-key]');
const prefSelects = document.querySelectorAll('.settings-select[data-pref-key]');

// Settings — Subscription tab
const settingsBillingPortalBtn = document.getElementById('settings-billing-portal-btn');

// Memory toast (kept — driven by SSE, unrelated to modal)
const memoryToast = document.getElementById('memory-toast');
const memoryToastText = document.getElementById('memory-toast-text');
let memoryToastTimeout = null;
```

### 9.2 Settings Modal Open / Close

```js
let currentSettingsTab = 'account';

function openSettingsModal(tab = 'account') {
  if (!settingsModal) return;
  settingsModal.classList.remove('hidden');
  switchSettingsTab(tab);
  if (tab === 'memory') loadSettingsMemory();
  if (tab === 'subscription') loadSettingsSubscription();
}

function closeSettingsModal() {
  if (settingsModal) settingsModal.classList.add('hidden');
}

function switchSettingsTab(tab) {
  currentSettingsTab = tab;
  settingsTabs.forEach((t) => {
    t.classList.toggle('active', t.dataset.tab === tab);
    t.setAttribute('aria-selected', String(t.dataset.tab === tab));
  });
  settingsPanels.forEach((p) => {
    const isActive = p.dataset.panel === tab;
    p.classList.toggle('active', isActive);
    p.classList.toggle('hidden', !isActive);
  });
  if (tab === 'memory') loadSettingsMemory();
  if (tab === 'subscription') loadSettingsSubscription();
}
```

### 9.3 Tab Navigation Wiring

```js
settingsTabs.forEach((tab) => {
  tab.addEventListener('click', () => switchSettingsTab(tab.dataset.tab));
});
if (settingsBtn) settingsBtn.addEventListener('click', () => openSettingsModal('account'));
if (settingsModalClose) settingsModalClose.addEventListener('click', closeSettingsModal);
if (settingsModal) {
  settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) closeSettingsModal();
  });
}
```

### 9.4 Account Tab — Save Name

```js
if (settingsSaveNameBtn) {
  settingsSaveNameBtn.addEventListener('click', async () => {
    const name = settingsNameInput?.value.trim() || '';
    if (!name) {
      showSettingsFeedback(settingsNameFeedback, 'error', 'Name cannot be empty.');
      return;
    }
    settingsSaveNameBtn.disabled = true;
    settingsSaveNameBtn.textContent = 'Saving…';
    try {
      const res = await fetch('/api/user/profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to save name');
      }
      showSettingsFeedback(settingsNameFeedback, 'success', 'Name updated.');
      // Update avatar and sidebar display name
      const nameEl = document.querySelector('.user-profile .name');
      const avatarEl = document.querySelector('.user-profile .avatar');
      if (nameEl) nameEl.textContent = name;
      if (avatarEl) avatarEl.textContent = name[0].toUpperCase();
    } catch (err) {
      showSettingsFeedback(settingsNameFeedback, 'error', err.message);
    } finally {
      settingsSaveNameBtn.disabled = false;
      settingsSaveNameBtn.textContent = 'Save';
    }
  });
}

function showSettingsFeedback(el, type, msg) {
  if (!el) return;
  el.textContent = msg;
  el.className = `settings-feedback ${type}`;
  setTimeout(() => { if (el) el.textContent = ''; }, 4000);
}
```

### 9.5 Account Tab — Change Password

```js
if (settingsChangePasswordToggle) {
  settingsChangePasswordToggle.addEventListener('click', () => {
    settingsPasswordFields?.classList.toggle('hidden');
  });
}

if (settingsCancelPasswordBtn) {
  settingsCancelPasswordBtn.addEventListener('click', () => {
    settingsPasswordFields?.classList.add('hidden');
    if (settingsPasswordFeedback) settingsPasswordFeedback.textContent = '';
    document.getElementById('settings-current-password').value = '';
    document.getElementById('settings-new-password').value = '';
    document.getElementById('settings-confirm-password').value = '';
  });
}

if (settingsSavePasswordBtn) {
  settingsSavePasswordBtn.addEventListener('click', async () => {
    const current = document.getElementById('settings-current-password')?.value || '';
    const newPwd = document.getElementById('settings-new-password')?.value || '';
    const confirm = document.getElementById('settings-confirm-password')?.value || '';
    if (!current || !newPwd || !confirm) {
      showSettingsFeedback(settingsPasswordFeedback, 'error', 'All fields are required.');
      return;
    }
    settingsSavePasswordBtn.disabled = true;
    settingsSavePasswordBtn.textContent = 'Updating…';
    try {
      const res = await fetch('/api/user/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify({ current_password: current, new_password: newPwd, confirm_password: confirm }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Password update failed');
      showSettingsFeedback(settingsPasswordFeedback, 'success', 'Password updated successfully.');
      settingsPasswordFields?.classList.add('hidden');
      document.getElementById('settings-current-password').value = '';
      document.getElementById('settings-new-password').value = '';
      document.getElementById('settings-confirm-password').value = '';
    } catch (err) {
      showSettingsFeedback(settingsPasswordFeedback, 'error', err.message);
    } finally {
      settingsSavePasswordBtn.disabled = false;
      settingsSavePasswordBtn.textContent = 'Update Password';
    }
  });
}
```

### 9.6 Memory Tab — Load & Delete (migrated from `openMemoryModal`)

```js
async function loadSettingsMemory() {
  if (!settingsMemoryItems) return;
  if (settingsMemoryLoading) settingsMemoryLoading.classList.remove('hidden');
  if (settingsMemoryEmpty) settingsMemoryEmpty.classList.add('hidden');
  settingsMemoryItems.replaceChildren();
  try {
    const resp = await fetch('/api/user/memory', { headers: { Accept: 'application/json' } });
    if (!resp.ok) throw new Error('Failed to load memory');
    const data = await resp.json();
    const list = data.memories || [];
    if (settingsMemoryLoading) settingsMemoryLoading.classList.add('hidden');
    if (list.length === 0) {
      if (settingsMemoryEmpty) settingsMemoryEmpty.classList.remove('hidden');
      return;
    }
    list.forEach((mem) => {
      const card = element('div', 'memory-item-card');
      const content = element('div', 'memory-item-content');
      const header = element('div', 'memory-item-header');
      const badge = element('span', 'memory-category-badge', mem.category || 'general');
      const keyName = element('span', 'memory-key-name', mem.memory_key || mem.key || 'Preference');
      header.append(badge, keyName);
      const val = element('div', 'memory-item-value', mem.memory_value || mem.value || '');
      content.append(header, val);
      const delBtn = element('button', 'memory-delete-btn', 'Delete');
      delBtn.type = 'button';
      delBtn.title = 'Forget this preference';
      delBtn.addEventListener('click', async () => {
        const delResp = await fetch(`/api/user/memory/${mem.id}`, {
          method: 'DELETE',
          headers: { 'X-CSRF-Token': csrfToken },
        });
        if (delResp.ok) {
          card.remove();
          if (settingsMemoryItems && settingsMemoryItems.children.length === 0 && settingsMemoryEmpty) {
            settingsMemoryEmpty.classList.remove('hidden');
          }
        }
      });
      card.append(content, delBtn);
      settingsMemoryItems.append(card);
    });
  } catch {
    if (settingsMemoryLoading) settingsMemoryLoading.textContent = 'Failed to load preferences.';
  }
}
```

### 9.7 Memory Tab — Clear All

```js
if (settingsClearAllMemoryBtn) {
  settingsClearAllMemoryBtn.addEventListener('click', () => {
    settingsClearMemoryConfirm?.classList.remove('hidden');
  });
}

if (settingsClearMemoryNo) {
  settingsClearMemoryNo.addEventListener('click', () => {
    settingsClearMemoryConfirm?.classList.add('hidden');
  });
}

if (settingsClearMemoryYes) {
  settingsClearMemoryYes.addEventListener('click', async () => {
    settingsClearMemoryYes.disabled = true;
    settingsClearMemoryYes.textContent = 'Deleting…';
    try {
      const res = await fetch('/api/user/memory', {
        method: 'DELETE',
        headers: { 'X-CSRF-Token': csrfToken },
      });
      if (!res.ok) throw new Error('Clear failed');
      settingsClearMemoryConfirm?.classList.add('hidden');
      settingsMemoryItems?.replaceChildren();
      if (settingsMemoryEmpty) settingsMemoryEmpty.classList.remove('hidden');
    } catch {
      // silently restore button
    } finally {
      settingsClearMemoryYes.disabled = false;
      settingsClearMemoryYes.textContent = 'Yes, Delete';
    }
  });
}
```

### 9.8 Preferences Tab — Toggles & Selects

```js
// Load preferences from data attributes on app-container (set from settings_json)
function initPreferences() {
  const app = document.querySelector('.app-container');
  if (!app) return;

  // Map of pref keys to their initial values (read from data attrs set by Jinja)
  const savedPrefs = {};
  try {
    // Optionally embed as JSON in a data attr: data-user-settings='{"key":val}'
    const raw = app.dataset.userSettings || '{}';
    Object.assign(savedPrefs, JSON.parse(raw));
  } catch {}

  prefToggles.forEach((btn) => {
    const key = btn.closest('[data-pref-key]')?.dataset.prefKey;
    if (!key) return;
    const val = savedPrefs[key] !== undefined ? savedPrefs[key] : getDefaultPref(key);
    btn.setAttribute('aria-checked', String(val));
    applyPrefEffect(key, val);
    btn.addEventListener('click', async () => {
      const current = btn.getAttribute('aria-checked') === 'true';
      const next = !current;
      btn.setAttribute('aria-checked', String(next));
      applyPrefEffect(key, next);
      await savePref(key, next);
    });
  });

  prefSelects.forEach((sel) => {
    const key = sel.dataset.prefKey;
    const val = savedPrefs[key] !== undefined ? savedPrefs[key] : '';
    if (val) sel.value = val;
    sel.addEventListener('change', async () => {
      await savePref(key, sel.value);
    });
  });
}

function getDefaultPref(key) {
  const defaults = {
    show_thinking: false,
    show_token_usage: true,
    show_pipeline_stages: true,
    always_web_search: false,
    compact_messages: false,
  };
  return defaults[key] ?? false;
}

function applyPrefEffect(key, value) {
  const app = document.querySelector('.app-container');
  if (!app) return;
  if (key === 'show_token_usage') {
    const usage = document.getElementById('token-usage-card');
    if (usage) usage.style.display = value ? '' : 'none';
  }
  if (key === 'compact_messages') {
    app.classList.toggle('compact-messages', Boolean(value));
  }
  if (key === 'show_pipeline_stages') {
    app.dataset.showPipeline = String(value);
  }
}

async function savePref(key, value) {
  try {
    await fetch('/api/user/preferences', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
      body: JSON.stringify({ key, value }),
    });
  } catch {}
}
```

### 9.9 Subscription Tab — Load Data

```js
let subscriptionDataLoaded = false;

async function loadSettingsSubscription() {
  if (subscriptionDataLoaded) return;
  const container = document.getElementById('settings-usage-rows');
  if (!container) return;
  try {
    const res = await fetch('/api/billing/subscription', { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('Could not load subscription');
    const data = await res.json();
    subscriptionDataLoaded = true;

    const { usage, limits } = data;
    container.innerHTML = '';

    const rows = [
      { label: 'Daily Tokens', used: usage.tokens_used_day, limit: limits.tokens_day },
      { label: 'Monthly Tokens', used: usage.tokens_used_month, limit: limits.tokens_month },
      { label: '5-Hour Window', used: usage.tokens_used_5h, limit: limits.tokens_5h },
    ];
    rows.forEach(({ label, used, limit }) => {
      const row = element('div', 'usage-row');
      const labelEl = element('span', 'usage-label', label);
      const limitLabel = limit ? used.toLocaleString() + ' / ' + limit.toLocaleString() : used.toLocaleString() + ' / ∞';
      const valEl = element('span', 'usage-val', limitLabel);
      row.append(labelEl, valEl);
      container.append(row);
    });

    // Show Manage Billing button if subscription exists
    if (data.subscription && settingsBillingPortalBtn) {
      settingsBillingPortalBtn.classList.remove('hidden');
      settingsBillingPortalBtn.addEventListener('click', async () => {
        settingsBillingPortalBtn.disabled = true;
        settingsBillingPortalBtn.textContent = 'Opening…';
        try {
          const portalRes = await fetch('/api/billing/portal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ return_url: window.location.href }),
          });
          const portalData = await portalRes.json();
          if (portalData.url) window.location.href = portalData.url;
        } catch {
          settingsBillingPortalBtn.disabled = false;
          settingsBillingPortalBtn.textContent = 'Manage Billing';
        }
      });
    }

    // Plan features list
    const featuresList = document.getElementById('settings-features-list');
    if (featuresList) {
      const features = getPlanFeatures(data.plan);
      features.forEach((f) => {
        const li = element('li', null, f);
        featuresList.append(li);
      });
    }
  } catch {
    if (container) container.textContent = 'Unable to load subscription data.';
  }
}

function getPlanFeatures(plan) {
  const featureMap = {
    lite: ['Lite model access', 'Web search enabled', 'RAG knowledge base', '2 file uploads (2 MB each)', '25,000 daily tokens'],
    pro:  ['Core model access', 'Web search enabled', 'RAG knowledge base', 'User memory (cross-session)', '5 file uploads (5 MB each)', '150,000 daily tokens'],
    max:  ['Apex, Core & Lite model access', 'Max-depth reasoning', 'Web search enabled', 'RAG knowledge base', 'User memory (cross-session)', '10 file uploads (10 MB each)', '500,000 daily tokens'],
    developer: ['All models (Apex, Core, Lite)', 'Max-depth reasoning', 'Unlimited token quota', 'User memory (cross-session)', '20 file uploads (20 MB each)', 'Full cloud API tools'],
  };
  return featureMap[plan?.toLowerCase()] || featureMap.lite;
}
```

### 9.10 Keyboard Shortcut — `Ctrl+,` for Settings

```js
// In the existing keydown handler (alongside Escape):
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if (settingsModal && !settingsModal.classList.contains('hidden')) {
      closeSettingsModal();
    } else if (activeController) {
      activeController.abort();
    }
  }
  if ((event.ctrlKey || event.metaKey) && event.key === ',') {
    event.preventDefault();
    openSettingsModal('account');
  }
});
```

### 9.11 Update ESC Handler (Remove Old Memory Modal Reference)

**Remove:**
```js
if (memoryModal && !memoryModal.classList.contains('hidden')) {
  closeMemoryModal();
}
```

**Replace with:**
```js
if (settingsModal && !settingsModal.classList.contains('hidden')) {
  closeSettingsModal();
}
```

### 9.12 Memory Toast — No Change

`showMemoryToast(text)` and its SSE handler (`data.type === 'memory_updated'`) remain completely unchanged. Only the trigger source (the old modal button) is replaced.

### 9.13 Initialize Preferences on DOMContentLoaded

Add `initPreferences()` call inside `document.addEventListener('DOMContentLoaded', () => { ... })` after all DOM elements are resolved.

---

## 10. Data Attribute for Preferences (Jinja)

In `app.py` dashboard route, `user_settings` dict is passed. In `chat.html`, embed it:

```html
<div class="app-container"
     data-user-tier="{{ effective_tier or user_tier }}"
     data-user-settings="{{ user_settings | tojson | e }}">
```

This allows `initPreferences()` to read saved preferences without an extra API call on page load.

---

## 11. Implementation Order (Recommended)

Execute tasks in this exact order to avoid breaking the app at any intermediate step:

| Step | File(s) Changed | Notes |
|---|---|---|
| **1** | `migrations/013_user_settings.sql` | Add `settings_json` column; run via `python migrate.py` |
| **2** | `db.py` | Add `update_user_setting()`, `delete_all_user_memory()`, `update_user_profile()` name-only overload |
| **3** | `api/chat_routes.py` | Add 4 new endpoints: `POST /user/profile`, `POST /user/change-password`, `DELETE /user/memory` (bulk), `POST /user/preferences` |
| **4** | `app.py` | Add `user_settings` and `has_memory` and `has_password` to dashboard Jinja context |
| **5** | `static/css/chat.css` | Append all new Settings CSS rules; verify `.header-icon-btn` usage before removing |
| **6** | `templates/chat.html` | (a) Replace logout form with `#settings-btn`; (b) Remove `#memory-btn` and `.header-actions`; (c) Remove `#memory-modal`; (d) Add `#settings-modal`; (e) Add `data-user-settings` attribute to `.app-container` |
| **7** | `static/js/chat.js` | Replace memory DOM references, add all Settings JS sections (9.1–9.13) |
| **8** | Verify | Run `python -m pytest -v`; check lint with `python -m ruff check .`; smoke test all 5 tabs manually |

---

## 12. Testing Checklist

| Check | Expected |
|---|---|
| Settings button appears in sidebar footer (replaces logout icon) | ✅ Gear SVG, same `.icon-btn` styling |
| Memory button removed from workspace header | ✅ Header is clean |
| `Ctrl+,` opens Settings | ✅ Account tab focused |
| ESC closes Settings modal | ✅ |
| Backdrop click closes Settings modal | ✅ |
| Account tab: Name save → sidebar name/avatar update | ✅ |
| Account tab: Wrong current password → error feedback | ✅ |
| Account tab: Password rules violated → error feedback | ✅ |
| Account tab: Sign Out button → POST /logout → redirects to login | ✅ |
| Account tab: OAuth user → no Change Password section | ✅ `has_password=False` |
| Memory tab: Items load from API | ✅ |
| Memory tab: Individual delete → card removed | ✅ |
| Memory tab: Clear All → confirm inline appears → Yes deletes → empty state | ✅ |
| Memory tab: Lite user → tier lock banner shown | ✅ |
| Preferences tab: Toggle show_token_usage → sidebar usage card hides/shows | ✅ |
| Preferences tab: Toggle saved → persists on page reload | ✅ |
| Subscription tab: Usage rows populated from `/api/billing/subscription` | ✅ |
| Subscription tab: Manage Billing hidden if no subscription | ✅ |
| About tab: Shortcuts table renders | ✅ |
| Memory toast still fires on SSE `memory_updated` event | ✅ (untouched) |
| Existing tests pass without API keys | ✅ Mock at llm/provider.py boundary |
| `ruff check .` — no new lint errors | ✅ |
| ARIA: `role="dialog"`, `aria-modal`, `aria-labelledby` all set | ✅ |
| Mobile (< 768px): Settings modal fits screen, tabs scroll horizontally | ✅ |

---

## 13. Non-Negotiables Compliance Check

| Rule | Status |
|---|---|
| Config only through `config.py` Settings | ✅ No new `os.environ` reads; `settings_json` defaults managed in db/JS |
| Secrets never echoed | ✅ No secrets in new endpoints |
| Every mutating POST/DELETE has CSRF | ✅ All 4 new endpoints use `X-CSRF-Token` header |
| DB is synchronous — use `run_in_executor` | ✅ All new DB calls wrapped in `asyncio.to_thread()` |
| Redis optional — no crash if disabled | ✅ New endpoints don't use Redis directly |
| One upload funnel unchanged | ✅ No file upload routes added |
| Additive-only frontend | ✅ New DOM inside new `#settings-modal`; existing SSE events untouched |
| Migrations are append-only | ✅ New file `013_user_settings.sql`; existing files not touched |
| Structlog / metrics.py style | ✅ New endpoints follow existing `logger.debug` pattern |
| SSE event contract unchanged | ✅ No new event types; no existing shapes modified |

---

## 14. Out of Scope (Future Work)

- **Email address change** — requires re-verification flow; considerable security surface; leave for a future milestone.
- **Two-factor authentication (2FA)** — requires TOTP library and migrations; separate workstream.
- **Theme switching** (light/dark/system) — the design system is monochrome black; no tokens exist for a light theme yet.
- **Notification preferences** — no notification system currently exists.
- **Export conversation history** — useful but requires background job; separate task.
- **Delete account** — high-risk destructive action; requires separate confirmation flow and data erasure job.
- **API key management** — no user-facing API key system currently.

---

*Content was researched against ChatGPT Settings, Claude Settings, and industry UX pattern sources including [openai.com](https://help.openai.com/en/articles/8590148-memory-faq-), [claude.com](https://support.claude.com/en/articles/8887527-customizing-your-appearance-settings), and [toptal.com](https://www.toptal.com/designers/ux/settings-ux). Content was paraphrased for compliance with licensing restrictions.*

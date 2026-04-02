"""
generate_secrets.py
───────────────────
Run once locally to generate cryptographically secure values for:
  - ENCRYPTION_KEY  (used by auth_manager.py to encrypt/decrypt API keys)
  - JWT_SECRET      (used by main.py to sign/verify dashboard login tokens)

Usage:
    python generate_secrets.py

Then copy the output into:
  - Your local  .env  file          (for local dev)
  - GitHub Secrets                  (for production / CI)
    Settings → Secrets and variables → Actions → New repository secret
"""

import secrets
import base64

# ── ENCRYPTION_KEY ────────────────────────────────────────────────────────── #
# 32 random bytes → base64-encoded → 44-char URL-safe string.
# Fernet (the fallback) requires exactly 32 decoded bytes, which this satisfies.
# securepipe accepts any string, so this works for both.
encryption_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

# ── JWT_SECRET ────────────────────────────────────────────────────────────── #
# 64 random bytes → hex string → 128 chars.
# Long enough to be brute-force proof for HS256 JWT signing.
jwt_secret = secrets.token_hex(64)

print("=" * 60)
print("  GENERATED SECRETS — copy these to .env / GitHub Secrets")
print("=" * 60)
print()
print(f"ENCRYPTION_KEY={encryption_key}")
print()
print(f"JWT_SECRET={jwt_secret}")
print()
print("=" * 60)
print("  ⚠  IMPORTANT WARNINGS")
print("=" * 60)
print()
print("1. ENCRYPTION_KEY must NEVER change after first deployment.")
print("   Changing it makes all stored API keys unreadable.")
print("   You would have to delete data/api_keys.json and")
print("   regenerate every key.")
print()
print("2. JWT_SECRET can be rotated but it will immediately")
print("   invalidate all active dashboard sessions (users get")
print("   logged out).")
print()
print("3. Never commit these values to source control.")
print("   Add .env to your .gitignore.")
print()
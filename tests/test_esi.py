import json
import unittest

from pressure_overlay import windows
from pressure_overlay.esi import (
    EsiError,
    b64encode,
    character_from_token,
    rate_spacing,
    retry_after,
)


def token(**claims):
    payload = b64encode(json.dumps(claims).encode())
    return {"access_token": f"header.{payload}.signature", "refresh_token": "refresh"}


class EsiTest(unittest.TestCase):
    def test_token_claims_identify_the_character_for_this_app(self):
        data = token(
            iss="https://login.eveonline.com",
            aud=["client", "EVE Online"],
            sub="CHARACTER:EVE:90000001",
            name="Test Pilot",
            exp=1700000000,
        )
        character = character_from_token(data, "client")
        self.assertEqual((character.id, character.name), (90000001, "Test Pilot"))
        self.assertEqual((character.refresh_token, character.expires), ("refresh", 1700000000))

    def test_token_for_another_app_is_rejected(self):
        for claims in (
            {"iss": "https://evil.example", "aud": ["client", "EVE Online"]},
            {"iss": "login.eveonline.com", "aud": ["other", "EVE Online"]},
        ):
            with self.assertRaises(EsiError):
                character_from_token(token(sub="CHARACTER:EVE:1", **claims), "client")
        with self.assertRaises(EsiError):
            character_from_token({"access_token": "not a jwt"}, "client")

    def test_refresh_tokens_are_encrypted_for_the_windows_user(self):
        secret = b'{"refresh_token": "abc"}'
        sealed = windows.protect(secret)
        self.assertNotIn(b"abc", sealed)
        self.assertEqual(windows.unprotect(sealed), secret)

    def test_polling_uses_at_most_half_of_each_rate_limit_group(self):
        location = {
            "X-Ratelimit-Group": "char-location",
            "X-Ratelimit-Limit": "1200/15m",
            "X-Ratelimit-Used": "2",
        }
        # Two 2-token requests a poll against 1,200 tokens per 900 s, at half the allowance.
        self.assertEqual(rate_spacing(location, location), 6)
        self.assertEqual(rate_spacing({}), 0)
        self.assertEqual(retry_after({"Retry-After": "90"}, 60), 90)
        self.assertEqual(retry_after({}, 60), 60)

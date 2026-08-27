from __future__ import annotations

from email.message import EmailMessage
import unittest
from unittest.mock import patch

from binance_analyzer.integrations import email_imap


class _FakeImapConnection:
    def __init__(self, raw_messages):
        self.raw_messages = raw_messages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def select(self, _mailbox):
        return "OK", [b""]

    def search(self, *_args):
        ids = b" ".join(str(index).encode() for index in range(1, len(self.raw_messages) + 1))
        return "OK", [ids]

    def fetch(self, mail_id, *_args):
        index = int(mail_id) - 1
        return "OK", [(mail_id, self.raw_messages[index])]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    payload = {
        "status": 1,
        "message": {
            "subject": "Binance verification code",
            "content": "Your verification code is 123456",
            "send_time_utc": "2026-04-26 08:16:15",
        },
    }

    def __init__(self):
        self.trust_env = True
        self.proxies = {"https": "http://127.0.0.1:8888"}
        self.closed = False
        self.requests = []

    def get(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        return _FakeResponse(self.payload)

    def close(self):
        self.closed = True


class _FakeOAuthImapClient:
    calls = []

    def __init__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.authenticated = False
        self.logged_out = False

    def authenticate(self, mechanism, callback):
        self.authenticated = True
        self.mechanism = mechanism
        self.payload = callback(None)

    def logout(self):
        self.logged_out = True


def _raw_email(*, sender: str, subject: str, body: str, date: str = "Wed, 08 Apr 2026 05:57:08 +0000") -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "alice@example.com"
    message["Date"] = date
    message["Subject"] = subject
    message.set_content(body)
    return message.as_bytes()


class EmailImapHistoricalLogicTests(unittest.TestCase):
    def test_subject_without_digits_is_not_treated_as_code(self) -> None:
        self.assertIsNone(email_imap._extract_6digit_code("Mesoc - Verification Code"))

    def test_body_keyword_context_extracts_six_digit_code(self) -> None:
        self.assertEqual(
            email_imap._extract_6digit_code("Your verification code is:\n643033"),
            "643033",
        )

    def test_body_without_keyword_context_is_not_treated_as_code(self) -> None:
        self.assertIsNone(email_imap._extract_6digit_code("Order 643033 was created successfully"))

    def test_message_extraction_reads_text_body_code(self) -> None:
        raw = _raw_email(
            sender='"Mesoc" <info1@mesoc.xyz>',
            subject="Mesoc - Verification Code",
            body="Your verification code is:\n643033\n",
        )

        import email

        message = email.message_from_bytes(raw)
        self.assertEqual(email_imap._extract_code_from_message(message), "643033")

    def test_get_email_verification_code_uses_historical_binance_sender_filter(self) -> None:
        raw_messages = [
            _raw_email(
                sender='"Mesoc" <info1@mesoc.xyz>',
                subject="Mesoc - Verification Code",
                body="Your verification code is:\n643033\n",
            ),
            _raw_email(
                sender='"Binance" <do-not-reply@binance.com>',
                subject="Binance verification code",
                body="Your verification code is:\n222333\n",
            ),
        ]

        with patch("binance_analyzer.integrations.email_imap.imap_connection", return_value=_FakeImapConnection(raw_messages)):
            code = email_imap.get_email_verification_code(
                "imap.firstmail.ltd",
                993,
                "alice@example.com",
                "pass",
                timeout=5,
                initial_count=0,
            )

        self.assertEqual(code, "222333")

    def test_four_part_account_password_extracts_login_password(self) -> None:
        self.assertEqual(
            email_imap.get_login_password("real-pass----client-id----refresh-token"),
            "real-pass",
        )
        self.assertEqual(email_imap.get_login_password("real-pass"), "real-pass")

    def test_four_part_outlook_account_uses_oauth_imap_instead_of_api(self) -> None:
        raw_messages = [
            _raw_email(
                sender='"Binance" <do-not-reply@binance.com>',
                subject="Binance verification code",
                body="Your verification code is 445566\n",
            ),
        ]
        account_tail = "real-pass----client-id----refresh-token"

        with (
            patch("binance_analyzer.integrations.email_imap.oauth_imap_connection", return_value=_FakeImapConnection(raw_messages)),
            patch("binance_analyzer.integrations.email_imap._fetch_outlook_code_via_api") as fetch_api,
        ):
            code = email_imap.get_email_verification_code(
                "outlook.office365.com",
                993,
                "alice@outlook.com",
                account_tail,
                timeout=5,
                initial_count=0,
            )

        self.assertEqual(code, "445566")
        fetch_api.assert_not_called()

    def test_oauth_imap_uses_microsoft_host_even_when_config_host_differs(self) -> None:
        _FakeOAuthImapClient.calls = []

        with (
            patch("binance_analyzer.integrations.email_imap._get_oauth_imap_access_token", return_value="access-token"),
            patch("binance_analyzer.integrations.email_imap.imaplib.IMAP4_SSL", _FakeOAuthImapClient),
        ):
            with email_imap.oauth_imap_connection(
                "imap.firstmail.ltd",
                993,
                "alice@outlook.com",
                "real-pass----client-id----refresh-token",
            ) as client:
                self.assertTrue(client.authenticated)
                self.assertEqual(client.mechanism, "XOAUTH2")

        args, _kwargs = _FakeOAuthImapClient.calls[0]
        self.assertEqual(args[0], email_imap.MICROSOFT_IMAP_HOST)
        self.assertEqual(args[1], email_imap.MICROSOFT_IMAP_PORT)

    def test_outlook_api_ignores_configured_proxy(self) -> None:
        fake_session = _FakeSession()

        with patch("binance_analyzer.integrations.email_imap.requests.Session", return_value=fake_session):
            code = email_imap._fetch_outlook_code_via_api(
                "alice@outlook.com",
                "pass",
                timeout=5,
                poll_interval=0,
            )

        self.assertEqual(code, "123456")
        self.assertFalse(fake_session.trust_env)
        self.assertEqual(fake_session.proxies, {})
        self.assertTrue(fake_session.closed)
        self.assertEqual(fake_session.requests[0][1]["params"], {"name": "alice@outlook.com", "pwd": "pass"})

    def test_hotmail_account_uses_microsoft_api(self) -> None:
        with patch("binance_analyzer.integrations.email_imap._fetch_outlook_code_via_api", return_value="123456") as fetch_api:
            code = email_imap.get_email_verification_code(
                "imap.firstmail.ltd",
                993,
                "alice@hotmail.com",
                "pass",
                timeout=5,
                initial_count=0,
            )

        self.assertEqual(code, "123456")
        fetch_api.assert_called_once()

    def test_microsoft_api_ignores_non_binance_mail_digits(self) -> None:
        fake_session = _FakeSession()
        fake_session.payload = {
            "status": 1,
            "message": {
                "subject": "Welcome! Your Microsoft account is here",
                "content": "Use your Microsoft account to access products. Color code 505050.",
                "send_time_utc": "2026-04-11 02:11:56",
            },
        }

        with (
            patch("binance_analyzer.integrations.email_imap.requests.Session", return_value=fake_session),
            patch("binance_analyzer.integrations.email_imap.time.time", side_effect=[0, 0, 2, 2]),
            patch("binance_analyzer.integrations.email_imap.time.sleep"),
        ):
            code = email_imap._fetch_outlook_code_via_api(
                "alice@hotmail.com",
                "pass",
                timeout=1,
                poll_interval=0,
            )

        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()

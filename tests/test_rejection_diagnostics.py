import unittest
import httpx
from arcus_mm.api import rejection_details

class RejectionDiagnostics(unittest.TestCase):
    def test_safe_categories_do_not_echo_payload(self):
        for error,category in [("accountIndex permission mismatch SECRET","account_or_subaccount_permission"),("temporarily banned SECRET","rate_limit_or_temporary_ban"),("API key revoked SECRET","api_key_status"),("invalid signature SECRET","signature_or_timestamp"),("unknown SECRET","unclassified_api_denial")]:
            r=rejection_details(httpx.Response(403,json={"error":error}))
            self.assertEqual(r["rejection_class"],category)
            self.assertNotIn("SECRET",str(r))
    def test_html_and_non_string_errors(self):
        self.assertEqual(rejection_details(httpx.Response(403,text="SECRET",headers={"content-type":"text/html"}))["response_kind"],"html")
        self.assertEqual(rejection_details(httpx.Response(403,json={"error":{"private":"SECRET"}}))["rejection_class"],"unclassified_api_denial")

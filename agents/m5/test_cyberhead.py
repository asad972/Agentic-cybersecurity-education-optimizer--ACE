"""Tests for the public CYBERHEAD programme client."""

import json
import unittest
from io import BytesIO
from unittest.mock import patch

import cyberhead


class CyberheadClientTests(unittest.TestCase):
    def test_fetches_all_pages_and_keeps_only_public_approved_rows(self) -> None:
        def result(row):
            return BytesIO(
                json.dumps(
                    {
                        "data": {
                            "programmes": {
                                "all_programmes": {
                                    "total": 2,
                                    "numpages": 2,
                                    "results": [row],
                                }
                            }
                        }
                    }
                ).encode()
            )

        approved = {"ID": "approved", "state": {"public": True, "state": "approved"}}
        pending = {"ID": "pending", "state": {"public": False, "state": "pending"}}
        responses = [
            BytesIO(b'<div id="app" data-protect="test-token"></div>'),
            result(approved),
            result(pending),
        ]
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            return responses.pop(0)

        with (
            patch.object(cyberhead, "_PAGE_SIZE", 1),
            patch.object(cyberhead, "urlopen", side_effect=fake_urlopen),
        ):
            programmes = cyberhead.fetch_public_programmes()

        self.assertEqual(programmes, [approved])
        self.assertEqual(len(requests), 3)
        self.assertTrue(requests[1].full_url.endswith("_authenticator=test-token"))
        first_page = json.loads(requests[1].data)["query"]
        second_page = json.loads(requests[2].data)["query"]
        self.assertIn("page: 1", first_page)
        self.assertIn("page: 2", second_page)


if __name__ == "__main__":
    unittest.main()

import unittest

from barricade.enums import ReportReasonFlag
from barricade.schemas import ReportSubmissionData


class ReportSubmissionReasonsTest(unittest.TestCase):
    def setUp(self):
        self.base_payload = {
            "token": "token",
            "players": [
                {
                    "playerId": "123",
                    "playerName": "Test Player",
                    "bmRconUrl": None,
                }
            ],
            "body": "Test body",
            "attachmentUrls": [],
        }

    def test_empty_other_reason_removed(self):
        payload = {
            **self.base_payload,
            "reasons": ["Toxicity / Harassment", ""],
        }

        submission = ReportSubmissionData.model_validate(payload)
        self.assertEqual(submission.reasons, ["Toxicity / Harassment"])

        flag, custom = ReportReasonFlag.from_list(submission.reasons)
        self.assertEqual(flag, ReportReasonFlag.TOXICITY_HARASSMENT)
        self.assertIsNone(custom)

    def test_whitespace_only_reason_removed(self):
        payload = {
            **self.base_payload,
            "reasons": ["   "],
        }

        submission = ReportSubmissionData.model_validate(payload)
        self.assertEqual(submission.reasons, [])

        flag, custom = ReportReasonFlag.from_list(submission.reasons)
        self.assertEqual(flag, ReportReasonFlag(0))
        self.assertIsNone(custom)


if __name__ == "__main__":
    unittest.main()

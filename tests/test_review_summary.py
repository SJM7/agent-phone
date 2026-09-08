import unittest

from agent_phone.review_summary import render_review_summary


class ReviewSummaryTests(unittest.TestCase):
    def test_links_bookmarks_clips_and_nearby_events(self):
        review = {
            "version": 1,
            "startedAt": 1_700_000_000_000,
            "endedAt": 1_700_000_065_000,
            "status": "complete",
            "reason": "Reviewer finished",
            "video": {"file": "review.webm", "bytes": 1234, "mimeType": "video/webm"},
            "events": [
                {"type": "click", "atMs": 9_000, "url": "https://example.test/page", "target": {"role": "button", "name": "Open menu"}},
                {"type": "scroll", "atMs": 31_000, "scroll": {"x": 0, "y": 420}},
                {"type": "keydown", "atMs": 64_000, "key": "Escape"},
            ],
            "bookmarks": [
                {"number": 1, "atMs": 10_000, "label": "Menu spacing", "frames": [{"file": "review-1-frame-1.png", "atMs": 9_500}]},
                {"number": 2, "atMs": 50_000, "label": "Dialog close", "frames": [{"file": "review-2-frame-1.png", "atMs": 50_500}]},
            ],
        }

        result = render_review_summary(review)

        self.assertTrue(result.startswith("# Agent Phone review\n"))
        self.assertIn("Status: complete", result)
        self.assertIn("Duration: 65.0s", result)
        self.assertIn("[review.webm](review.webm)", result)
        self.assertIn("## Bookmark 1 — Menu spacing (10.0s)", result)
        self.assertIn("Suggested video clip: 0.0s–50.0s", result)
        self.assertIn("![Frame at 9.5s](review-1-frame-1.png)", result)
        self.assertIn("click at 9.0s", result)
        first_bookmark = result.split("## Bookmark 2", 1)[0]
        self.assertNotIn("scroll at 31.0s", first_bookmark)
        self.assertIn("## Bookmark 2 — Dialog close (50.0s)", result)
        self.assertIn("Suggested video clip: 30.0s–65.0s", result)
        self.assertIn("keydown Escape at 64.0s", result)
        self.assertIn("state capture", result.lower())
        self.assertIn("exact word", result.lower())

    def test_escapes_untrusted_text_and_handles_no_video(self):
        review = {
            "version": 1,
            "startedAt": 10,
            "endedAt": 10,
            "status": "interrupted",
            "reason": "[do](https://bad.example) *now*",
            "video": None,
            "events": [{"type": "click", "atMs": 0, "target": {"name": "[unsafe](javascript:bad)"}}],
            "bookmarks": [{"number": 1, "atMs": 0, "label": "# heading [link](x)", "frames": []}],
        }

        result = render_review_summary(review)

        self.assertIn("Video: unavailable", result)
        self.assertIn("\\[do\\]\\(https://bad.example\\) \\*now\\*", result)
        self.assertIn("\\# heading \\[link\\]\\(x\\)", result)
        self.assertIn("\\[unsafe\\]\\(javascript:bad\\)", result)
        self.assertNotIn("](javascript:bad)", result)


if __name__ == "__main__":
    unittest.main()

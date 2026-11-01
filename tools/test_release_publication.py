"""Publication policy: qualified candidates remain downloadable without moving latest."""
import unittest
from release_publication import plan

class PublicationContracts(unittest.TestCase):
    def choose(self, **overrides):
        args=dict(version="1.2.3",event="pull_request",ref_type="branch",run_id="123",run_attempt="1",publish_requested=False,repository="SB0LTD/ramsql",head_repository="SB0LTD/ramsql")
        args.update(overrides)
        return plan(**args)

    def test_internal_pr_is_unique_prerelease(self):
        p=self.choose()
        self.assertEqual(p,{"tag":"ci-v1.2.3-run.123.1","prerelease":True,"publish_allowed":True,"candidate":True})
        self.assertFalse(p["tag"].startswith("v")) # candidate tags do not retrigger the v* matrix
        self.assertNotEqual(p["tag"],self.choose(run_attempt="2")["tag"])
        self.assertNotEqual(p["tag"],self.choose(run_id="124")["tag"])

    def test_development_candidate_preserves_source_version(self):
        self.assertEqual(self.choose(version="0.3.0-beta.2")["tag"],"ci-v0.3.0-beta.2-run.123.1")

    def test_manual_validation_also_publishes_candidate(self):
        self.assertTrue(self.choose(event="workflow_dispatch")["candidate"])
        self.assertTrue(self.choose(event="workflow_dispatch")["publish_allowed"])

    def test_explicit_manual_and_tag_releases_keep_normal_tags(self):
        for context in [dict(event="workflow_dispatch",publish_requested=True),dict(event="push",ref_type="tag")]:
            p=self.choose(**context)
            self.assertEqual(p["tag"],"v1.2.3")
            self.assertFalse(p["prerelease"])
            self.assertFalse(p["candidate"])
            self.assertTrue(self.choose(version="1.2.3-beta.1",**context)["prerelease"])

    def test_fork_pr_cannot_obtain_release_write_access(self):
        for head in ["someone/ramsql",""]:
            self.assertFalse(self.choose(head_repository=head)["publish_allowed"])

    def test_unexpected_events_and_invalid_identifiers_fail_closed(self):
        for context in [dict(event="push",ref_type="branch"),dict(event="pull_request_target"),dict(run_id="../tag"),dict(run_attempt="0"),dict(version="1.2.3\nBAD=true")]:
            with self.subTest(context=context),self.assertRaises(ValueError):self.choose(**context)

if __name__=="__main__":unittest.main()

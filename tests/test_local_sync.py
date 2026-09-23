"""Delivery checks actual Git inclusion and the provider's applied opt-out."""

import unittest

from test_source_selection import commit, git
from tollgate_fixture import CANDIDATE, oid, provider_fixture, reply, status

from hive.errors import ErrorCode, HiveError
from hive.identity import SourceCommit
from hive.jsonvalue import record


class LocalSyncTests(unittest.TestCase):
    def test_integrated_commit_must_reach_master_and_later_commits_are_allowed(
        self,
    ) -> None:
        with provider_fixture() as fixture:
            (fixture.workspace / "feature.txt").write_text("Reviewed source\n")
            source = SourceCommit(commit(fixture.workspace, "feat: reviewed source"))
            git(fixture.repository, "merge", "--ff-only", source)
            (fixture.workspace / "integration.txt").write_text("Integrated result\n")
            tested = SourceCommit(commit(fixture.workspace, "feat: integrated result"))
            snapshot = status(source)
            snapshot["generation"] = {
                **record(snapshot["generation"]),
                "tested_oid": oid(tested),
            }
            fixture.configure(
                wait=reply(snapshot),
                status=reply(snapshot),
                config=reply({"sync_user_master": True}),
            )
            with self.assertRaises(HiveError) as missing:
                fixture.provider.wait(CANDIDATE)
            self.assertEqual(missing.exception.code, ErrorCode.SYNCHRONIZATION_REQUIRED)

            git(fixture.repository, "merge", "--ff-only", tested)
            fixture.provider.wait(CANDIDATE)
            (fixture.repository / "later.txt").write_text("Later unrelated work\n")
            commit(fixture.repository, "docs: later work")
            fixture.provider.wait(CANDIDATE)

            # History is neither required nor trusted as a substitute for the
            # actual configured branch. Rewinding it makes delivery unresolved.
            git(fixture.repository, "reset", "--hard", source)
            with self.assertRaises(HiveError) as rewound:
                fixture.provider.wait(CANDIDATE)
            self.assertEqual(rewound.exception.code, ErrorCode.SYNCHRONIZATION_REQUIRED)

    def test_disabled_sync_requires_applied_candidate_policy(self) -> None:
        with provider_fixture() as fixture:
            (fixture.workspace / "feature.txt").write_text("Local sync disabled\n")
            source = SourceCommit(commit(fixture.workspace, "feat: disabled sync"))
            snapshot = status(source)
            (fixture.repository / ".tollgate/config.toml").write_text(
                "sync_user_master = false\n"
            )
            for generation_policy, active_policy, repository, expected in (
                (
                    "native-policy-1",
                    "not-applied",
                    "project-1",
                    ErrorCode.UNRESOLVED_OUTCOME,
                ),
                (
                    "older-policy",
                    "native-policy-1",
                    "project-1",
                    ErrorCode.UNRESOLVED_OUTCOME,
                ),
                (
                    "native-policy-1",
                    "native-policy-1",
                    "other-project",
                    ErrorCode.INVALID_INPUT,
                ),
                ("native-policy-1", "native-policy-1", "project-1", None),
            ):
                with self.subTest(
                    generation=generation_policy,
                    active=active_policy,
                    repository=repository,
                ):
                    snapshot["generation"] = {
                        **record(snapshot["generation"]),
                        "configuration_digest": generation_policy,
                    }
                    fixture.configure(
                        wait=reply(snapshot),
                        status=reply(snapshot),
                        config=reply(
                            {"sync_user_master": False, "digest": "native-policy-1"}
                        ),
                        repository=reply(
                            {
                                "state": {
                                    "id": repository,
                                    "path": str(fixture.repository),
                                    "active_configuration_digest": active_policy,
                                }
                            }
                        ),
                    )
                    if expected is None:
                        fixture.provider.wait(CANDIDATE)
                    else:
                        with self.assertRaises(HiveError) as refused:
                            fixture.provider.wait(CANDIDATE)
                        self.assertEqual(refused.exception.code, expected)
            self.assertEqual(
                git(fixture.repository, "rev-parse", "master"), fixture.source
            )

    def test_missing_or_mismatched_generation_never_proves_sync(self) -> None:
        with provider_fixture() as fixture:
            for generation in (
                None,
                {
                    "id": "wrong",
                    "item_id": CANDIDATE,
                    "tested_oid": oid(fixture.source),
                },
                {
                    "id": "generation-1",
                    "item_id": "another-candidate",
                    "tested_oid": oid(fixture.source),
                },
            ):
                snapshot = {**status(fixture.source), "generation": generation}
                fixture.configure(
                    wait=reply(status(fixture.source)), status=reply(snapshot)
                )
                with self.assertRaises(HiveError) as refused:
                    fixture.provider.wait(CANDIDATE)
                self.assertEqual(refused.exception.code, ErrorCode.INVALID_RECORD)
            changed = status(fixture.source, "ready")
            fixture.configure(wait=reply(status(fixture.source)), status=reply(changed))
            with self.assertRaises(HiveError) as not_promoted:
                fixture.provider.wait(CANDIDATE)
            self.assertEqual(not_promoted.exception.code, ErrorCode.UNRESOLVED_OUTCOME)

    def test_foreign_candidate_cannot_deliver_approve_or_settle_shared_source(
        self,
    ) -> None:
        with provider_fixture() as fixture:
            snapshot = status(fixture.source, authorized=False)
            snapshot["item"] = {
                **record(snapshot["item"]),
                "repository_id": "another-project",
            }
            fixture.configure(wait=reply(snapshot), status=reply(snapshot))
            # A fork may share the exact commit already in local master.
            # That does not make its globally resolved candidate ours.
            for action in (
                lambda: fixture.provider.wait(CANDIDATE),
                lambda: fixture.provider.approve(CANDIDATE, fixture.source),
                lambda: fixture.provider.inspect_settled(CANDIDATE, fixture.source),
            ):
                with self.assertRaises(HiveError) as foreign:
                    action()
                self.assertEqual(foreign.exception.code, ErrorCode.INVALID_INPUT)
            self.assertFalse(
                any(call["args"] == ["approve", CANDIDATE] for call in fixture.calls())
            )

    def test_native_fallback_or_parent_registration_cannot_authorize_other_work(
        self,
    ) -> None:
        with provider_fixture() as fixture:
            snapshot = status(fixture.source, authorized=False)
            for path in (fixture.root / "foreign-project", fixture.root):
                fixture.configure(
                    wait=reply(snapshot),
                    status=reply(snapshot),
                    repository=reply(
                        {
                            "state": {
                                "id": "project-1",
                                "path": str(path),
                                "active_configuration_digest": "native-policy-1",
                            }
                        }
                    ),
                )
                for action in (
                    lambda: fixture.provider.wait(CANDIDATE),
                    lambda: fixture.provider.approve(CANDIDATE, fixture.source),
                    lambda: fixture.provider.inspect_settled(CANDIDATE, fixture.source),
                ):
                    with self.assertRaises(HiveError) as foreign:
                        action()
                    self.assertEqual(foreign.exception.code, ErrorCode.INVALID_INPUT)
            self.assertFalse(
                any(call["args"] == ["approve", CANDIDATE] for call in fixture.calls())
            )

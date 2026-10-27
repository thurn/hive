# Cold review and disposition

Fresh read-only same-host subagent `/root/observation_progress_warden` used the warden skill against base `6a25dad92bf1a2cafe13046290622ea700ce32a1`; no author conversation or edits.

One confirmed P2: starting repository polling before bead history is ready can cache missing branch matches for five minutes, so later caught-up history cannot immediately repair the fixture's fallback attribution. The reviewer demonstrated the path by failing the first history refresh, with all later observation code real.

Fixed: repository polling starts only after history is caught up. A disposable external Beads CLI wrapper fails its first read then delegates to the real CLI, giving a black-box regression. Before the fix the regression fails with empty FALLBACK links in 4.296 s; after the fix all four module tests pass in 4.966 s. Every original ownership/identity/branch/promotion/cache/outage assertion and the native timeout guard remains. Production code is unchanged. No other findings and no confirmation review.

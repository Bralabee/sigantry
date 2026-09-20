"""Throwaway: deliberately failing test used to prove the branch-protection
gate BLOCKS a merge. Deleted immediately after the observation."""


def test_deliberately_failing_probe() -> None:
    assert False, "intentional failure: proving required status checks block merges"

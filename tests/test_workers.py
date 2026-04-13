from bot_workers import subscription_migration_mark_bucket


def test_subscription_migration_mark_bucket_rounds_by_hours() -> None:
    now_ts = 1_800_000
    assert subscription_migration_mark_bucket(now_ts=now_ts, cooldown_hours=24) == 1_728_000


def test_subscription_migration_mark_bucket_minimum_window() -> None:
    now_ts = 7_200
    assert subscription_migration_mark_bucket(now_ts=now_ts, cooldown_hours=0) == 7_200

import bot


def test_parse_plans_json_defaults_to_single_plan() -> None:
    plans = bot._parse_plans_json("", default_days=30, default_gb=0, default_rub=99.0)
    assert len(plans) == 1
    assert plans[0].days == 30
    assert plans[0].gb == 0
    assert abs(plans[0].rub - 99.0) < 1e-9


def test_parse_plans_json_accepts_custom_plans() -> None:
    raw = (
        '[{"key":"m1","title":"M1","days":30,"gb":0,"rub":99},'
        '{"key":"m3","title":"M3","days":90,"gb":0,"rub":259}]'
    )
    plans = bot._parse_plans_json(raw, default_days=30, default_gb=0, default_rub=99.0)
    assert [p.key for p in plans] == ["m1", "m3"]
    assert [p.days for p in plans] == [30, 90]
    assert [p.rub for p in plans] == [99.0, 259.0]

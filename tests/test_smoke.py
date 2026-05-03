from pathlib import Path

import pandas as pd

import corr


def test_load_fixture_roundtrip(tmp_path: Path) -> None:
    fixture_name = "spike_fixture.csv"

    available = corr.available_fixtures()
    assert fixture_name in available

    df = corr.load_fixture(fixture_name)
    assert not df.empty
    assert set(df.columns) == {"timestamp", "spike_count", "region"}

    parquet = corr.load_fixture("spike_fixture.parquet")
    assert isinstance(parquet, pd.DataFrame)
    assert len(parquet) == len(df)

    # Ensure fixtures stay small; enforce max 32 rows for quick runs.
    assert len(df) <= 32

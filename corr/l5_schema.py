from __future__ import annotations

import pyarrow as pa

CANONICAL_FIELDS = [
    pa.field("system", pa.string(), nullable=False),
    pa.field("year", pa.int64(), nullable=False),
    pa.field("prn", pa.string(), nullable=False),
    pa.field("severity", pa.string(), nullable=False),
    pa.field("max_epoch_severity", pa.string(), nullable=False),
    pa.field("epoch_count", pa.int64(), nullable=False),
    pa.field("promoted", pa.bool_(), nullable=False),
    pa.field("epoch_seconds", pa.int64(), nullable=False),
    pa.field("start_utc", pa.timestamp("ns"), nullable=False),
    pa.field("end_utc", pa.timestamp("ns"), nullable=False),
    pa.field("duration_s", pa.float64(), nullable=False),
    pa.field("peak_ns", pa.float64(), nullable=False),
    pa.field("mean_ns", pa.float64(), nullable=False),
    pa.field("sigma_ns", pa.float64(), nullable=False),
    pa.field("z_max", pa.float64(), nullable=False),
]

CANONICAL_SCHEMA = pa.schema(CANONICAL_FIELDS)


def ensure_schema(table: pa.Table) -> pa.Table:
    """Cast a table to the canonical schema, adding missing columns as null."""
    columns = table.column_names
    arrays = []
    for field in CANONICAL_SCHEMA:
        if field.name in columns:
            arrays.append(table[field.name].cast(field.type, safe=False))
        else:
            arrays.append(pa.nulls(len(table), type=field.type))
    if len(table) == 0:
        return pa.Table.from_arrays(
            [pa.array([], type=field.type) for field in CANONICAL_SCHEMA],
            names=[field.name for field in CANONICAL_SCHEMA],
        )
    return pa.Table.from_arrays(arrays, names=[f.name for f in CANONICAL_SCHEMA])


__all__ = ["CANONICAL_SCHEMA", "ensure_schema"]

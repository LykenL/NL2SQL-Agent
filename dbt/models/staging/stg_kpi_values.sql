with source as (
    select * from {{ source('bronze', 'kolada_kpi_values') }}
)

select
    kpi_id,
    period as year,
    municipality_code,
    gender,
    cast(value as float64) as value,
    status,
    is_deleted,
    ingested_at
from source
where value is not null
qualify row_number() over (
    partition by kpi_id, period, municipality_code, gender
    order by ingested_at desc
) = 1

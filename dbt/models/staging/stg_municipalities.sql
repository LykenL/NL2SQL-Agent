with source as (
    select * from {{ source('bronze', 'kolada_municipalities') }}
)

select
    municipality_code,
    trim(municipality_name) as municipality_name,
    case municipality_type
        when 'K' then 'municipality'
        when 'L' then 'region'
        else 'other'
    end as region_type,
    ingested_at
from source
qualify row_number() over (partition by municipality_code order by ingested_at desc) = 1

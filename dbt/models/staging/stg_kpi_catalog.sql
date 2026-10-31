select
    kpi_id,
    title,
    description,
    operating_area,
    is_divided_by_gender
from {{ source('bronze', 'kolada_kpi_catalog') }}

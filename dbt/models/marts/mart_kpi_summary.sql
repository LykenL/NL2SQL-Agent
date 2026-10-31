{{ config(materialized='table') }}

select
    v.kpi_id,
    c.title as kpi_title,
    v.year,
    m.region_type,
    count(distinct v.municipality_code) as municipalities_reporting,
    avg(v.value) as avg_value,
    sum(v.value) as sum_value,
    min(v.value) as min_value,
    max(v.value) as max_value,
    current_timestamp() as _loaded_at
from {{ ref('stg_kpi_values') }} v
join {{ ref('stg_municipalities') }} m using (municipality_code)
left join {{ ref('stg_kpi_catalog') }} c using (kpi_id)
where v.gender = 'T'
group by 1, 2, 3, 4

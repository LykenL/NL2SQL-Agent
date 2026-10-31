{{ config(materialized='table') }}

with cost as (
    select
        municipality_code,
        year,
        value as cost_per_capita_sek
    from {{ ref('stg_kpi_values') }}
    where kpi_id = 'N00003' and gender = 'T'
)

select
    concat(c.municipality_code, '-', cast(c.year as string)) as region_year_key,
    c.municipality_code,
    p.municipality_name,
    p.region_type,
    c.year,
    p.population,
    c.cost_per_capita_sek,
    c.cost_per_capita_sek * p.population as estimated_total_cost_sek,
    current_timestamp() as _loaded_at
from cost c
join {{ ref('mart_municipality_population') }} p
  using (municipality_code, year)

{{ config(materialized='table') }}

-- NB: do not name this CTE `population` — BigQuery then resolves
-- `lag(population)` to the CTE row (a STRUCT) instead of the column.
with pop_by_year as (
    select
        municipality_code,
        year,
        value as population
    from {{ ref('stg_kpi_values') }}
    where kpi_id = 'N01951' and gender = 'T'
),

with_previous as (
    select
        municipality_code,
        year,
        population,
        lag(population) over (partition by municipality_code order by year) as population_prev_year
    from pop_by_year
)

select
    concat(m.municipality_code, '-', cast(w.year as string)) as region_year_key,
    w.municipality_code,
    m.municipality_name,
    m.region_type,
    w.year,
    w.population,
    w.population_prev_year,
    safe_divide(w.population - w.population_prev_year, w.population_prev_year) as yoy_pct,
    current_timestamp() as _loaded_at
from with_previous w
join {{ ref('stg_municipalities') }} m using (municipality_code)

{{ config(materialized='table', schema='meta') }}

-- One row per published mart: when it last loaded and how many rows it holds.
-- The API and the Data Health page read this table instead of scanning the marts.
select 'mart_municipality_population' as table_name,
       max(_loaded_at) as last_loaded,
       count(*) as row_count
from {{ ref('mart_municipality_population') }}

union all

select 'mart_personnel_cost' as table_name,
       max(_loaded_at) as last_loaded,
       count(*) as row_count
from {{ ref('mart_personnel_cost') }}

union all

select 'mart_kpi_summary' as table_name,
       max(_loaded_at) as last_loaded,
       count(*) as row_count
from {{ ref('mart_kpi_summary') }}

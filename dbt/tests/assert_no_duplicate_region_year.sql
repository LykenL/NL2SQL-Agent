-- Fails if a (municipality, year) pair appears more than once.
select region_year_key, count(*) as n
from {{ ref('mart_municipality_population') }}
group by 1
having count(*) > 1

-- Fails if any population value is negative.
select *
from {{ ref('mart_municipality_population') }}
where population < 0

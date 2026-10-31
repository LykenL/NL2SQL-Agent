from dagster import Definitions, load_assets_from_modules

from pipelines.assets import dq, kolada
from pipelines.checks import dq_checks

defs = Definitions(assets=load_assets_from_modules([kolada, dq, dq_checks]))

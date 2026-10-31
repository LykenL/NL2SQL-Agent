{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set prefix = env_var('BQ_PREFIX', 'data_copilot_dev') -%}
    {%- if custom_schema_name is none -%}
        {{ prefix }}_gold
    {%- else -%}
        {{ prefix }}_{{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}

CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.f_td_add_months')(d DATE, n INT)
RETURNS DATE
DETERMINISTIC
RETURN add_months(d, n);

CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.f_month_start')(d DATE)
RETURNS DATE
DETERMINISTIC
RETURN trunc(d, 'MONTH');

CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.f_month_end')(d DATE)
RETURNS DATE
DETERMINISTIC
RETURN last_day(d);

CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.f_decimal_2')(v DECIMAL(38,6))
RETURNS DECIMAL(18,2)
DETERMINISTIC
RETURN CAST(round(v, 2) AS DECIMAL(18,2));

CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.f_zeroifnull')(v DECIMAL(38,2))
RETURNS DECIMAL(38,2)
DETERMINISTIC
RETURN coalesce(v, CAST(0 AS DECIMAL(38,2)));

CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.f_nullifzero')(v DECIMAL(38,2))
RETURNS DECIMAL(38,2)
DETERMINISTIC
RETURN nullif(v, CAST(0 AS DECIMAL(38,2)));

CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.f_trim')(s STRING)
RETURNS STRING
DETERMINISTIC
RETURN trim(s);

CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.f_clean_text')(s STRING)
RETURNS STRING
DETERMINISTIC
RETURN trim(replace(s, chr(160), ' '));

CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.f_parse_amount')(s STRING)
RETURNS DECIMAL(18,2)
DETERMINISTIC
RETURN try_cast(replace(trim(s), ',', '.') AS DECIMAL(18,2));

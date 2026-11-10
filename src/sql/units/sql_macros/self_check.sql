WITH cases(d, n, expected) AS (
  VALUES (DATE '2025-06-30', -6, DATE '2024-12-30'),
         (DATE '2024-02-29', 1, DATE '2024-03-29'),
         (DATE '2025-01-30', 1, DATE '2025-02-28'),
         (DATE '2025-03-15', -1, DATE '2025-02-15'),
         (DATE '2025-06-30', 0, DATE '2025-06-30'),
         (CAST(NULL AS DATE), 1, CAST(NULL AS DATE)),
         (DATE '2025-01-01', CAST(NULL AS INT), CAST(NULL AS DATE))
), checked AS (
  SELECT expected, IDENTIFIER(:catalog || '.' || :silver_schema || '.f_td_add_months')(d, n) AS actual
  FROM cases
)
SELECT CASE WHEN count(*) = 7 AND count_if(NOT (actual <=> expected)) = 0
            THEN 'PASS f_td_add_months'
            ELSE raise_error('f_td_add_months semantics drift') END AS self_check
FROM checked;

WITH cases(d, expected) AS (
  VALUES (DATE '2024-02-29', DATE '2024-02-01'),
         (DATE '2025-12-31', DATE '2025-12-01'),
         (CAST(NULL AS DATE), CAST(NULL AS DATE))
), checked AS (
  SELECT expected, IDENTIFIER(:catalog || '.' || :silver_schema || '.f_month_start')(d) AS actual
  FROM cases
)
SELECT CASE WHEN count(*) = 3 AND count_if(NOT (actual <=> expected)) = 0
            THEN 'PASS f_month_start'
            ELSE raise_error('f_month_start semantics drift') END AS self_check
FROM checked;

WITH cases(d, expected) AS (
  VALUES (DATE '2024-02-01', DATE '2024-02-29'),
         (DATE '2025-02-01', DATE '2025-02-28'),
         (DATE '2025-06-30', DATE '2025-06-30'),
         (CAST(NULL AS DATE), CAST(NULL AS DATE))
), checked AS (
  SELECT expected, IDENTIFIER(:catalog || '.' || :silver_schema || '.f_month_end')(d) AS actual
  FROM cases
)
SELECT CASE WHEN count(*) = 4 AND count_if(NOT (actual <=> expected)) = 0
            THEN 'PASS f_month_end'
            ELSE raise_error('f_month_end semantics drift') END AS self_check
FROM checked;

WITH cases(v, expected) AS (
  VALUES (CAST(1.005 AS DECIMAL(38,6)), CAST(1.01 AS DECIMAL(18,2))),
         (CAST(-1.005 AS DECIMAL(38,6)), CAST(-1.01 AS DECIMAL(18,2))),
         (CAST(0 AS DECIMAL(38,6)), CAST(0 AS DECIMAL(18,2))),
         (CAST(NULL AS DECIMAL(38,6)), CAST(NULL AS DECIMAL(18,2)))
), checked AS (
  SELECT expected, IDENTIFIER(:catalog || '.' || :silver_schema || '.f_decimal_2')(v) AS actual
  FROM cases
)
SELECT CASE WHEN count(*) = 4 AND count_if(NOT (actual <=> expected)) = 0
            THEN 'PASS f_decimal_2'
            ELSE raise_error('f_decimal_2 semantics drift') END AS self_check
FROM checked;

WITH cases(v, expected) AS (
  VALUES (CAST(NULL AS DECIMAL(38,2)), CAST(0 AS DECIMAL(38,2))),
         (CAST(-2.25 AS DECIMAL(38,2)), CAST(-2.25 AS DECIMAL(38,2))),
         (CAST(0 AS DECIMAL(38,2)), CAST(0 AS DECIMAL(38,2)))
), checked AS (
  SELECT expected, IDENTIFIER(:catalog || '.' || :silver_schema || '.f_zeroifnull')(v) AS actual
  FROM cases
)
SELECT CASE WHEN count(*) = 3 AND count_if(NOT (actual <=> expected)) = 0
            THEN 'PASS f_zeroifnull'
            ELSE raise_error('f_zeroifnull semantics drift') END AS self_check
FROM checked;

WITH cases(v, expected) AS (
  VALUES (CAST(0 AS DECIMAL(38,2)), CAST(NULL AS DECIMAL(38,2))),
         (CAST(NULL AS DECIMAL(38,2)), CAST(NULL AS DECIMAL(38,2))),
         (CAST(-3 AS DECIMAL(38,2)), CAST(-3 AS DECIMAL(38,2))),
         (CAST(2.25 AS DECIMAL(38,2)), CAST(2.25 AS DECIMAL(38,2)))
), checked AS (
  SELECT expected, IDENTIFIER(:catalog || '.' || :silver_schema || '.f_nullifzero')(v) AS actual
  FROM cases
)
SELECT CASE WHEN count(*) = 4 AND count_if(NOT (actual <=> expected)) = 0
            THEN 'PASS f_nullifzero'
            ELSE raise_error('f_nullifzero semantics drift') END AS self_check
FROM checked;

WITH cases(s, expected) AS (
  VALUES ('  CARD  ', 'CARD'),
         ('', ''),
         ('   ', ''),
         (CAST(NULL AS STRING), CAST(NULL AS STRING))
), checked AS (
  SELECT expected, IDENTIFIER(:catalog || '.' || :silver_schema || '.f_trim')(s) AS actual
  FROM cases
)
SELECT CASE WHEN count(*) = 4 AND count_if(NOT (actual <=> expected)) = 0
            THEN 'PASS f_trim'
            ELSE raise_error('f_trim semantics drift') END AS self_check
FROM checked;

WITH cases(s, expected) AS (
  VALUES (concat(chr(160), 'O', chr(39), 'Brien', chr(160)), concat('O', chr(39), 'Brien')),
         ('A  B', 'A  B'),
         ('', ''),
         (CAST(NULL AS STRING), CAST(NULL AS STRING))
), checked AS (
  SELECT expected, IDENTIFIER(:catalog || '.' || :silver_schema || '.f_clean_text')(s) AS actual
  FROM cases
)
SELECT CASE WHEN count(*) = 4 AND count_if(NOT (actual <=> expected)) = 0
            THEN 'PASS f_clean_text'
            ELSE raise_error('f_clean_text semantics drift') END AS self_check
FROM checked;

WITH cases(s, expected) AS (
  VALUES (' -1,25 ', CAST(-1.25 AS DECIMAL(18,2))),
         ('1.005', CAST(1.01 AS DECIMAL(18,2))),
         ('', CAST(NULL AS DECIMAL(18,2))),
         ('bad', CAST(NULL AS DECIMAL(18,2))),
         ('99999999999999999', CAST(NULL AS DECIMAL(18,2))),
         (CAST(NULL AS STRING), CAST(NULL AS DECIMAL(18,2)))
), checked AS (
  SELECT expected, IDENTIFIER(:catalog || '.' || :silver_schema || '.f_parse_amount')(s) AS actual
  FROM cases
)
SELECT CASE WHEN count(*) = 6 AND count_if(NOT (actual <=> expected)) = 0
            THEN 'PASS f_parse_amount'
            ELSE raise_error('f_parse_amount semantics drift') END AS self_check
FROM checked;

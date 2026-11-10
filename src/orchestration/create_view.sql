SET ANSI_MODE = true;
DECLARE OR REPLACE VARIABLE _nightly_date DATE;
DECLARE OR REPLACE VARIABLE _nightly_batch BIGINT;
SET VAR (_nightly_date, _nightly_batch) = (
  SELECT delivery.business_date, delivery.batch_id
  FROM (SELECT element_at(from_json(:manifest_drops,
    'MAP<STRING, STRUCT<batch_id: BIGINT, business_date: DATE, counts: MAP<STRING, BIGINT>>>'), :drop) AS delivery)
);
SELECT CASE WHEN _nightly_date IS NOT NULL AND _nightly_batch IS NOT NULL
            THEN 'Valid captured drop'
            ELSE raise_error(concat('Unknown captured drop: ', coalesce(:drop, '<NULL>'))) END;

EXECUTE IMMEDIATE replace(replace(replace(:view_sql,
  '{silver}', '`' || replace(:catalog, '`', '``') || '`.`' || replace(:silver_schema, '`', '``') || '`'),
  '{gold}', '`' || replace(:catalog, '`', '``') || '`.`' || replace(:gold_schema, '`', '``') || '`'),
  '{business_date}', CAST(_nightly_date AS STRING));

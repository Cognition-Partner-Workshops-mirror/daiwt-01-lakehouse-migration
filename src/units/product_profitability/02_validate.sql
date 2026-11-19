SELECT CASE WHEN COUNT(*) = 0 THEN 1
       ELSE CAST('Invalid product rate or mean balance' AS INT) END
FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.product_income')
WHERE annual_rate IS NULL OR annual_rate < 0 OR precise_balance IS NULL;

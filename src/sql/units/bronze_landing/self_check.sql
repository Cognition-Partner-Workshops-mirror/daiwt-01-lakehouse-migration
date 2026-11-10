DECLARE OR REPLACE VARIABLE _landing_batch_id BIGINT;
DECLARE OR REPLACE VARIABLE _landing_business_date DATE;
DECLARE OR REPLACE VARIABLE _landing_counts MAP<STRING, BIGINT>;
SET VAR (_landing_batch_id, _landing_business_date, _landing_counts) = (
  SELECT delivery.batch_id, delivery.business_date, delivery.counts
  FROM (
    SELECT element_at(from_json(:manifest_drops,
      'MAP<STRING, STRUCT<batch_id: BIGINT, business_date: DATE, counts: MAP<STRING, BIGINT>>>'),
      :drop) AS delivery
  )
);
SELECT CASE WHEN _landing_batch_id IS NOT NULL AND _landing_business_date IS NOT NULL
                 AND size(_landing_counts) = 11
            THEN 'Valid captured drop'
            ELSE raise_error(concat('Unknown captured drop or invalid manifest metadata: ', coalesce(:drop, '<NULL>'))) END;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_edw.dim_account')
                 AND count_if(account_id IS NULL) = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS edw_dim_account'
            ELSE raise_error('edw_dim_account: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_account') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_edw.dim_branch')
                 AND count_if(branch_id IS NULL) = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS edw_dim_branch'
            ELSE raise_error('edw_dim_branch: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_branch') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_edw.dim_calendar')
                 AND count_if(calendar_date IS NULL) = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS edw_dim_calendar'
            ELSE raise_error('edw_dim_calendar: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_calendar') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_edw.dim_card')
                 AND count_if(card_id IS NULL) = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS edw_dim_card'
            ELSE raise_error('edw_dim_card: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_card') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_edw.dim_merchant')
                 AND count_if(merchant_id IS NULL) = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS edw_dim_merchant'
            ELSE raise_error('edw_dim_merchant: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_merchant') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_edw.dim_product')
                 AND count_if(product_id IS NULL) = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS edw_dim_product'
            ELSE raise_error('edw_dim_product: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_product') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_edw.fct_daily_balance')
                 AND count_if(account_id IS NULL OR balance_date IS NULL) = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS edw_fct_daily_balance'
            ELSE raise_error('edw_fct_daily_balance: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_daily_balance') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_edw.fct_loan')
                 AND count_if(loan_id IS NULL) = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS edw_fct_loan'
            ELSE raise_error('edw_fct_loan: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_loan') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_edw.fct_product_order')
                 AND count_if(order_id IS NULL) = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS edw_fct_product_order'
            ELSE raise_error('edw_fct_product_order: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_product_order') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_stg.customer_feed')
                 AND count_if(customer_id IS NULL OR customer_id = '') = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS stg_customer_feed'
            ELSE raise_error('stg_customer_feed: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_customer_feed') WHERE _drop = :drop;

SELECT CASE WHEN count(*) = element_at(_landing_counts, 'nvb_stg.transaction_feed')
                 AND count_if(transaction_id IS NULL OR transaction_id = '') = 0
                 AND count_if(_drop IS NULL OR _batch_id IS NULL OR _business_date IS NULL
                   OR _source_file IS NULL OR _source_file = '' OR _loaded_at IS NULL
                   OR _batch_id <> _landing_batch_id
                   OR _business_date <> _landing_business_date) = 0
            THEN 'PASS stg_transaction_feed'
            ELSE raise_error('stg_transaction_feed: row count, keys or lineage failed') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_transaction_feed') WHERE _drop = :drop;

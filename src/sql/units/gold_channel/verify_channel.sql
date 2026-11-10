SELECT CASE WHEN m.rows > 0 AND m.payments = t.payments AND m.net_amount <=> t.net_amount
            THEN concat('PASS mart_channel_daily reconciles: ', m.rows, ' rows, ', m.payments, ' payments')
            ELSE raise_error('mart_channel_daily totals differ from fct_transaction') END AS verification
FROM (SELECT count(*) AS rows, sum(txn_count) AS payments, sum(net_amount) AS net_amount
      FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')) AS m
CROSS JOIN (SELECT count(*) AS payments, sum(amount) AS net_amount
            FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction')) AS t;

SELECT CASE WHEN count(*) = 0 THEN 'PASS one row per txn_date and channel'
            ELSE raise_error('mart_channel_daily has duplicate txn_date/channel keys') END AS verification
FROM (SELECT txn_date, channel FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
      GROUP BY 1, 2 HAVING count(*) > 1);

SELECT channel, sum(txn_count) AS payments, sum(net_amount) AS net_amount,
       sum(net_amount) / nullif(sum(txn_count), 0) AS average_payment
FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
GROUP BY 1
ORDER BY 1;

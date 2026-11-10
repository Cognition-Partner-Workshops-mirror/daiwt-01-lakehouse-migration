CREATE OR REPLACE VIEW {gold}.v_channel_trend AS
SELECT d.calendar_date AS txn_date, d.year_no, d.month_no, d.month_start,
       'MOBILE' AS channel, coalesce(m.txn_count, 0) AS txn_count,
       coalesce(m.net_amount, 0) AS net_amount,
       sum(coalesce(m.net_amount, 0)) OVER (
         ORDER BY d.calendar_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_amount,
       avg(coalesce(m.net_amount, 0)) OVER (
         ORDER BY d.calendar_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS seven_day_average,
       coalesce(m.net_amount, 0) / nullif(m.txn_count, 0) AS average_payment,
       CASE WHEN m.txn_count IS NULL THEN 'NO_ACTIVITY' ELSE 'CLEARED' END AS activity_status
FROM {silver}.dim_calendar AS d
LEFT JOIN {gold}.mart_channel_daily AS m ON d.calendar_date = m.txn_date AND m.channel = 'MOBILE'
WHERE d.calendar_date BETWEEN (SELECT min(calendar_date) FROM {silver}.dim_calendar)
                          AND DATE '{business_date}'

CREATE OR REPLACE VIEW {gold}.report_risk_arrears AS
SELECT customer_id, exposure, impaired_ratio FROM {gold}.v_credit_risk
WHERE impaired_ratio > 0 ORDER BY exposure DESC

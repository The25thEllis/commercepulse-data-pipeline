-- ============================================================
-- CommercePulse Analytics Queries
-- Covering all 6 required analytics capabilities
-- ============================================================


-- ------------------------------------------------------------
-- Query 1: Daily Gross vs Net Revenue
-- ------------------------------------------------------------
SELECT
    order_date,
    total_orders,
    ROUND(gross_revenue, 2)                         AS gross_revenue,
    ROUND(total_refunds, 2)                         AS total_refunds,
    ROUND(net_revenue, 2)                           AS net_revenue
FROM `commercepulse-488300.commercepulse.fact_order_daily`
ORDER BY order_date;


-- ------------------------------------------------------------
-- Query 2: Payment Success Rate by Vendor
-- ------------------------------------------------------------
SELECT
    vendor,
    COUNT(*)                                        AS total_payments,
    COUNTIF(status = 'SUCCESS')                     AS successful_payments,
    COUNTIF(status = 'FAILED')                      AS failed_payments,
    ROUND(
        COUNTIF(status = 'SUCCESS') / COUNT(*) * 100
    , 2)                                            AS success_rate_pct
FROM `commercepulse-488300.commercepulse.fact_payments`
WHERE status IS NOT NULL
GROUP BY vendor
ORDER BY success_rate_pct ASC;


-- ------------------------------------------------------------
-- Query 3: Average Time from Order Creation to Payment
-- ------------------------------------------------------------
SELECT
    o.vendor,
    COUNT(*)                                        AS total_orders,
    ROUND(AVG(
        TIMESTAMP_DIFF(
            TIMESTAMP(p.paid_at),
            TIMESTAMP(o.order_time),
            HOUR
        )
    ), 2)                                           AS avg_hours_to_payment
FROM `commercepulse-488300.commercepulse.fact_orders` o
JOIN `commercepulse-488300.commercepulse.fact_payments` p
    ON o.order_id = p.order_id
WHERE p.status = 'SUCCESS'
  AND p.paid_at IS NOT NULL
  AND o.order_time IS NOT NULL
GROUP BY o.vendor
ORDER BY avg_hours_to_payment;


-- ------------------------------------------------------------
-- Query 4: Refund Rate and Partial Refunds
-- Partial refund = refund amount is less than order amount
-- ------------------------------------------------------------
SELECT
    o.vendor,
    COUNT(DISTINCT o.order_id)      AS total_orders,
    COUNT(DISTINCT r.order_id)      AS refunded_orders,
    COUNTIF(r.amount < o.amount)    AS partial_refunds,
    COUNTIF(r.amount >= o.amount)   AS full_refunds,
    ROUND(
        COUNT(DISTINCT r.order_id) /
        COUNT(DISTINCT o.order_id) * 100
    , 2)                                            AS refund_rate_pct
FROM `commercepulse-488300.commercepulse.fact_orders` o
LEFT JOIN `commercepulse-488300.commercepulse.fact_refunds` r
    ON o.order_id = r.order_id
GROUP BY o.vendor
ORDER BY refund_rate_pct DESC;


-- ------------------------------------------------------------
-- Query 5: Percentage of Late-Arriving Events
-- Late = refund arriving more than 7 days after order creation
-- ------------------------------------------------------------
SELECT
    COUNT(*)                                        AS total_refunds,
    COUNTIF(
        TIMESTAMP_DIFF(
            TIMESTAMP(r.refunded_at),
            TIMESTAMP(o.order_time),
            DAY
        ) > 7
    )                                               AS late_refunds,
    ROUND(
        COUNTIF(
            TIMESTAMP_DIFF(
                TIMESTAMP(r.refunded_at),
                TIMESTAMP(o.order_time),
                DAY
            ) > 7
        ) / COUNT(*) * 100
    , 2)                                            AS late_arriving_pct
FROM `commercepulse-488300.commercepulse.fact_refunds` r
JOIN `commercepulse-488300.commercepulse.fact_orders` o
    ON r.order_id = o.order_id
WHERE r.refunded_at IS NOT NULL
  AND o.order_time IS NOT NULL;


-- ------------------------------------------------------------
-- Query 6: Top Products by Revenue
-- Note: Product SKUs are stored in MongoDB payload.
-- This query reports top vendors by revenue as a proxy.
-- ------------------------------------------------------------
SELECT
    vendor,
    COUNT(DISTINCT order_id)                        AS total_orders,
    ROUND(SUM(amount), 2)                           AS total_revenue,
    ROUND(AVG(amount), 2)                           AS avg_order_value
FROM `commercepulse-488300.commercepulse.fact_orders`
WHERE amount IS NOT NULL
GROUP BY vendor
ORDER BY total_revenue DESC;

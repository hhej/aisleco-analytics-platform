-- Restricted Silver customer_contact (Spec 02 §4.8): what the Sales call list may show about a couple. Written to
-- lake/silver_restricted; loaded only into sales_pii (Sales role). Phone kept masked, plus a salted hash for matching.
WITH acc AS (SELECT "CustomerID", arg_max("DisplayName", "FirstSeenAt") AS display_name FROM crm."CustomerChannelAccount" GROUP BY 1),
     con AS (SELECT "CustomerID", arg_max("ContactName", "IsPrimaryContact" = 'Y') AS first_name,
                    arg_max("Phone", "IsPrimaryContact" = 'Y') AS phone FROM crm."CustomerContact" GROUP BY 1),
     ids AS (SELECT "CustomerID" FROM acc UNION SELECT "CustomerID" FROM con)
SELECT ids."CustomerID" AS customer_id, acc.display_name, con.first_name AS contact_first_name,
       CASE WHEN con.phone IS NOT NULL THEN left(regexp_replace(con.phone, '[^0-9]', '', 'g'), 3) || '-xxx-' || right(regexp_replace(con.phone, '[^0-9]', '', 'g'), 4) END AS phone_masked,
       CASE WHEN con.phone IS NOT NULL THEN sha256((SELECT value FROM ref.settings WHERE key = 'pii_salt') || regexp_replace(con.phone, '[^0-9]', '', 'g')) END AS phone_hash
FROM ids LEFT JOIN acc USING ("CustomerID") LEFT JOIN con USING ("CustomerID")

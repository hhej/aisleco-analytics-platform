-- Silver package (Spec 02 §4.2): one row per package price version. package_family groups the versions of one package.
SELECT "PackageID" AS package_id, trim("PackageName") AS package_name, trim("PackageName") AS package_family,
       "PackageType" AS package_type, "ServiceFeeType" AS fee_type, "BasePrice" AS base_price, "ServiceFeeRate" AS fee_rate,
       "EffectiveFrom" AS effective_from, "EffectiveTo" AS effective_to, "Status" AS status
FROM crm."Package"

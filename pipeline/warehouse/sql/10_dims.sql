-- Gold dimensions from stg (Silver). Upsert by natural key: a surrogate key, once issued, never changes;
-- a row is rewritten only when one of its values changed.
INSERT INTO gold."DimDate"
SELECT gold.dk(date), date, week_start, gold.dk(week_start), iso_year, iso_week, year, ((month - 1) / 3 + 1), month,
       to_char(date, 'Mon'), to_char(date, 'YYYY-MM'), to_char(date, 'Dy'),
       is_weekend, is_high_season, is_buddhist_lent, lent_source, is_public_holiday, holiday_name, is_valentine,
       coalesce(auspicious_level, 'None'), auspicious_types,
       CASE WHEN is_buddhist_lent THEN 'Buddhist Lent' WHEN is_high_season THEN 'High season' ELSE 'Regular' END
FROM stg.calendar_day
ON CONFLICT ("DateKey") DO UPDATE SET
  "IsPublicHoliday" = EXCLUDED."IsPublicHoliday", "HolidayName" = EXCLUDED."HolidayName", "IsBuddhistLent" = EXCLUDED."IsBuddhistLent",
  "LentSource" = EXCLUDED."LentSource", "IsValentine" = EXCLUDED."IsValentine", "AuspiciousLevel" = EXCLUDED."AuspiciousLevel",
  "AuspiciousTypes" = EXCLUDED."AuspiciousTypes", "SeasonLabel" = EXCLUDED."SeasonLabel"
WHERE (gold."DimDate"."IsPublicHoliday", gold."DimDate"."HolidayName", gold."DimDate"."IsBuddhistLent", gold."DimDate"."AuspiciousLevel",
       gold."DimDate"."AuspiciousTypes", gold."DimDate"."LentSource")
  IS DISTINCT FROM (EXCLUDED."IsPublicHoliday", EXCLUDED."HolidayName", EXCLUDED."IsBuddhistLent", EXCLUDED."AuspiciousLevel",
                    EXCLUDED."AuspiciousTypes", EXCLUDED."LentSource");

INSERT INTO gold."DimChannel" ("ChannelID", "ChannelName", "ChannelType", "Platform", "TrackingCode")
SELECT channel_id, channel_name, channel_type, coalesce(platform, 'in person'), tracking_code FROM stg.channel
ON CONFLICT ("ChannelID") DO UPDATE SET "ChannelName" = EXCLUDED."ChannelName", "ChannelType" = EXCLUDED."ChannelType",
  "Platform" = EXCLUDED."Platform", "TrackingCode" = EXCLUDED."TrackingCode"
WHERE (gold."DimChannel"."ChannelName", gold."DimChannel"."ChannelType", gold."DimChannel"."Platform", gold."DimChannel"."TrackingCode")
  IS DISTINCT FROM (EXCLUDED."ChannelName", EXCLUDED."ChannelType", EXCLUDED."Platform", EXCLUDED."TrackingCode");

INSERT INTO gold."DimPackage" ("PackageID", "PackageName", "PackageFamily", "PackageType", "FeeType", "BasePrice", "FeeRate", "EffectiveFrom", "EffectiveTo")
SELECT package_id, package_name, package_family, package_type, fee_type, base_price, fee_rate, effective_from, effective_to FROM stg.package
ON CONFLICT ("PackageID") DO UPDATE SET "PackageName" = EXCLUDED."PackageName", "PackageFamily" = EXCLUDED."PackageFamily",
  "BasePrice" = EXCLUDED."BasePrice", "FeeRate" = EXCLUDED."FeeRate", "EffectiveTo" = EXCLUDED."EffectiveTo"
WHERE (gold."DimPackage"."PackageName", gold."DimPackage"."BasePrice", gold."DimPackage"."FeeRate", gold."DimPackage"."EffectiveTo")
  IS DISTINCT FROM (EXCLUDED."PackageName", EXCLUDED."BasePrice", EXCLUDED."FeeRate", EXCLUDED."EffectiveTo");

-- SCD Type 2: one row per contract version. A contract's own row is updated only when HR corrects it
-- (e.g. its end date is entered); a new contract is a new row with a new key.
INSERT INTO gold."DimEmployee" ("ContractID", "EmployeeID", "EmployeeCode", "Nickname", "RoleName", "RoleGroup", "EmploymentType",
                                "StandardWeeklyHours", "MaxConcurrentEvents", "ValidFrom", "ValidTo", "IsCurrent")
SELECT contract_id, employee_id, employee_code, nickname, role_name, role_group, employment_type, standard_weekly_hours,
       max_concurrent_events, effective_from, coalesce(effective_to, DATE '9999-12-31'),
       (SELECT asof FROM work.run_ctx) BETWEEN effective_from AND coalesce(effective_to, DATE '9999-12-31')
FROM stg.employee_contract
ON CONFLICT ("ContractID") DO UPDATE SET "Nickname" = EXCLUDED."Nickname", "RoleName" = EXCLUDED."RoleName",
  "RoleGroup" = EXCLUDED."RoleGroup", "EmploymentType" = EXCLUDED."EmploymentType", "StandardWeeklyHours" = EXCLUDED."StandardWeeklyHours",
  "MaxConcurrentEvents" = EXCLUDED."MaxConcurrentEvents", "ValidFrom" = EXCLUDED."ValidFrom", "ValidTo" = EXCLUDED."ValidTo",
  "IsCurrent" = EXCLUDED."IsCurrent"
WHERE (gold."DimEmployee"."ValidTo", gold."DimEmployee"."IsCurrent", gold."DimEmployee"."StandardWeeklyHours", gold."DimEmployee"."RoleName")
  IS DISTINCT FROM (EXCLUDED."ValidTo", EXCLUDED."IsCurrent", EXCLUDED."StandardWeeklyHours", EXCLUDED."RoleName");

SELECT (SELECT count(*) FROM gold."DimDate") AS dim_date, (SELECT count(*) FROM gold."DimEmployee") AS dim_employee,
       (SELECT count(*) FROM gold."DimChannel") AS dim_channel, (SELECT count(*) FROM gold."DimPackage") AS dim_package;

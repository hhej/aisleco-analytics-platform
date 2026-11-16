-- Silver channel (Spec 02 §4.2): one row per marketing channel, from the latest CRM snapshot.
SELECT "ChannelID" AS channel_id, trim("ChannelName") AS channel_name, "ChannelType" AS channel_type,
       "Platform" AS platform, "TrackingCode" AS tracking_code, "Status" AS status
FROM crm."MarketingChannel"

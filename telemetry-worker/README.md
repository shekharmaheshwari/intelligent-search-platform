# ISP telemetry collector

Cloudflare Worker that receives ISP's anonymous pings (see the main README → Telemetry)
and stores them in a D1 database. Stores the whitelisted fields plus Cloudflare's
network organisation, ASN, and country, and never the IP address.

## Useful queries (D1 console)

```sql
-- Active installs in the last 7 days, by company network
SELECT org, COUNT(DISTINCT install_id) AS installs
FROM pings WHERE received_at > datetime('now', '-7 days')
GROUP BY org ORDER BY installs DESC;

-- Provider mix
SELECT embedding_provider, chat_provider, COUNT(DISTINCT install_id) AS installs
FROM pings GROUP BY 1, 2 ORDER BY installs DESC;
```

# WTC / Competitor Results API

The data comes from the internal API used by [labs-v2.competitor.com](https://labs-v2.competitor.com). There is no official public API — these endpoints were reverse-engineered from the results site.

## Event group page

```
GET https://labs-v2.competitor.com/results/event/<GROUP_UUID>
```

Returns a full Next.js SSR HTML page. The useful data is embedded in a `<script id="__NEXT_DATA__">` JSON block inside the HTML.

### Extracting the data

```python
import re, json
m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
data = json.loads(m.group(1))
subevents = data["props"]["pageProps"]["subevents"]
```

### `subevents` array — fields used

| Field | Type | Notes |
|---|---|---|
| `wtc_eventid` | string UUID | Unique ID for this race year; passed to the results API |
| `wtc_name` | string | Full event name, e.g. `"2025 IRONMAN 70.3 Oregon"` |
| `wtc_externaleventname` | string | Short code, e.g. `"IRM-OREGON703-2025"` |
| `wtc_eventdate` | ISO datetime string | Race date |
| `sport` | string | `"Triathlon"` |

The GROUP_UUID in the URL is the umbrella for all years of a given race. Each year has its own `wtc_eventid`.

**Known group UUIDs:**

| Race | GROUP_UUID |
|---|---|
| IRONMAN 70.3 Coeur d'Alene | `eca2b3cf-881e-e511-9403-005056951bf1` |
| IRONMAN 70.3 Oregon (Salem) | `a2bd7104-f82f-42f2-8943-1265d315a160` |

---

## Results API

```
GET https://labs-v2.competitor.com/api/results?wtc_eventid=<WTC_EVENT_UUID>
```

Returns JSON directly (no HTML parsing needed).

### Response structure

```json
{
  "resultsJson": {
    "value": [ ...athlete result objects... ]
  }
}
```

The outer `resultsJson.value` array contains one object per athlete entry.

### Athlete result object — key fields

All field names carry the `wtc_` prefix from WTC's CRM (Microsoft Dynamics).

**Identity**

| Field | Type | Notes |
|---|---|---|
| `wtc_resultid` | string UUID | Unique result ID; used as dedup key |
| `bib` / `wtc_bibnumber` | string/int | Bib number |
| `athlete` | string | Full name (display) |
| `wtc_ContactId.fullname` | string | Full name from contact record |
| `wtc_ContactId.firstname` | string | |
| `wtc_ContactId.lastname` | string | |
| `wtc_ContactId.gendercode_formatted` | string | `"Male"` / `"Female"` |
| `wtc_ContactId.address1_city` | string | Athlete's home city |
| `wtc_ContactId.address1_stateorprovince` | string | State/province |
| `countryiso2` | string | ISO 2-letter country code |
| `wtc_CountryRepresentingId.wtc_iso2` | string | Country athlete competed under |
| `wtc_AgeGroupId.wtc_agegroupname` | string | Age group, e.g. `"M35-39"` |

**Status flags** (boolean, mutually exclusive)

| Field | Meaning |
|---|---|
| `wtc_dns` | Did Not Start |
| `wtc_dnf` | Did Not Finish |
| `wtc_dq` | Disqualified |
| _(none set)_ | Finisher |

**Times** (all in **seconds** as integers)

| Field | Segment |
|---|---|
| `wtc_swimtime` | Swim |
| `wtc_transition1time` | T1 |
| `wtc_biketime` | Bike |
| `wtc_transition2time` | T2 |
| `wtc_runtime` | Run |
| `wtc_finishtime` | Total finish |

Formatted versions (H:MM:SS strings) have the same names with `formatted` appended, e.g. `wtc_finishtimeformatted`.

**Rankings** (integers; gender rank is stored as a string `"1"` — must be cast)

| Field | Scope |
|---|---|
| `wtc_finishrankoverall` / `wtc_finishrankgender` / `wtc_finishrankgroup` | Finish |
| `wtc_swimrankoverall` / `wtc_swimrankgender` / `wtc_swimrankgroup` | Swim segment |
| `wtc_bikerankoverall` / `wtc_bikerankgender` / `wtc_bikerankgroup` | Bike segment |
| `wtc_runrankoverall` / `wtc_runrankgender` / `wtc_runrankgroup` | Run segment |

**Distances** (floats, in **km**)

| Field | Notes |
|---|---|
| `wtc_swimdistancecompleted` | ~1.9 km for full, ~0.5 km for 70.3 |
| `wtc_bikedistancecompleted` | ~180 km for full, ~90 km for 70.3 |
| `wtc_rundistancecompleted` | ~42 km for full, ~21 km for 70.3 |
| `wtc_totaldistancecompleted` | Sum |

**Points**

| Field | Notes |
|---|---|
| `wtc_points` | AWA (All World Athlete) qualification points |

---

## Pagination

The API currently returns all results in a single response with no pagination. For large races this can be 2,000–3,000 rows.

## Rate limiting / auth

No auth token is required. The endpoint is not officially public but has no rate limiting in practice. The HTML page uses Cloudflare — standard `curl -sL` works without any special headers.

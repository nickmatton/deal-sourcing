# SEC EDGAR Free API Reference

> Compiled 2026-04-08 for deal-sourcing connector development.

---

## 0. Global Access Rules (All Endpoints)

| Item | Detail |
|---|---|
| **Authentication** | None required – completely free, no API key |
| **Rate limit** | **10 requests/second** per IP (enforced since July 27 2021). Exceeding → temporary IP block. |
| **User-Agent requirement** | All `data.sec.gov` and `efts.sec.gov` endpoints require a descriptive `User-Agent` header: `"CompanyName AdminContact@company.com"`. Requests without it get **403 Forbidden**. |
| **Allowed protocols** | HTTPS only. All endpoints below use `https://`. |
| **Fair access** | SEC explicitly prohibits "unclassified bots." Declare yourself. No ToS signup needed. |
| **CORS** | Not enabled – server-side only. |

**Recommended User-Agent example:**
```
User-Agent: DealSourcing/1.0 admin@yourcompany.com
```

---

## 1. EDGAR Full-Text Search (EFTS) — 8-K & M&A Discovery

### Endpoint
```
GET https://efts.sec.gov/LATEST/search-index
```

### Purpose
Full-text search across all EDGAR filings since ~2001. This is the backend powering https://efts.sec.gov/LATEST/search-index — an Elasticsearch-based index of filing document text.

### Parameters

| Parameter | Required | Type | Description | Example |
|---|---|---|---|---|
| `q` | Yes | string | Search query. Supports phrase matching with `%22` (URL-encoded `"`). Multiple phrases use space (AND logic). | `%22acquisition%22`, `%22total%20consideration%22%20%22acquisition%22` |
| `forms` | No | string | Comma-separated form types to filter | `8-K`, `D`, `10-K` |
| `dateRange` | No | string | Must be `custom` to use startdt/enddt | `custom` |
| `startdt` | No | date | Start date (YYYY-MM-DD) | `2024-01-01` |
| `enddt` | No | date | End date (YYYY-MM-DD) | `2024-12-31` |
| `from` | No | int | Pagination offset (default 0) | `0`, `100`, `200` |
| `size` | No | int | Results per page (default 100, max 100) | `100` |

### Response JSON Structure

```jsonc
{
  "took": 609,                  // Query time in ms
  "timed_out": false,
  "_shards": { "total": 50, "successful": 50, "skipped": 0, "failed": 0 },
  "hits": {
    "total": {
      "value": 10000,           // Total matching documents (capped at 10000 with "gte")
      "relation": "gte"         // "gte" = 10k+ results; "eq" = exact count
    },
    "max_score": 3.83,
    "hits": [                   // Array of matching filing documents
      {
        "_index": "edgar_file",
        "_id": "0001193125-24-170599:d692630dex996.htm",  // accession:filename
        "_score": 3.83,         // Relevance score
        "_source": {
          // === KEY FIELDS FOR DEAL SOURCING ===
          "ciks": ["0001768446"],                          // CIK(s) of filer
          "display_names": ["Eliem Therapeutics, Inc.  (ELYM)  (CIK 0001768446)"],
          "form": "8-K",                                   // Specific form variant (8-K, 8-K/A)
          "root_forms": ["8-K"],                           // Normalized form type
          "adsh": "0001193125-24-170599",                  // Accession number (unique filing ID)
          "file_date": "2024-06-27",                       // Filing date
          "period_ending": "2024-06-27",                   // Reporting period
          "items": ["1.01","2.01","3.02","5.02","7.01","8.01","9.01"],  // 8-K item numbers
          "file_type": "EX-99.6",                          // Document type within filing
          "file_description": "EX-99.6",                   // Document description
          "sequence": 11,                                  // Document sequence in filing
          
          // === COMPANY METADATA ===
          "sics": ["2834"],                                // SIC industry codes
          "biz_states": ["DE"],                            // Business state
          "biz_locations": ["Wilmington, DE"],             // Business city, state
          "inc_states": ["DE"],                            // Incorporation state
          "file_num": ["001-40708"],                       // SEC file number
          "film_num": ["241079591"],                       // Film number
          "xsl": null,                                     // XSL transform (for XML filings)
          "schema_version": null                           // Schema version (for XBRL)
        }
      }
      // ... up to 100 results
    ]
  },
  "aggregations": {
    "form_filter":       { "buckets": [{"key": "8-K", "doc_count": 39940}] },
    "entity_filter":     { "buckets": [{"key": "CompanyName (TICKER) (CIK ...)", "doc_count": 50}] },
    "sic_filter":        { "buckets": [{"key": "6770", "doc_count": 4211}] },
    "biz_states_filter": { "buckets": [{"key": "NY", "doc_count": 5977}] }
  },
  "query": { /* Echo of the Elasticsearch query that was executed */ }
}
```

### Key 8-K Item Numbers for M&A

| Item | Description |
|---|---|
| `1.01` | Entry into a Material Definitive Agreement |
| `2.01` | Completion of Acquisition or Disposition of Assets |
| `2.03` | Creation of a Direct Financial Obligation |
| `7.01` | Regulation FD Disclosure |
| `8.01` | Other Events |
| `9.01` | Financial Statements and Exhibits |

### Example: Find 8-K M&A filings mentioning "acquisition"
```
GET https://efts.sec.gov/LATEST/search-index?q=%22acquisition%22&forms=8-K&dateRange=custom&startdt=2024-01-01&enddt=2024-12-31
```
Result: 10,000+ hits with company names, CIKs, dates, items.

### Example: Find 8-K filings with deal value mentions
```
GET https://efts.sec.gov/LATEST/search-index?q=%22total%20consideration%22%20%22acquisition%22&forms=8-K&dateRange=custom&startdt=2024-06-01&enddt=2024-12-31
```
Result: 520 exact hits – much more targeted M&A filings with financial terms.

### Private Company Data Extractable
- **Target company names** (from filing text, not structured)
- **Deal values** (from filing text — search for "total consideration", "purchase price", "aggregate consideration")
- **Acquirer details** (CIK, ticker, SIC code, state, location)
- **Filing date** as proxy for deal announcement date
- **Item 2.01** specifically signals completion of acquisition/disposition

### Pagination
Use `from` parameter: `from=0`, `from=100`, `from=200`, etc. Max window is 10,000 results.

---

## 2. EDGAR Company Submissions API — Company & Filing Metadata

### Endpoint
```
GET https://data.sec.gov/submissions/CIK{padded_cik}.json
```

### Purpose
Returns comprehensive metadata about a company and ALL its recent filings. CIK must be zero-padded to 10 digits.

### Parameters

| Parameter | Required | Description | Example |
|---|---|---|---|
| `{padded_cik}` | Yes (URL path) | 10-digit zero-padded CIK | `0000320193` (Apple) |

### Response JSON Structure

```jsonc
{
  // === COMPANY IDENTITY ===
  "cik": "0000320193",
  "entityType": "operating",            // "operating", "investment-company", etc.
  "name": "Apple Inc.",
  "tickers": ["AAPL"],
  "exchanges": ["Nasdaq"],
  "sic": "3571",                        // Primary SIC code
  "sicDescription": "Electronic Computers",
  "ein": "942404110",                   // Employer Identification Number
  "category": "Large accelerated filer",
  "fiscalYearEnd": "0926",              // MMDD
  "stateOfIncorporation": "CA",
  "website": "",                        // Often empty
  
  // === ADDRESSES ===
  "addresses": {
    "mailing": { "street1": "ONE APPLE PARK WAY", "city": "CUPERTINO", "stateOrCountry": "CA", "zipCode": "95014" },
    "business": { "street1": "ONE APPLE PARK WAY", "city": "CUPERTINO", "stateOrCountry": "CA", "zipCode": "95014" }
  },
  "phone": "(408) 996-1010",
  
  // === FORMER NAMES ===
  "formerNames": [
    { "name": "APPLE INC", "from": "2007-01-10T05:00:00.000Z", "to": "2019-08-05T04:00:00.000Z" },
    { "name": "APPLE COMPUTER INC", "from": "1994-01-26T05:00:00.000Z", "to": "2007-01-04T05:00:00.000Z" }
  ],
  
  // === FILINGS ===
  "filings": {
    "recent": {
      "accessionNumber": ["0001140361-26-013192", ...],  // Parallel arrays
      "filingDate":      ["2026-04-03", ...],
      "reportDate":      ["2026-04-03", ...],
      "acceptanceDateTime": ["2026-04-03T16:15:22.000Z", ...],
      "act":             ["34", ...],
      "form":            ["4", "4", "4", "D", "8-K", ...],  // Filing form type
      "fileNumber":      ["001-40708", ...],
      "filmNumber":      ["", ...],
      "items":           ["", ...],                       // 8-K items (when applicable)
      "size":            [5765, ...],                     // Filing size in bytes
      "isXBRL":          [0, ...],
      "isInlineXBRL":    [0, ...],
      "primaryDocument":     ["xslForm4X01/...", ...],
      "primaryDocDescription": ["4", ...]
    },
    "files": [
      {
        "name": "CIK0000320193-submissions-001.json",  // Older filings in separate files
        "filingCount": 1219,
        "filingFrom": "1994-01-26",
        "filingTo": "2015-04-15"
      }
    ]
  }
}
```

### Accessing Older Filings
If `filings.files` array is non-empty, fetch additional filing history:
```
GET https://data.sec.gov/submissions/CIK0000320193-submissions-001.json
```

### Private Company Data Extractable
- This API only works for companies that have a CIK (filed with SEC)
- **Company metadata**: name, address, SIC code, state of incorporation, EIN
- **Filing history**: all forms filed, dates, accession numbers
- **Form D filings** appear in the filing history — filter `form` field for `"D"` or `"D/A"`
- Use accession numbers to fetch actual filing documents

### Constructing Filing Document URLs
From accession number `0001193125-24-170599`:
```
https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{filename}
```
Where `accession_no_dashes` = `000119312524170599` (remove hyphens).

---

## 3. Form D Search — Private Placement / Exempt Offering Discovery

### 3a. EFTS Full-Text Search for Form D Filings

```
GET https://efts.sec.gov/LATEST/search-index?q=%22form+D%22&forms=D&dateRange=custom&startdt=2024-10-01&enddt=2024-12-31
```

Form D filings ARE indexed in EFTS but have minimal text content (they're XML). The search works but yields limited results because the full-text index has less content to match on for Form D.

**Response format**: Same as Section 1 above. Key differences for Form D:
- `form`: `"D"` or `"D/A"` (amendment)
- `file_type`: `"D"` or `"D/A"`
- `xsl`: `"xslFormDX01"` (XSL stylesheet for rendering)
- `schema_version`: `"X0708"`
- `items`: Contains Regulation D rule references like `["06B", "3C", "3C.7"]`
- `sics`: Often empty for private funds

### 3b. EDGAR Company Search for Form D Filers (HTML/Scraping)

```
GET https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=&CIK=&type=D&dateb=&owner=include&count=40&search_text=&State={state}&SIC={sic}
```

**NOTE**: This endpoint requires at least one filter (State, SIC, or company name). Cannot do a blank search. Returns **HTML** (not JSON) — requires scraping.

| Parameter | Required | Description | Example |
|---|---|---|---|
| `action` | Yes | Must be `getcompany` | `getcompany` |
| `type` | Yes | Filing type | `D` |
| `State` | Recommended | 2-letter state code (required if no company/CIK) | `CA`, `NY` |
| `SIC` | No | SIC industry code | `2834` |
| `company` | No | Company name search | `fund` |
| `CIK` | No | Specific CIK number | `0001768446` |
| `dateb` | No | Filed before date (YYYYMMDD) | `20241231` |
| `owner` | No | Include ownership filings | `include` |
| `count` | No | Results per page (max 100) | `40` |

**Response**: HTML table with columns: CIK, Company, State/Country. Click-through to get filings.

### 3c. Form D XML Data (Best Source for Structured Data)

Once you have an accession number for a Form D filing, fetch the actual XML:
```
GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/primary_doc.xml
```

The Form D XML contains structured fields:
- Issuer name, CIK, jurisdiction
- **Total offering amount**
- **Total amount sold**
- **Total remaining**
- Number of investors (accredited vs non-accredited)
- Industry group classification
- Revenue range
- Federal exemption(s) claimed (Rule 506(b), 506(c), etc.)
- Related persons (directors, officers, promoters)

### 3d. SEC Form D Bulk Data Sets

The SEC publishes quarterly bulk Form D data:
```
https://www.sec.gov/data/form-d
```
Available as downloadable CSV/TSV files with structured data on all Form D filings.

---

## 4. Rate Limits & Fair Use Summary

| Policy | Detail |
|---|---|
| **Max request rate** | 10 req/sec per IP address |
| **Daily limit** | None explicitly stated (but be reasonable) |
| **User-Agent header** | **Required** on `data.sec.gov` and `efts.sec.gov`. Format: `"AppName contact@email.com"` |
| **Blocking** | Temporary IP block if rate exceeded; auto-unblocks when rate drops |
| **Bot policy** | SEC prohibits "unclassified" bots. Must identify yourself via User-Agent. |
| **No API key** | Free, no registration needed |
| **Recommended** | Stay at ≤5 req/sec to be safe; implement exponential backoff on 429/403 |

---

## 5. Practical Connector Design Notes

### For 8-K M&A Filing Discovery
1. Query EFTS with targeted phrases: `"acquisition"`, `"merger"`, `"total consideration"`, `"purchase price"`, `"definitive agreement"`
2. Filter by `forms=8-K` and date range
3. Use `items` field to prioritize: `1.01` (agreement) and `2.01` (completion) are most relevant
4. Extract accession number → fetch full filing text for NLP/extraction
5. Paginate with `from` param (0, 100, 200, ...)

### For Form D Private Placement Discovery
1. Use EFTS `forms=D` for text search, or bulk data sets for comprehensive coverage
2. Fetch individual Form D XMLs using accession numbers for structured data
3. Key private company data: offering amount, investors, revenue range, exemption type

### For Company Metadata Enrichment
1. Use submissions API: `https://data.sec.gov/submissions/CIK{padded_cik}.json`
2. Get SIC code, address, phone, filing history
3. Filter filing history for specific form types
4. Cross-reference CIK from EFTS results to get full company profile

### Filing Document Retrieval
Given accession number `0001193125-24-170599`:
```python
# Remove hyphens from accession number
accession_clean = "000119312524170599"
cik = "0001768446"
filename = "d692630dex996.htm"

url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}/{filename}"
```

Or use the filing index:
```
https://www.sec.gov/Archives/edgar/data/{cik}/{accession_with_dashes}/index.json
```

---

## 6. Quick Reference: URL Patterns

| Use Case | URL Pattern |
|---|---|
| Full-text search | `https://efts.sec.gov/LATEST/search-index?q={query}&forms={type}&dateRange=custom&startdt={date}&enddt={date}` |
| Company submissions | `https://data.sec.gov/submissions/CIK{padded_cik_10digits}.json` |
| Company search (HTML) | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type={form}&State={st}&count={n}` |
| Filing index | `https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json` |
| Filing document | `https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{filename}` |
| Form D bulk data | `https://www.sec.gov/data/form-d` |
| CIK lookup by ticker | `https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK={ticker}&type=&dateb=&owner=include&count=10&search_text=&action=getcompany` |

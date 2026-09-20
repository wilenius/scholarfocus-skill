# ScholarFocus API Credential Test Summary

> [!WARNING]
> **SUPERSEDED — this report's conclusions are wrong.** Retested live on 2026-09-20:
> the CORE key is **valid** (it raises the rate limit from 10/min to 150/min), and the
> CrossRef failure was a **client bug**, not a credential problem (`select=...,reference-count`
> is not a valid CrossRef field). Only the Semantic Scholar finding held up.
> See [`docs/api-status.md`](../api-status.md) for current status.
> API keys in this file have been redacted.

**Date**: 2026-05-06  
**Tester**: Automated API credential validation

---

## Test Results

| API | Status | Credential | Issue |
|-----|--------|-----------|-------|
| **OpenAlex** | ✓ WORKING | `heikki.wilenius@iki.fi` | No issues — polite pool enabled |
| **Semantic Scholar (S2AG)** | ✗ FAILED | `<redacted>...` | HTTP 403 Forbidden — key invalid/expired |
| **CrossRef** | ✗ FAILED | `heikki.wilenius@iki.fi` | HTTP 400 Bad Request — query format issue |
| **CORE** | ✗ FAILED | `<redacted>...` | Rate limited then failed — key invalid/revoked |

---

## Detailed Findings

### ✓ OpenAlex (Primary Source)
- **Status**: Fully functional
- **Email credential**: `heikki.wilenius@iki.fi` ← Polite pool enabled
- **Test result**: Successfully retrieved 5 author records for "Marie Curie"
- **Impact**: Core researcher lookup and network analysis works

### ✗ Semantic Scholar (Secondary Source)
- **Status**: Non-functional
- **API Key**: `<redacted>`
- **Error**: HTTP 403 Forbidden on author search endpoint
- **Diagnosis**: API key is invalid, expired, or revoked
- **Impact**: Abstracts and fields of study cannot be enriched (medium priority)
- **Action**: Regenerate key at https://api.semanticscholar.org/

### ✗ CrossRef (Tertiary Source)
- **Status**: Non-functional  
- **Email credential**: `heikki.wilenius@iki.fi` ← Polite pool setup correct
- **Error**: HTTP 400 Bad Request on `search_works_by_author` call
- **Diagnosis**: Likely query parameter format issue in client implementation
- **Impact**: CrossRef DOI enrichment unavailable (low priority — not wired into main pipeline)
- **Action**: Debug the query construction in `scripts/apis/crossref.py:55-62`

### ✗ CORE (Quaternary Source)
- **Status**: Non-functional
- **API Key**: `<redacted>`
- **Error**: Rate limited (HTTP 429) three times, then failed
- **Diagnosis**: API key is invalid/revoked or quota exhausted
- **Impact**: Open-access abstract fallback unavailable (low priority)
- **Action**: Regenerate key at https://core.ac.uk/services/api

---

## Impact Assessment

**Severity: MEDIUM**

- ✓ Primary researcher lookup works (OpenAlex)
- ✗ Abstract enrichment broken (S2AG)
- ✗ Reference enrichment partially broken (CrossRef + CORE)
- → Tool functions for basic network analysis; richer metadata unavailable

---

## Action Items

| Priority | Task | Endpoint |
|----------|------|----------|
| High | Regenerate S2AG API key | https://api.semanticscholar.org/ |
| High | Regenerate CORE API key | https://core.ac.uk/services/api |
| Medium | Debug CrossRef query format | See `scripts/apis/crossref.py` |
| Low | Update `config.yaml` with new keys | After regeneration |

---

## Test Commands Used

```bash
# Full pipeline test (can take 30+ seconds)
python scripts/scholarfocus.py --researchers "Albert Einstein" --config config.yaml --output json

# Individual API tests
python3 -c "
import sys
sys.path.insert(0, 'scripts')
from apis.openalex import OpenAlexClient
oa = OpenAlexClient(email='heikki.wilenius@iki.fi')
print(oa.search_author('Marie Curie'))
"
```

---

## Notes

- OpenAlex's polite pool email is correctly configured and functional
- The tool gracefully degrades when S2AG/CORE are unavailable (logs warnings, continues)
- CrossRef is not currently imported in the main pipeline (`scholarfocus.py`), so its failure has minimal impact for now
- All three secondary/tertiary sources are optional enhancements; core functionality depends only on OpenAlex

# Recorded SerpApi responses

Real responses for the demo brand (Ola), recorded on 2026-10-03 with `gl=in`, `hl=en`
(ADR-0007: contracts come from real responses, never guessed).

Before they were committed, every response was:
1. **redacted** with `adapters.serp.redaction.redact`: no key, no `api_key` field or `api_key=`
   string, no `search_metadata` URL, no author or reviewer identity;
2. **trimmed** to a few items per list and to the blocks the parsers read, without SerpApi's
   pagination tokens, follow-up links and Google's redirect and "about" links (opaque
   tokens a secret scanner can't tell from keys, which no parser reads);
3. written as minified UTF-8 JSON.

`tests/security/test_fixtures_redacted.py` scans every file here on each CI run.

| File | Engine | Request |
|---|---|---|
| `ola/google.json` | `google` | `q=Ola`, `google_domain=google.co.in` |
| `ola/autocomplete.json` | `google_autocomplete` | `q=ola ` |
| `ola/news.json` | `google_news` | `q=Ola` |

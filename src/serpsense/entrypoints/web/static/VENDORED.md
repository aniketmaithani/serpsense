# Vendored front-end files

Files in `vendor/`, served from `/static/vendor/` (CSP `script-src 'self'`: no CDN at runtime, ADR-0002).

| File | Package | Version | Licence | Source |
|---|---|---|---|---|
| `chart.umd.js` | chart.js | 4.4.4 | MIT | `dist/chart.umd.js` from the npm tarball `https://registry.npmjs.org/chart.js/-/chart.js-4.4.4.tgz`, unchanged |

- Tarball integrity (npm registry): `sha512-emICKGBABnxhMjUjlYRR12PmOXhJ2eJjEHL2/dZlWjxRAZT1D8xplLFq5M0tMQK8ja+wBS/tuVEJB5C6r7VxJA==`
- File sha256: `fed6a739f8d0f0687174de6cd14745fc0fc7809144ab113d22908a26bf0d7fea` (pinned by `tests/unit/test_vendored.py`)

**To update:** download the new tarball, check its sha512 against `npm view chart.js@<version> dist.integrity`, copy `package/dist/chart.umd.js` into `vendor/` unchanged, and update this table and the pinned hash in the same commit.

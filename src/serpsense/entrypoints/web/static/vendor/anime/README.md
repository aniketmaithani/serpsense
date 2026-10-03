# anime.js (vendored)

Served from `/static/vendor/anime/` for the public landing page (CSP `script-src 'self'`: no CDN
at runtime, ADR-0002). Imported as an ES module; nothing else on the site loads it.

| File | Package | Version | Licence | Source |
|---|---|---|---|---|
| `anime.esm.min.js` | animejs | 4.5.0 | MIT (`LICENSE.md`) | `dist/bundles/anime.esm.min.js` from the npm tarball `https://registry.npmjs.org/animejs/-/animejs-4.5.0.tgz`, unchanged |

- Tarball integrity (npm registry, checked): `sha512-NQimYX+lz8WaXonGS9zVVoviCIAjONeJayxacUditaivYLqyXgGjFKjuyl0aUUhFKm5MriX9ty1TGovNzoJQWA==`
- File sha256: `a19015a1a92d52025a2fb6703b6d67eadd1cc2aeaf880770e96e04cf6aa07be1` (pinned by `tests/unit/test_vendored.py`)

**To update:** download the new tarball, check its sha512 against `npm view animejs@<version> dist.integrity`, copy `package/dist/bundles/anime.esm.min.js` and `package/LICENSE.md` here unchanged, and update this file and the pinned hash in the same commit.

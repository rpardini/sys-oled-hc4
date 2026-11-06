# Asset sources

- `logo-armbian.svg`: the Armbian wordmark, vendored unmodified from
  [armbian/stationery](https://github.com/armbian/stationery) at commit
  `ad7fe652d8bda25bf0632b2a030e83e099860dbf` (`logo/logo-armbian.svg`).
  The Armbian name and logo belong to the Armbian project; the stationery repository carries no explicit license.

`scripts/build_assets.py` turns it into the 1-bit bitmaps in `src/sys_oled/assets/` (dev-time only, needs `uv sync`).
CI runs it with `--check` to make sure the committed bitmaps are up to date.

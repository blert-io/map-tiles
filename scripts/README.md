# Publishing tiles to object storage

Map tiles are hosted in an S3-compatible bucket (AWS S3, DigitalOcean Spaces,
Cloudflare R2, ...) and served to consumers via a CDN. `tiles.py` is the tool
for seeding, regenerating, and syncing.

Consumers fetch tiles from paths mirroring the generated tree:

```
https://<your-cdn-domain>/{plane}/{zoom}/{x}/{y}.png
```

## How it works

- Credentials are read from an existing `s3cmd` config (`~/.s3cfg`).
- Uploads prefer [`s5cmd`](https://github.com/peak/s5cmd) for speed. If it isn't
  installed, `tiles.py` falls back to `s3cmd`.
- Incremental syncs upload only the tiles changed since the last publish,
  detected via a local marker file (`.sync-stamp`).
- DigitalOcean CDN purging (optional) flushes changed paths via `doctl` so
  consumers see updates immediately rather than waiting for the cache TTL.

## One-time setup

1. **Configure the tool.** Copy the example and fill in your bucket:

   ```sh
   cp scripts/tiles.local.env.example scripts/tiles.local.env
   # edit scripts/tiles.local.env, set TILES_BUCKET (and optionally other vars)
   ```

   (`scripts/tiles.local.env` is gitignored. You can also export `TILES_*`
   environment variables instead.)

2. **Install s5cmd** (recommended):

   ```sh
   go install github.com/peak/s5cmd/v2@latest
   # or download a release binary from the project's releases page
   ```

3. Create the bucket and CDN through your cloud provider's console or API.

4. Generate tiles locally and see the bucket with the full tile set:

   ```sh
   python3 scripts/tiles.py regenerate
   
   # Following generation, if the upload is interrupted it can be resumed:
   python3 scripts/tiles.py sync
   ```

Verify your settings any time with `python3 scripts/tiles.py info`.

## Updating tiles

Regenerate from the latest OSRS cache and publish the changes:

```sh
python3 scripts/tiles.py regenerate
```

This runs the incremental Docker tile generator (see the top-level README).

### Manual sync

```sh
python3 scripts/tiles.py sync              # upload tiles changed since last publish
python3 scripts/tiles.py sync --dry-run    # list what would be uploaded
python3 scripts/tiles.py sync --reconcile  # authoritative full diff vs. the bucket
```

Use `--reconcile` if you suspect the bucket has drifted from the working copy
(it diffs the whole tree instead of trusting the marker).

### Hydrate

If you already have tiles in your bucket, you can download them instead of
running a full build:

```sh
python3 scripts/tiles.py hydrate
```

## Full vs. incremental generation

The generator diffs the local rendered tiles against a baseline image
(`generated_images/current-map-image-*.png`) and only re-renders tiles that
changed. That baseline is not committed to git, so:

- On a fresh clone there is no baseline, so the generator does a full build.
  `regenerate` then uploads all of it.
- After a build, the baseline exists locally, so the next run on the same
  machine only generates tiles that changed in the latest OSRS cache.

To force a full rebuild on a machine that already has a baseline, delete it:

```sh
rm generated_images/current-map-image-*.png
python3 scripts/tiles.py regenerate
```

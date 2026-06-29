#!/usr/bin/env python3
"""Generates and publishes OSRS map tiles to an S3-compatible object store.

Uploads are run via s5cmd (if available) or s3cmd. The endpoint and credentials
are read from an existing s3cmd config (`~/.s3cfg`).

Settings (environment variables, or scripts/tiles.local.env which is gitignored):

    TILES_BUCKET           (required)  Destination bucket, e.g. "my-osrs-tiles".
    TILES_CACHE_CONTROL    (optional)  Cache-Control header for each tile.
                                       Default: "public, max-age=86400".
    TILES_CDN_ENDPOINT_ID  (optional)  DigitalOcean Spaces CDN endpoint UUID.
                                       When set (and `doctl` is installed),
                                       changed tiles are purged after a sync.
    TILES_CDN_DOMAIN       (optional)  Public domain from which tiles are served.
                                       Used only to print example URLs.
    S3CMD_CONFIG           (optional)  Path to the s3cmd config. Default: ~/.s3cfg.

Subcommands:

    info         Show the resolved configuration and the public tile URL.
    seed         Upload the entire tile tree (idempotent).
    sync         Upload only tiles changed since the last publish (fast path),
                 or `sync --reconcile` to diff the whole tree against the bucket.
    hydrate      Download the tile tree from the bucket into the working copy.
    regenerate   Run the Docker tile generator, then sync the changed tiles.
"""

from __future__ import annotations

import argparse
import configparser
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CACHE_CONTROL = "public, max-age=86400"
STAMP_NAME = ".sync-stamp"
DOCKER_IMAGE = "map-tile-generator"

CYAN, YELLOW, RED, RESET = "\033[0;36m", "\033[0;33m", "\033[0;31m", "\033[0m"


def log(msg: str) -> None:
    print(f"{CYAN}[tiles]{RESET} {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"{YELLOW}[tiles] WARN:{RESET} {msg}", file=sys.stderr)


def die(msg: str) -> "None":
    print(f"{RED}[tiles] ERROR:{RESET} {msg}", file=sys.stderr)
    raise SystemExit(1)


def content_type_for(path: str) -> str:
    return {
        "png": "image/png",
        "xml": "application/xml",
        "json": "application/json",
    }.get(path.rsplit(".", 1)[-1].lower(), "application/octet-stream")


@dataclass
class Config:
    repo_root: Path
    bucket: str
    endpoint: str
    access_key: str
    secret_key: str
    cache_control: str
    cdn_endpoint_id: str | None
    cdn_domain: str | None
    planes: list[str]
    use_s5cmd: bool
    s3cmd: str
    numworkers: int

    @property
    def s3_base(self) -> str:
        return f"s3://{self.bucket}"

    @property
    def s5_base(self) -> list[str]:
        # --numworkers caps parallel operations to stay under the object store's
        # request rate ceiling.
        return [
            "s5cmd",
            "--numworkers",
            str(self.numworkers),
            "--endpoint-url",
            self.endpoint,
        ]

    @property
    def stamp_file(self) -> Path:
        return self.repo_root / STAMP_NAME

    @property
    def public_base(self) -> str:
        if self.cdn_domain:
            return f"https://{self.cdn_domain}"
        host = self.endpoint.removeprefix("https://").removeprefix("http://")
        return f"https://{self.bucket}.{host}"

    def s5cmd_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.access_key:
            env["AWS_ACCESS_KEY_ID"] = self.access_key
        if self.secret_key:
            env["AWS_SECRET_ACCESS_KEY"] = self.secret_key
        env.setdefault("AWS_REGION", "us-east-1")
        return env


def load_local_env(repo_root: Path) -> None:
    """Loads scripts/tiles.local.env without overriding the real environment."""
    path = repo_root / "scripts" / "tiles.local.env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(bucket_override: str | None) -> Config:
    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path(__file__).resolve().parent,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )

    load_local_env(repo_root)

    bucket = bucket_override or os.environ.get("TILES_BUCKET")
    if not bucket:
        die(
            "No bucket set. Pass --bucket NAME, export TILES_BUCKET, or set it in "
            "scripts/tiles.local.env (see scripts/tiles.local.env.example)."
        )
    bucket = bucket.removeprefix("s3://").rstrip("/")

    # Read endpoint and credentials from the s3cmd config.
    cfg_path = Path(os.environ.get("S3CMD_CONFIG", str(Path.home() / ".s3cfg")))
    if not cfg_path.exists():
        die(f"s3cmd config not found at {cfg_path}. Configure s3cmd first (s3cmd --configure).")
    parser = configparser.RawConfigParser()
    parser.read(cfg_path)
    section = "default" if parser.has_section("default") else parser.sections()[0]
    host_base = parser.get(section, "host_base", fallback="").strip()
    if not host_base:
        die(f"host_base missing from {cfg_path}; cannot determine the S3 endpoint.")
    endpoint = host_base if host_base.startswith("http") else f"https://{host_base}"

    planes_env = os.environ.get("PLANES")
    if planes_env:
        planes = planes_env.split()
    else:
        planes = sorted(
            (p.name for p in repo_root.iterdir() if p.is_dir() and p.name.isdigit()),
            key=int,
        )
        # Fresh checkout; no tile dirs yet. The generator always produces the
        # four OSRS map planes, so default to those.
        if not planes:
            planes = ["0", "1", "2", "3"]

    force_s3cmd = os.environ.get("TILES_FORCE_S3CMD", "").lower() in ("1", "true", "yes")
    use_s5cmd = (not force_s3cmd) and shutil.which("s5cmd") is not None

    return Config(
        repo_root=repo_root,
        bucket=bucket,
        endpoint=endpoint,
        access_key=parser.get(section, "access_key", fallback="").strip(),
        secret_key=parser.get(section, "secret_key", fallback="").strip(),
        cache_control=os.environ.get("TILES_CACHE_CONTROL", DEFAULT_CACHE_CONTROL),
        cdn_endpoint_id=os.environ.get("TILES_CDN_ENDPOINT_ID") or None,
        cdn_domain=os.environ.get("TILES_CDN_DOMAIN") or None,
        planes=planes,
        use_s5cmd=use_s5cmd,
        s3cmd=os.environ.get("TILES_S3CMD", "s3cmd"),
        numworkers=int(os.environ.get("TILES_NUMWORKERS", "32")),
    )


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, check=True, env=env)


def publish_tree(cfg: Config, local_dir: Path, prefix: str) -> None:
    """Recursively upload a directory, skipping unchanged objects. Objects are
    public, tagged with the right content type, and given Cache-Control."""
    if not local_dir.is_dir():
        warn(f"skip missing dir: {local_dir}")
        return

    if cfg.use_s5cmd:
        env = cfg.s5cmd_env()
        run(
            [
                *cfg.s5_base, "sync",
                "--acl", "public-read",
                "--cache-control", cfg.cache_control,
                "--content-type", "image/png",
                "--exclude", "*.xml",
                f"{local_dir}/", f"{cfg.s3_base}/{prefix}/",
            ],
            env=env,
        )
        # Upload non-png files with their correct content type.
        for f in sorted(p for p in local_dir.rglob("*") if p.is_file() and p.suffix != ".png"):
            rel = f.relative_to(local_dir).as_posix()
            run(
                [
                    *cfg.s5_base, "cp",
                    "--acl", "public-read",
                    "--cache-control", cfg.cache_control,
                    "--content-type", content_type_for(f.name),
                    str(f), f"{cfg.s3_base}/{prefix}/{rel}",
                ],
                env=env,
            )
    else:
        # s3cmd guesses content types from its own config (guess_mime_type=True).
        run(
            [
                cfg.s3cmd, "sync", "--acl-public",
                "--add-header", f"Cache-Control: {cfg.cache_control}",
                f"{local_dir}/", f"{cfg.s3_base}/{prefix}/",
            ]
        )


def publish_files(cfg: Config, keys: list[str]) -> None:
    """Upload an explicit list of repo-relative paths."""
    if not keys:
        return
    if cfg.use_s5cmd:
        env = cfg.s5cmd_env()
        with tempfile.NamedTemporaryFile("w", suffix=".s5cmd", delete=False) as fh:
            for key in keys:
                fh.write(
                    f'cp --acl public-read '
                    f'--content-type {content_type_for(key)} '
                    f'--cache-control "{cfg.cache_control}" '
                    f'"{cfg.repo_root / key}" "{cfg.s3_base}/{key}"\n'
                )
            runfile = fh.name
        try:
            run([*cfg.s5_base, "run", runfile], env=env)
        finally:
            os.unlink(runfile)
    else:
        for key in keys:
            run(
                [
                    cfg.s3cmd, "put", str(cfg.repo_root / key), f"{cfg.s3_base}/{key}",
                    "--acl-public", "--add-header", f"Cache-Control: {cfg.cache_control}",
                ]
            )
    log(f"uploaded {len(keys)} file(s)")


def purge_cdn(cfg: Config, keys: list[str]) -> None:
    """Flush changed paths from the DigitalOcean Spaces CDN.

    No-op unless a CDN endpoint id is configured and `doctl` is installed.
    """
    if not keys:
        return
    if not cfg.cdn_endpoint_id:
        log("CDN purge skipped (TILES_CDN_ENDPOINT_ID not set); tiles refresh on TTL.")
        return
    if shutil.which("doctl") is None:
        warn(f"CDN purge skipped: doctl not installed ({len(keys)} changed paths).")
        return

    if len(keys) > 1000:
        log(f"purging entire CDN endpoint ({len(keys)} changed paths)")
        run(["doctl", "compute", "cdn", "flush", cfg.cdn_endpoint_id, "--files", "*"])
        return

    log(f"purging {len(keys)} path(s) from the CDN")
    for i in range(0, len(keys), 100):
        batch = ",".join(keys[i : i + 100])
        run(["doctl", "compute", "cdn", "flush", cfg.cdn_endpoint_id, "--files", batch])


def write_stamp(cfg: Config) -> None:
    cfg.stamp_file.touch()


def changed_since_stamp(cfg: Config) -> list[str]:
    if not cfg.stamp_file.exists():
        die(
            f"No sync marker ({STAMP_NAME}). Run `tiles.py seed` for the first upload, "
            "or `tiles.py sync --reconcile` to diff the whole tree."
        )
    cutoff = cfg.stamp_file.stat().st_mtime
    changed: list[str] = []
    for plane in cfg.planes:
        root = cfg.repo_root / plane
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_mtime > cutoff:
                changed.append(path.relative_to(cfg.repo_root).as_posix())
    return sorted(changed)


def cmd_info(cfg: Config, _args: argparse.Namespace) -> None:
    print(f"repo root        {cfg.repo_root}")
    print(f"bucket           {cfg.s3_base}")
    print(f"endpoint         {cfg.endpoint}")
    print(f"backend          {'s5cmd' if cfg.use_s5cmd else 's3cmd'}")
    print(f"planes           {' '.join(cfg.planes)}")
    print(f"cache-control    {cfg.cache_control}")
    print(f"cdn purge        {'enabled' if cfg.cdn_endpoint_id else 'disabled'}")
    print(f"public tile URL  {cfg.public_base}/{{plane}}/{{zoom}}/{{x}}/{{y}}.png")


def cmd_seed(cfg: Config, _args: argparse.Namespace) -> None:
    log(f"seeding {cfg.s3_base} via {'s5cmd' if cfg.use_s5cmd else 's3cmd'} "
        f"(planes: {' '.join(cfg.planes)})")
    for plane in cfg.planes:
        log(f"uploading plane {plane}")
        publish_tree(cfg, cfg.repo_root / plane, plane)
    write_stamp(cfg)
    log(f"done. Tiles are at {cfg.public_base}/{{plane}}/{{zoom}}/{{x}}/{{y}}.png")


def cmd_sync(cfg: Config, args: argparse.Namespace) -> None:
    if args.reconcile:
        log("reconciling whole tree against the bucket")
        for plane in cfg.planes:
            publish_tree(cfg, cfg.repo_root / plane, plane)
        write_stamp(cfg)
        log("reconcile complete")
        return

    keys = changed_since_stamp(cfg)
    if not keys:
        log("no tiles changed since last sync; nothing to do")
        return
    log(f"{len(keys)} tile(s) changed since last sync")
    if args.dry_run:
        for key in keys:
            print(key)
        log("dry run; nothing uploaded")
        return
    publish_files(cfg, keys)
    purge_cdn(cfg, keys)
    write_stamp(cfg)
    log("sync complete")


def cmd_hydrate(cfg: Config, _args: argparse.Namespace) -> None:
    log(f"downloading tiles from {cfg.s3_base} into the working copy")
    for plane in cfg.planes:
        dest = cfg.repo_root / plane
        dest.mkdir(parents=True, exist_ok=True)
        if cfg.use_s5cmd:
            run(
                [*cfg.s5_base, "sync",
                 f"{cfg.s3_base}/{plane}/*", f"{dest}/"],
                env=cfg.s5cmd_env(),
            )
        else:
            run([cfg.s3cmd, "sync", f"{cfg.s3_base}/{plane}/", f"{dest}/"])
    # Reset the marker so a subsequent regen only re-uploads what the generator
    # changes.
    write_stamp(cfg)
    log("hydrate complete")


def cmd_regenerate(cfg: Config, args: argparse.Namespace) -> None:
    if args.hydrate:
        cmd_hydrate(cfg, args)

    # Without a baseline, the generator creates every tile, so upload the whole
    # whole tree rather than a per-file incremental sync.
    images_dir = cfg.repo_root / "generated_images"
    full_build = not all((images_dir / f"current-map-image-{p}.png").exists() for p in cfg.planes)

    # Marker so an incremental upload only sees what the generator just wrote.
    write_stamp(cfg)

    build_env = dict(os.environ, DOCKER_BUILDKIT="0")
    log("building the tile generator image")
    run(["docker", "build", str(cfg.repo_root / "tile_generator"), "-t", DOCKER_IMAGE], env=build_env)
    log("running the tile generator" + (" (full build)" if full_build else "") + " (this can take a while)")
    run(["docker", "run", "--rm", "-v", f"{cfg.repo_root}:/repo", DOCKER_IMAGE], env=build_env)

    if args.no_sync:
        log("generation done; skipping sync (--no-sync). Run `tiles.py seed` (full) or `tiles.py sync`.")
        return

    if full_build:
        log("full build: uploading the entire tile tree")
        cmd_seed(cfg, args)
        return

    keys = changed_since_stamp(cfg)
    if not keys:
        log("generator produced no tile changes; nothing to upload")
        write_stamp(cfg)
        return
    log(f"generator changed {len(keys)} tile(s); uploading")
    publish_files(cfg, keys)
    purge_cdn(cfg, keys)
    write_stamp(cfg)
    log("regenerate + sync complete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", help="Destination bucket (overrides TILES_BUCKET).")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="Show resolved configuration and the public URL.")
    sub.add_parser("seed", help="Upload the entire tile tree.")

    p_sync = sub.add_parser("sync", help="Upload tiles changed since the last publish.")
    p_sync.add_argument("--reconcile", action="store_true",
                        help="Diff the whole tree against the bucket instead of using the marker.")
    p_sync.add_argument("--dry-run", action="store_true", help="List changed tiles without uploading.")

    sub.add_parser("hydrate", help="Download the tile tree from the bucket into the working copy.")

    p_regen = sub.add_parser("regenerate", help="Run the Docker generator, then sync changed tiles.")
    p_regen.add_argument("--hydrate", action="store_true",
                         help="Download the published tiles from the bucket first.")
    p_regen.add_argument("--no-sync", action="store_true", help="Generate only; don't upload afterwards.")

    args = parser.parse_args()
    cfg = load_config(args.bucket)

    {
        "info": cmd_info,
        "seed": cmd_seed,
        "sync": cmd_sync,
        "hydrate": cmd_hydrate,
        "regenerate": cmd_regenerate,
    }[args.command](cfg, args)


if __name__ == "__main__":
    main()

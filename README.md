# osrs_map_tiles

The OSRS map split into tiles for use with map viewers.

## Generating tiles

1. Install docker: https://docs.docker.com/get-docker/
2. Update your docker settings to set the maximum memory to 8GB
3. You may also need to turn on osxfs(Legacy) in Docker > Settings > General
4. Open powershell in windows, or the terminal in other OS'
5. From the root directory of this repo run

### Windows
```
$Env:DOCKER_BUILDKIT=0
docker build ./tile_generator -t "map-tile-generator"
docker run -it -v "${pwd}:/repo" map-tile-generator
```

### Mac / Unix
```
export DOCKER_BUILDKIT=0
docker build ./tile_generator -t "map-tile-generator"
docker run -it -v $(pwd):/repo map-tile-generator
```

## Hosting the tiles

Blert's fork replaces the committed tile data with a script that syncs them with
an S3-compatible storage bucket.
See [scripts/README.md](scripts/README.md) for setup and usage.

## Credits

Thanks to [Explv](https://github.com/Explv/osrs_map_tiles) for writing the
original tile generator.

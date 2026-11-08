#!/usr/bin/env bash
# GeoJSON -> PMTiles (tippecanoe が必要: brew install tippecanoe)
# 使い方: scripts/build_tiles.sh [jartic|osm]   (引数なしで両方)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/tiles

build() {
  local out="$1"; shift
  local args=()
  for name in "$@"; do
    [ -s "data/geojson/${name}.geojson" ] || continue
    # 空レイヤー(0件)は tippecanoe が失敗するので除外
    if python3 -I -c "import json,sys; sys.exit(0 if json.load(open('data/geojson/${name}.geojson'))['features'] else 1)"; then
      args+=(-L "${name}:data/geojson/${name}.geojson")
    fi
  done
  if [ "${#args[@]}" -eq 0 ]; then echo "no non-empty layers for ${out}" >&2; return 0; fi
  tippecanoe -o "data/tiles/${out}.pmtiles" --force \
    -Z8 -z16 --drop-densest-as-needed --extend-zooms-if-still-dropping \
    --no-tile-size-limit --simplification=2 "${args[@]}"
}

target="${1:-all}"
if [ "$target" = "jartic" ] || [ "$target" = "all" ]; then
  build jartic jartic_stop jartic_bike_sidewalk_ok jartic_bike_sidewalk_part \
    jartic_bike_no_entry jartic_bike_crossing jartic_closure_bike
fi
if [ "$target" = "osm" ] || [ "$target" = "all" ]; then
  build osm osm_cycleway osm_cycleway_shared osm_bicycle_designated osm_bicycle_no osm_stop
fi
ls -lh data/tiles

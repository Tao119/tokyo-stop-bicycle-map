# 東京 一時停止・自転車規制マップ

**[地図を開く (GitHub Pages)](https://tao119.github.io/tokyo-stop-bicycle-map/)**

東京都の「一時停止」と「自転車の通行に関する規制」を、OpenStreetMapの背景に重ねて表示するローカル地図です。

- JARTIC(日本道路交通情報センター)オープンデータ「交通規制情報」東京都分(2026年9月分、2026-11-01公開)
- OpenStreetMap(Overpass API、東京23区、取得時点 2026-11-08)

## 見るだけの手順

データとタイルは生成済みです。リポジトリ直下で次を実行し、ブラウザで開きます。

```bash
python -m http.server 8000
# http://localhost:8000/ を開く
```

地図ライブラリ(MapLibre GL JS, PMTiles)とOSM背景タイルはネット経由で読み込むため、オンライン環境が必要です。PMTilesはRange要求に対応していない `python -m http.server` でも動くよう、ファイル全体を1回取得する方式にしています(合計約12MB)。

画面左のチェックボックスでレイヤーをON/OFFでき、地物をクリックすると属性が出ます。

## データを作り直す手順

必要なもの: Python 3.11、numpy、tippecanoe(`brew install tippecanoe`)。

```bash
python3 scripts/parse_jartic.py      # JARTICを取得してレイヤー別GeoJSONに(--refreshで再ダウンロード)
python3 scripts/fetch_osm.py         # Overpassから取得 (data/raw/osm/)。公開サーバーが混むと数分かかる
python3 scripts/build_osm.py         # OSMをレイヤー別GeoJSONに、23区境界も作る
scripts/build_tiles.sh               # GeoJSON -> PMTiles
python3 scripts/quality_check.py     # 品質チェック
```

## レイヤー

GeoJSONは `data/geojson/`、タイルは `data/tiles/jartic.pmtiles` と `data/tiles/osm.pmtiles` です。一時停止(JARTIC)が約49MBあるためPMTiles(8.5MB)を地図で使い、GeoJSONは他ツール用に残しています。

| レイヤー | 内容 | 件数 |
|---|---|---|
| jartic_stop | 一時停止(63) | 141,084 |
| jartic_bike_sidewalk_ok | 普通自転車歩道通行可(81) | 332 |
| jartic_bike_sidewalk_part | 普通自転車の歩道通行部分(82) | 132 |
| jartic_bike_no_entry | 普通自転車の交差点進入禁止(83) | 0 |
| jartic_bike_crossing | 自転車横断帯(87) | 2,148 |
| jartic_closure_bike | 通行止め系のうち自転車が対象のもの(条件付き除外を含む) | 3,687 |
| osm_cycleway | highway=cycleway、または cycleway(:left/:right/:both) が lane・track・opposite_lane・opposite_track | 1,874 |
| osm_cycleway_shared | 上に入らず、cycleway系が shared_lane・shoulder・shared・crossing など(矢羽根や路肩。画面では初期OFF) | 2,857 |
| osm_bicycle_designated | bicycle=designated(osm_cycleway に入るものは重複するため除外) | 563 |
| osm_bicycle_no | bicycle=no | 3,147 |
| osm_stop | highway=stop | 20,834 |

## 注意すべき点

### 一時停止の進入方向・対象車両は「方向情報なし」

東京都(警視庁)のデータでは、一時停止の進入方向はほぼ入っていません。141,084件のうち方向情報があるのは73件(進入方向の座標のみ。文字情報は0件)で、残りは方向情報なしです。対象車両コードも一時停止には入っていません。属性には `has_direction` を付けてあり、ポップアップでは「方向情報なし」と表示します。73件は `approach_coords` に座標が残っています。

### 83(普通自転車の交差点進入禁止)は東京都のデータに存在しない

東京都のCSVに83の行は1件もありません(仕様上は点規制として定義されています)。レイヤーは空で、画面には0件と表示されます。同様に、通行止め系のうち 5(車両通行止め)、7(踏切)、13(車両進入禁止)も東京都のデータには含まれていませんでした。

### 通行止め系の自転車判定

対象は共通規制種別コード 4・5・7・13 です。対象車両コード(A〜D)と除外車両コードをデコードし、次の条件で判定しました。

- 対象に「車両」(A=1)、「自転車」(D=3)、「軽車両」(D=6)のいずれかを含む行を残す
- 対象車両の指定がない行は全車両対象とみなして残す(`bike_status` に記録)
- 除外車両に上記のいずれかを含み、その除外セットに期間・時間・曜日の条件がない行は落とす(自転車は常に規制対象外のため)
- 除外に条件(曜日・時間など)が付いている行は、条件外の日時には自転車も規制対象なので残し、`bike_status` を「条件付きで自転車を除外」にする

東京都の通行止め(4)は13,291件です。対象車両に自転車を含まない5,974件と、自転車が常に除外されている3,630件を落とし、3,687件が残りました。残りのうち2,754件は条件付き除外(多くは曜日付き)です。対象車両・除外車両は、セットごとの期間・時間・曜日コードと合わせて `targets` と `excludes` に `[1] 車両 (時間0800-1800 曜日コード3)` の形で入れています。曜日コードの意味は仕様書の別記2 表9を参照してください。路線名と道路種別は通行止めの行には入っていません。

### 範囲について

JARTICは東京都全域(多摩・島しょを含む)、OSMは23区が対象です。OSMの23区絞り込みは、まず長方形範囲で取得し、道路は中央の頂点、一時停止は点が23区の境界ポリゴンの内側にあるものだけを残しています。境界をまたぐ道路は片側だけが残る場合があります。

### 参考にした実装

ダウンロードとパースは [shiwaku/jartic-traffic-regulation-converter](https://github.com/shiwaku/jartic-traffic-regulation-converter)(Apache-2.0)の処理を参考にしています(カタログJSONからURLを解決、cp932のストリーム読み、`経度 緯度;…` の座標パース)。JARTICの仕様は拡張版標準フォーマット k_2.1 に基づきます。

## 品質チェック結果(`data/quality_report.json`)

座標範囲は、JARTICが東京都内(島しょを含む経度138.9〜142.3、緯度20.3〜36.0)、OSMが23区の範囲に収まっています。JARTICのパース時に範囲外だった頂点140個と、座標のない91件は取り除いています。OSMは23区内の頂点が99.3〜100%です。JARTICの23区内頂点率が60〜70%なのは、多摩・島しょ部を含むためです。

一時停止の重なり(23区内の点どうし、JARTIC 88,017点 / OSM 20,834点)は次のとおりです。

| 半径 | JARTICのうち近くにOSMがある | OSMのうち近くにJARTICがある |
|---|---|---|
| 15m | 23.0% | 94.4% |
| 30m | 25.0% | 95.8% |
| 50m | 29.0% | 96.9% |

座標のズレは確認済みです。JARTICは仕様上WGS84で、上野〜浅草周辺では一時停止の点が道路中心線から中央値1.9m、95%が5.5m以内にありました。OSMの一時停止ノードとの位置差も中央値4.8mで、測地系のような系統的なズレはありません。JARTICは停止線の位置、OSMは道路上のノードという置き方の違いで数mずれるため、同じ地点かどうかを見るなら15mより半径30m前後のほうが現実的です。

OSMの一時停止の9割以上はJARTICと一致します。一方でJARTICの一時停止の約8割は、OSMに停止ノードがまだ入っていないと読めます。JARTICは交差点単位や停止線単位で登録され、OSMは進入方向ごとの標識ノードで、点の置き方が違う点には注意が必要です。

## ライセンス・出典

- 交通規制情報: 公益財団法人 日本道路交通情報センター(JARTIC)。利用規約 https://www.jartic.or.jp/d/opendata/riyou_kiyaku.pdf を確認のうえ利用してください(CC BY 4.0への言及がありますが、規約本文は確認できていません)
- 地図データ: © OpenStreetMap contributors (ODbL)

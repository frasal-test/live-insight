# Geographic data for the map preview

They only give an idea of the map OAC will draw: precision does not matter (a design choice, 23/9/2026).
Downloaded once on 23/9/2026; not downloaded again at every start.

- `cities.tsv.gz`: from GeoNames `cities15000.zip` (https://download.geonames.org/export/dump/), licence
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), © GeoNames. Reduced: for every name (also in ASCII,
  lower case) the most populous city, coordinates rounded to 2 decimals: 39,048 main names. Plus the alternative
  names in Latin script of the cities above 100,000 inhabitants (New York, Frankfurt, Bangalore, Kiev…), only if
  no city has that main name: 65,500 names. On Retail Orders it finds 132 cities out of 133.
  Read by `liveinsight/engine/geo.py`.
- `web/public/land-110m.json`: `world-atlas@2` (ISC licence), TopoJSON with the `land` object, from Natural Earth
  1:110m data (public domain).

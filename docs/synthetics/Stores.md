## Stores

The number of stores in each prefecture is indicated in `ichimaru-ramen-delivery-demo/docs/profiles/Locations.md`.

Randomly sample the store locations in each prefecture with the indicated number of stores. The sampling algorithm is given as follows. Pass the arguments as:
  - prefecture = each prefecture indicated by above `Locations.md`
  - prefix = ''
  - suffix = '店'
  - n = the number of stores in the prefecture indicated by above `Locations.md`
  - magnitude1_range = (80, 300). This is the weekday ramen sales baseline (matching `synthetics/stores/weekday_sales_baseline` in `config/config.yaml`)
  - magnitude2_range = (50, 350). This is the weekend ramen sales baseline (matching `synthetics/stores/weekend_sales_baseline` in `config/config.yaml`)

#### Location sampling algorithm
  - Input data
    - population: `ichimaru-ramen-delivery-demo/DATA/s02_intermediate/regional_population.tsv`
    - geo-shape: `geoshape_[prefecture_code]`

  - Arguments:
    - prefecture (str): name of the prefecture within which the sampling is performed
    - prefix (str): the prefix of the name of the place
    - suffix (str): the suffix of the name of the place
    - n (int): number of places to sample
    - magnitude1_range (tuple of float): (x1, x2), which is the range of a uniform distributed sampled value
    - magnitude2_range (default is None, tuple of float): (y1, y2), which is the range of a uniform distributed sampled value

  - Outputs:
    - list of locations, each element is a tuple of (name, latitude, longitude, magnitude1, magnitude2). magnitude2 is None if the magnitude2_range argument is None

  - Processing steps:
    - Load the population of each ooaza (that has the `地域階層レベル` field of '3')
    - Select a big number N, e.g., 10000000, and normalize the population numbers of each ooaza to a weight so that their sum equals N
    - List the population weights in a sequence, so that they constitute a partition of N
    - Build a 3-column data frame sorted by the weights in descending order:
      - ooaza_name, weight, cumulative weight
    - Uniformly sample a number u in range [0, N), and map it to an ooaza by locating u within the cumulative-weight bounds
    - If the ooaza has already been sampled, then redo the sampling till it doesn't duplicate
    - Locate the polygon of ooaza from the geo-shape file, select the centroid coordinates of the ooaza as the latitude and longitude of the store
    - Generate the name as <prefix><prefecture><city/ward/town/village><ooaza><suffix>
    - Uniformly sampling the magnitude(s)


#### Save the sampled stores to `ichimaru-ramen-delivery-demo/DATA/s03_primary/store.tsv`
  - Columns layout:
    - prefecture
    - store_name
    - latitude
    - longitude
    - weekday_sale_baseline
    - weekend_sale_baseline

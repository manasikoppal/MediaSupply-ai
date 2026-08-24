# Knowledge Graph Data Quality Report

Generated: 2026-08-21T18:45:11.922914-04:00

Snapshot: `2026-08-21_17`

Database: `data/processed/2026-08-21_17/knowledge_graph.sqlite`

## Graph size

### Nodes

| Node type | Count |
|---|---:|
| `manufacturers` | 7,237 |
| `drug_products` | 137,206 |
| `ndcs` | 390,016 |
| `active_ingredients` | 7,694 |
| `shortages` | 1,628 |
| `recalls` | 17,876 |
| `applications` | 29,749 |
| `sponsors` | 2,119 |
| `equivalence_groups` | 10,625 |

### Relationships

| Edge type | Count |
|---|---:|
| `product_ndcs` | 392,437 |
| `product_ingredients` | 221,381 |
| `product_shortages` | 1,451 |
| `product_recalls` | 10,431 |
| `product_equivalence_groups` | 88,563 |
| `application_owned_by_sponsor` | 29,273 |
| `manufacturer_markets_product` | 137,206 |
| `ndc_contains_ingredient` | 580,469 |
| `therapeutic_equivalent_product` | 3,368,492 |

## Cross-source link quality

| Relationship | Linked | Total | Success rate |
|---|---:|---:|---:|
| Shortage → NDC product | 1,451 | 1,628 | 89.13% |
| Recall → NDC product | 3,484 | 17,876 | 19.49% |
| Drugs@FDA application → Sponsor | 29,273 | 29,273 | 100.00% |
| All graph applications → Sponsor | 29,273 | 29,749 | 98.40% |
| NDC product → sponsored application | 59,866 | 137,206 | 43.63% |

Recall linkage is 100.00% for the 3,230 records where openFDA supplies harmonized identifiers. Older recalls without identifiers remain unlinked unless their text contains an explicit NDC.

### Recall match methods

| Method | Recall records |
|---|---:|
| `application_number` | 6 |
| `explicit_ndc_text` | 254 |
| `openfda_ndc` | 3,224 |
| `unmatched` | 14,392 |

## Therapeutic-equivalence proxy

Orange Book data is not present in the current ingestion snapshot. Candidate equivalence therefore requires a finished drug product with the same normalized active-ingredient set, strength, dosage form, and route.

- Products with a complete proxy signature: 113,072
- Products belonging to a multi-product equivalence group: 88,563
- Multi-product equivalence groups: 10,625
- These are candidates, not FDA-rated therapeutic-equivalence determinations.
- Unfinished NDC products retained as graph nodes but excluded from alternative/equivalence results: 21,710

## Worked graph traversals

Each example follows product → active ingredient → other products/manufacturers, then checks current shortages, ongoing recalls, and proxy-equivalent products.

### 1. Lisdexamfetamine Dimesylate — `000540370`

- Manufacturer: Hikma Pharmaceuticals USA Inc.
- Active ingredient traversal: LISDEXAMFETAMINE DIMESYLATE (20 mg/1)
- Selected product currently in shortage: Yes
- Selected product under ongoing recall: No
- Other manufacturers sharing an ingredient: 20
- Alternative products currently in shortage: 100
- Alternative products under ongoing recall: 13
- Proxy-equivalent products: 18

| Example alternative manufacturer | Product | Current shortage | Ongoing recall |
|---|---|---:|---:|
| Sun Pharmaceutical Industries, Inc. | LISDEXAMFETAMINE DIMESYLATE | Yes | Yes |
| Mylan Pharmaceuticals Inc. | Lisdexamfetamine Dimesylate | Yes | No |
| SpecGx LLC | Lisdexamfetamine Dimesylate | Yes | No |
| Teva Pharmaceuticals, Inc. | Lisdexamfetamine dimesylate | Yes | No |
| Lannett Company, Inc. | Lisdexamfetamine Dimesylate | Yes | No |

### 2. Furosemide — `000543294`

- Manufacturer: Hikma Pharmaceuticals USA Inc.
- Active ingredient traversal: FUROSEMIDE (10 mg/mL)
- Selected product currently in shortage: Yes
- Selected product under ongoing recall: No
- Other manufacturers sharing an ingredient: 58
- Alternative products currently in shortage: 15
- Alternative products under ongoing recall: 6
- Proxy-equivalent products: 1

| Example alternative manufacturer | Product | Current shortage | Ongoing recall |
|---|---|---:|---:|
| Rising Pharma Holdings, Inc. | Furosemide | No | Yes |
| Leading Pharma, LLC | Furosemide | No | Yes |
| Hospira, Inc. | Furosemide | Yes | No |
| Accord Healthcare Inc. | Furosemide | Yes | No |
| Heritage Pharmaceuticals Inc. d/b/a Avet Pharmaceuticals Inc. | Furosemide | Yes | No |

### 3. Clonazepam — `000930832`

- Manufacturer: Teva Pharmaceuticals USA, Inc.
- Active ingredient traversal: CLONAZEPAM (.5 mg/1)
- Selected product currently in shortage: Yes
- Selected product under ongoing recall: No
- Other manufacturers sharing an ingredient: 28
- Alternative products currently in shortage: 15
- Alternative products under ongoing recall: 5
- Proxy-equivalent products: 46

| Example alternative manufacturer | Product | Current shortage | Ongoing recall |
|---|---|---:|---:|
| Par Health USA, LLC | Clonazepam | No | Yes |
| Accord Healthcare Inc. | Clonazepam | Yes | No |
| Solco Healthcare LLC | Clonazepam | Yes | No |
| Aurobindo Pharma Limited | Clonazepam | Yes | No |
| H2-Pharma, LLC | Klonopin | Yes | No |

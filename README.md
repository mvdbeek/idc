# IDC - Simon's Data Club

In memory of our friend and reference data champion, [Simon Gladman](https://www.biocommons.org.au/news/simon-gladman).

Formerly the Intergalactic (reference) Data Commission

The IDC is for Galaxy reference data what the [IUC](https://github.com/galaxyproject/tools-iuc) for Galaxy tools: A project by the Galaxy Team and Community to produce, host, and distribute reference data for use in Galaxy servers. Community contributions and Pull Request reviews are encouraged! Details on how to contribute can be found below.

### Summary

This repository is the entry point to contribute to the community maintained CVMFS data repository hosting approximately 6TB of public and open reference datasets.

Ultimately, it is envisioned that the set of files contained here would be modified with the addition of either a new genomic data set specification or a new data manager. Subsequent Pull Request acceptance would then fetch the genomic data, build the appropriate indices and upload everything to the proper position within the Galaxy project's CVMFS repositories.

Comments/discussion on the approach and contributions are very welcome!

Currently, the repository is geared to produce genomic indices for various tools using their data managers. The included `run_builder.sh` script will:

1. Create a virtualenv with the required software
2. Create a docker Galaxy instance
3. Install the data manager tools listed in `data_managers_tools.yml`
4. Dynamically create an Ephemeris .yml config file from a list of genomes and their sources
5. Fetch the genomes from the appropriate sources and install them into Galaxy's `all_fasta` data table
6. Restart Galaxy to reload the `all_fasta` data table
7. Create the tool indices using Ephemeris and the `data_managers_genomes.yml` file

The resulting genome files and tool indices will be located in the directory specified in the `run_builder.sh` script in the environment variables set at the top.

The two important files are:

* `data_managers.yml`
* `genomes.yml`

### data_managers.yml

This file contains the list of data managers that are to be installed into the target 
Galaxy building IDC data.

```yaml
NAME_OF_THE_DATA_MANAGER:
  tool_id: TOOL_ID_IN_TARGET_REPO_OF_DATA_MANAGER
  tags:
    - tag #Tag can be either "genome" or "fetch_source".
```

Other data managers are added as elements in the `tools` yml array. The first tool listed should always be the `fetch_source` data manager. In most cases this will be the `data_manager_fetch_genome_dbkeys_all_fasta` data manager that sources and downloads most genomes and populates the `all_fasta` and `__dbkeys__` data tables for later use by other data managers.

Ephemeris can be used to generate a shed-tool install file to bootstrap the required tools
and repositories into a target Galaxy for IDC installs.

```bash
pip install ephemeris
_idc-data-managers-to-tools
# defaults to:
# _idc-data-managers-to-tools --data-managers-conf=genomes.yml --shed-install-output-conf=tools.yml
shed-tools install -t tools.yml
```

### genomes.yml

This is the file that contains the list of the genomes to be fetched and indexed.

There is a lot more information in this file that Galaxy can currently use but its format has been specified with the future in mind.

At this stage this file only needs to contain the `dbkey`, `description`, `id` and `source` fields. The rest are there as discussion points currently on the kind of information we would like to have stored with Galaxy to ensure provenance of the reference data used in analyses.

Format:

```yaml
genomes:
    - dbkey: #The dbkey of the data
      description: #The description of the data, including its taxonomy, version and date
      id: #The unique id of the data in Galaxy
      source: #The source of the data. Can be: 'ucsc', an NCBI accession number or a URL to a fasta file.
      doi: #Any DOI associated with the data
      version: #Any version information associated with the data
      checksum: #A SHA256 checksum of the original
      blob: #A blob for any other pertinent information
      indexers: #A list of tags for the types of data managers to be run on this data
      skiplist: # A list of data managers with the above specified tag NOT to be run on this data

```

Example:

```yaml
genomes:
  - dbkey: dm6
    description: D. melanogaster Aug. 2014 (BDGP Release 6 + ISO1 MT/dm6) (dm6)
    id: dm6
    source: ucsc
    doi:
    version:
    checksum:
    blob:
    indexers:
      - genome
    skiplist:
      - bfast
  - dbkey: Ecoli-O157-H7-Sakai
    description: "Escherichia coli O157-H7 Sakai"
    id: Ecoli-O157-H7-Sakai
    source: https://swift.rc.nectar.org.au:8888/v1/AUTH_377/public/COMP90014/Assignment1/Ecoli-O157_H7-Sakai-chr.fna
    doi:
    version:
    checksum:
    blob:
    indexers:
      - genome
    skiplist:
      - bfast
  - dbkey: Salm-enterica-Newport
    description: "Salmonella enterica subsp. enterica serovar Newport str. USMARC-S3124.1"
    id: Salm-enterica-Newport
    source: NC_021902
    doi:
    version:
    checksum:
    blob:
    indexers:
      - genome
    skiplist:
      - bfast
```

## Contributing versioned reference data (workflow bundles)

Beyond the genome-indexing pipeline above (`genomes.yml` + `data_managers.yml`),
the IDC supports **versioned reference databases** built by data managers — e.g.
`metaphlan_database_versioned`, `motus_db_versioned`, `samestr_db`. Each requested
version is built by running a Galaxy **data-manager-bundle workflow** on
[test.galaxyproject.org](https://test.galaxyproject.org), and the resulting
bundle is imported onto CVMFS by Jenkins.

### How to request a new reference-data version

Open a PR that adds a single YAML file at:

```
data-managers/<data_manager>/<version>.yaml
```

- `<data_manager>` is the name of the data manager's primary **data table**
  (e.g. `motus_db_versioned`). It must be one of the file's `data_tables`.
- `<version>` (the file name, without extension) is the version identity used for
  the build history and for idempotency — it must be unique per data manager.

**Standalone request** (a self-contained download/build), e.g.
`data-managers/motus_db_versioned/3.1.0.yaml`:

```yaml
# Full, version-pinned Tool Shed GUID of the data manager tool (required).
tool_id: toolshed.g2.bx.psu.edu/repos/bgruening/data_manager_motus/motus_db_fetcher/3.1.0+galaxy0
# Data table(s) the data manager populates (required).
data_tables: [motus_db_versioned]
# Tool parameters for this build. Each becomes a workflow input (use "|" for
# nested/conditional params, e.g. "db|build").
params:
  version: "3.1.0"
# Optional, human-facing provenance:
description: mOTUs profiler database, version 3.1.0
doi:
checksum:
```

**Chained request** (a data manager that builds from another database), e.g.
`data-managers/samestr_db/marker_db_mpa_vJan21.yaml` — SameStr builds from a
MetaPhlAn database:

```yaml
tool_id: toolshed.g2.bx.psu.edu/repos/iuc/data_manager_samestr/samestr_db/1.2025.111+galaxy1
data_tables: [samestr_db]
# The upstream table -> version this build depends on. A request file must exist
# at data-managers/metaphlan_database_versioned/<version>.yaml so it is built
# first; its bundle is wired into this data manager's input.
depends_on:
  metaphlan_database_versioned: mpa_vJan21_CHOCOPhlAnSGB_202103
params: {}
description: SameStr marker database derived from MetaPhlAn mpa_vJan21
```

### What happens to your PR

1. **Lint** (GitHub Actions, on the PR): `scripts/request_models.py` validates the
   request (version-pinned GUID, folder/table match, `depends_on` resolves, not
   already in `published.yml`) and gxformat2-validates the workflow it generates.
2. **Build** (GitHub Actions, on merge to `main`): `scripts/generate_build.py`
   turns the request into a gxformat2 data-manager-bundle workflow, and
   `planemo run` executes it on test.galaxyproject.org into a history named
   `idc-<data_manager>-<version>`. Each data manager runs in
   `__data_manager_mode: bundle`, producing a downloadable bundle dataset.
3. **Import** (Jenkins): the bundle(s) are resolved from the build's workflow
   invocation and imported onto CVMFS with `galaxy-import-data-bundle`
   (`.ci/import_reference_data.sh` / `.ci/jenkins.sh`), recording
   `record/<data_manager>/<version>` for idempotency.

### Avoiding rebuilds of existing data ("does this already exist?")

Reference data that a Galaxy already has is never rebuilt or re-imported. The
authoritative check is the target Galaxy's tool data table:
`GET /api/tool_data/<table>` (public, no key) lists what is actually available
there — from *any* source, including the byhand `data.galaxyproject.org` CVMFS —
so we don't duplicate data that already exists. `scripts/check_data_exists.py`
performs this check and is used at three points:

- **Lint** (informational): warns on the PR if a request's data already exists.
- **Build** (`build.yml`): skips building requests whose data already exists.
- **Import**: `import_bundles.py` skips gracefully when there is no build history
  for a request (which is the case when the build skipped it).

Version matching is heuristic (the identifying column differs per data manager),
so a request is considered present if its version, any `params` value, or any
`depends_on` version matches a table entry.

### Adding a brand-new data manager

To onboard a data manager that isn't used yet:

1. Get the tool installed on test.galaxyproject.org by adding it to
   [usegalaxy-tools](https://github.com/galaxyproject/usegalaxy-tools)
   (`test.galaxyproject.org/data_managers.yml`).
2. Add its data table(s) to `config/tool_data_table_conf.xml` (columns must match
   the data manager's `<data_tables>` definition).
3. If it builds from another database, add a wiring entry to `CHAIN_WIRING` in
   `scripts/generate_build.py` describing the conditional selector and the input
   parameter that receives the upstream bundle.

### Testing locally

```bash
pip install "pydantic>=2" pyyaml gxformat2 pytest
python scripts/request_models.py                 # lint all requests
python scripts/generate_build.py --all --outdir build   # generate + gxformat2-validate
pytest tests/                                    # pipeline unit tests
```

## Testing

This repo can be tested using a machine with Docker installed and by a user with Docker privledges. As a warning however, some of the genomes will take a LOT (>64GB) of RAM to index.

It should work just by cloning the repo to the machine, modifying the environment variables in the `run_builder.sh` script to suit and then running it.

## Other data types

Work has been done on some of the other data types, tools and data managers such as those that work on multiple genomes at once like Busco, Metaphlan etc. These can be found in the `older_attempts` directory along with appropriate README.
## How to use the reference data

If you want to use the reference data, please have a look at our [ansible-role](https://github.com/galaxyproject/ansible-cvmfs
) and the [example playbook](https://github.com/usegalaxy-eu/cvmfs-example).


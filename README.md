# junos-exporter

## Overview
This is a Prometheus Exporter for Junos using PyEZ([Juniper/py-junos-eznc](https://github.com/Juniper/py-junos-eznc)) Tables and Views.  
PyEZ can extract information from Junos operational command output and map it to a Python data structure via yaml.  
`junos-exporter` converts the information provided by PyEZ into the Prometheus metrics format via yaml.  
So, this exporter's metrics can be flexibly configured by simply editing yaml.  

## Usage
1. Clone the repository:
    ```bash
    git clone https://github.com/minefuto/junos-exporter.git
    cd junos-exporter
    ```
2. Configure the exporter by editing the `config.yml`
3. Run the exporter:
    ```bash
    uv run -m junos_exporter
    ```

## Configuration
Please see the `config.yml` for configuration example.

#### Create Exporter Config
```yaml
general:
  port: 9326  # exporter's listen port
  prefix: junos  # prefix of the metrics
```

#### Create PyEZ Operational Tables
- [Parsing Structured Output](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/task/junos-pyez-tables-op-defining.html)
- [parsing Unstructured Output](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/topic-map/junos-pyez-tables-op-unstructured-output-defining.html)
  - [Using TextFSM Template](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/concept/junos-pyez-tables-op-using-textfsm-templates.html)

Please store the following configured(config.yml) directory.
```yaml
general:
  optables_dir: ./op/  # store the structured & unstructured tables and views configuration file 
  textfsm_dir: ./textfsm/  # store the textfsm template file
```
If using the following predefined operational table, you can skip this step.
- [Document](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/concept/junos-pyez-tables-op-predefined.html)
  - [Source Code](https://github.com/Juniper/py-junos-eznc/tree/master/lib/jnpr/junos/op)

#### Create Convert Rule
```yaml
optables:
  EthPortTable:  # pyez table name
    metrics:
      - name: interface_link_status  # metrics name
        value: oper  # metrics value
        type: gauge  # metrics type(gauge or count or untyped)
        help: physical interface link status(up:2, down:1)  # metrics help
        value_transform:  #(optional) if metrics value is str, can be transformed to float
          up: 2
          down: 1
    labels:
      - name: interface  # label name
        value: name  # label value
      - name: description
        value: description
        regex: .*  #(optional) label values can be extracted by using regexp
      - name: link_mode
        value: link_mode
      - name: mac
        value: macaddr
```
`metrics value` and `label value` select from fields key of PyEZ View.
```yaml
EthPortView:
  groups:
    mac_stats: ethernet-mac-statistics
    flags: if-device-flags
  fields: <- !!
    oper: oper-status
    admin: admin-status
    description: description
    mtu: { mtu : int }
    link_mode: link-mode
    macaddr: current-physical-address
-snip-
```
PyEZ Table's key is automatically mapping to `key` and `name`.
```yaml
EthPortTable:
  rpc: get-interface-information
  args:
    media: True
    interface_name: '[afgxe][et]-*'
  args_key: interface_name <- !!
  item: physical-interface
  view: EthPortView
```
If there are multiple keys, a number is assigned at the end such as `key.0`, `key.1`.
```yaml
IsisAdjacencyTable:
  rpc: get-isis-adjacency-information
  args:
    extensive: True
  item: isis-adjacency
  key:
    - interface-name  <- key.0, name.0
    - system-name     <- key.1, name.1
  view: IsisAdjacencyView
```
The metrics value can be a static value.
```yaml
optables:
  ArpTable:
    metrics:
      - name: arp_entry_info
        value: 1  <- !!
        type: gauge
        help: arp entry info
```

#### Create Junos Device Config
```yaml
modules:
  router:  # module name
    username: admin  # junos device's username
    password: admin@123  # junos device's password
    tables:  # pyez table name(which pyez table information to retrieve in this module)
      - ArpTable
      - EthPortTable
```

#### Create Prometheus Config
```yaml
scrape_configs:
  - job_name: "junos-exporter"
    static_configs:
      - targets:
          - "192.168.1.1"  # target device
          - "192.168.1.2"
        labels:
          module: "router"  # module name
      - targets:
          - "192.168.10.3"
        labels:
          module: "switch"
    relabel_configs:
      - source_labels: [module]
        target_label: __param_module
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: 127.0.0.1:9326
```

## License
MIT

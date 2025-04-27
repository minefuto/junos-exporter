# junos-exporter

![PyPI - Python Version](https://img.shields.io/pypi/pyversions/junos-exporter)
![PyPI](https://img.shields.io/pypi/v/junos-exporter)
![GitHub](https://img.shields.io/github/license/minefuto/junos-exporter)

## Overview
This is a Prometheus Exporter for Junos using PyEZ([Juniper/py-junos-eznc](https://github.com/Juniper/py-junos-eznc)) Tables and Views.  
PyEZ can extract information from Junos operational command output and map it to a Python data structure via yaml.  
`junos-exporter` converts the information provided by PyEZ into the Prometheus metrics format via yaml.  
So, this exporter's metrics can be flexibly configured by simply editing yaml.  

## Install
```
pip install junos-exporter
```

## Usage
<details>

<summary>Docker(Recommended)</summary>

Download config.yml
```sh
curl -sO https://raw.githubusercontent.com/minefuto/junos-exporter/refs/heads/main/config.yml
```

Edit config.yml
```yaml
general:
  prefix: junos  # prefix of the metrics
  timeout: 60  # request timeout of exporter

credentials:
  vjunos: # credential name
    username: admin  # junos device's username
    password: admin@123  # junos device's password
```

Edit prometheus config
```yaml
scrape_configs:
  - job_name: "junos-exporter"
    static_configs:
      - targets:
          - "192.168.1.1"  # target device
          - "192.168.1.2"
        labels:
          __meta_credential: "vjunos"  # credential name
          __meta_module: "router"
    relabel_configs:
      - source_labels: [__meta_credential]
        target_label: __param_credential
      - source_labels: [__meta_module]
        target_label: __param_module
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: 127.0.0.1:9326
```

Run junos-exporter
```
docker run -v config.yml:/app/config.yml ghcr.io/minefuto/junos-exporter
```
</details>
<details>

<summary>Pip</summary>

Download config.yml
```sh
curl -s -o ~/.junos-exporter/config.yml --create-dirs https://raw.githubusercontent.com/minefuto/junos-exporter/refs/heads/main/config.yml
```

Edit config.yml
```yaml
general:
  prefix: junos  # prefix of the metrics
  timeout: 60  # request timeout of exporter

credentials:
  vjunos: # credential name
    username: admin  # junos device's username
    password: admin@123  # junos device's password
```

Download op/tables.yml
```sh
curl -s -o ~/.junos-exporter/op/tables.yml --create-dirs https://raw.githubusercontent.com/minefuto/junos-exporter/refs/heads/main/op/tables.yml
```

Edit prometheus config
```yaml
scrape_configs:
  - job_name: "junos-exporter"
    static_configs:
      - targets:
          - "192.168.1.1"  # target device
          - "192.168.1.2"
        labels:
          __meta_credential: "vjunos"  # credential name
          __meta_module: "router"
    relabel_configs:
      - source_labels: [__meta_credential]
        target_label: __param_credential
      - source_labels: [__meta_module]
        target_label: __param_module
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: 127.0.0.1:9326
```

Run junos-exporter
```
junos-exporter
```
</details>

## Options

`junos-exporter` has some uvicorn options and change listen address/port options.
```
usage: junos-exporter [-h] [--host HOST] [--log-level {critical,error,warning,info,debug,trace}] [--no-access-log] [--port PORT] [--reload]
                      [--root-path ROOT_PATH] [--workers WORKERS]

options:
  -h, --help            show this help message and exit
  --host HOST           listen address[default: 0.0.0.0]
  --log-level {critical,error,warning,info,debug,trace}
                        log level[default: info]
  --no-access-log       disable access log
  --port PORT           listen port[default: 9326]
  --reload              enable auto reload
  --root-path ROOT_PATH
                        root path[default: ""]
  --workers WORKERS     number of worker processes[default: 1]
```

## Build your own metrics

Please see the `config.yml` and `op/tables.yml` for configuration example.

### 1. Create parse rule for junos output

Create PyEZ config file and put the yaml or textfsm in the following directory.
- pyez structured & unstructured tables and views configuration file
  - docker: `/app/op/`
  - pip: `~/.junos-exporter/op/`
- textfsm template file
  - docker: `/app/textfsm/`
  - pip: `~/.junos-exporter/textfsm/`

How to configure PyEZ Tables and Views:
- [Parsing Structured Output](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/task/junos-pyez-tables-op-defining.html)
- [parsing Unstructured Output](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/topic-map/junos-pyez-tables-op-unstructured-output-defining.html)
  - [Using TextFSM Template](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/concept/junos-pyez-tables-op-using-textfsm-templates.html)

### 2. Create metrics convert rule

Edit optables section in config.yml.
```yaml
optables:
  PhysicalInterfaceStatus:  # pyez table name
    metrics:
      - name: interface_speed  # metrics name
        value: speed  # metrics value
        type: gauge  # metrics type(gauge or count or untyped)
        help: Speed of show interfaces extensive  # metrics help
        value_transform:  #(optional) if metrics value is str, can be transformed to float
          100mbps: 100000000
          1000mbps: 1000000000
          1Gbps: 1000000000
          10Gbps: 10000000000
          100Gbps: 100000000000
          _: 0  #(optional) value_transform's fallback value(default: NaN)
      - name: interface_lastflap_seconds
        value: interface_flapped
        type: counter
        help: Last flapped of show interfaces extensive
        to_unixtime: True  # transform to unixtime for timestamp/uptime  e.g. 2025-03-22 12:57:10, 10w3d 11:11:11
-snip-
    labels:
      - name: interface  #(optional) label name
        value: name  # label value
        regex: ([^\.]*).*  #(optional) label values can be extracted by using regexp
      - name: unit
        value: name
        regex: .*\.(\d+)
      - name: description
        value: description
```

`metrics/label value` select from fields key of PyEZ View(e.g. oper_status, admin_status).
- tables.yml
```yaml
  PhysicalInterfaceStatusView:
    groups:
      traffic_statistics: traffic-statistics
      input_error_list: input-error-list
      output_error_list: output-error-list
      ethernet_pcs_statistics: ethernet-pcs-statistics
    fields: <- !!
      oper_status: oper-status
      admin_status: admin-status
      description: description
      speed: speed
      mtu: mtu
      link_mode: link-mode
      interface_flapped: interface-flapped
-snip-
```
- config.yml
```yaml
PhysicalInterfaceStatus:
    metrics:
      - name: interface_admin_status
        value: admin_status <- !!
        type: gauge
        help: "Admin status of show interfaces extensive(Up: 2, Down: 1)"
        value_transform:
          up: 2
          down: 1
      - name: interface_oper_status
        value: oper_status <- !!
        type: gauge
        help: "Oper status of show interfaces extensive(Up: 2, Down: 1)"
        value_transform:
          up: 2
          down: 1
```

PyEZ Table's key is automatically mapping to `key` and `name` for `metrics/label value`.
- tables.yml
```yaml
RoutingEngineStatus:
  rpc: get-route-engine-information
  item: route-engine
  key: slot <- !!
  view: RoutingEngineStatusView
```
- config.yml
```yaml
  RoutingEngineStatus:
    metrics:
-snip-
    labels:
      - name: slot
        value: key <- !!
```

If there are multiple keys, a number is assigned at the end such as `key.0`, `key.1`.
- tables.yml
```yaml
LldpStatus:
  rpc: get-lldp-neighbors-information
  item: lldp-neighbor-information
  key:
    - lldp-local-port-id <- key.0, name.0
    - lldp-remote-port-id <- key.1, name.1
  view: LldpStatusView
```
- config.yml
```yaml
  LldpStatus:
    metrics:
      - name: lldp_neighbor_info
        value: 1
        type: gauge
        help: Information of show lldp neighbor
    labels:
      - name: remote
        value: remote_system_name
      - name: interface
        value: key.0 <- !!
      - name: remote_interface
        value: key.1 <- !!
```

The metrics value can be a static value.
- config.yml
```yaml
  HardwareStatus:
    metrics:
      - name: hardware_info
        value: 1 <- !!
        type: gauge
        help: Infomation of show chassis hardware
```

### 3. Create modules

Edit modules section in config.yml.
```yaml
modules:
  test:  # module name
    tables:  # add created optables
      - SystemAlarmStatus
      - ChassisAlarmStatus
      - FpcStatus
-snip-
```

Edit prometheus config
```yaml
scrape_configs:
  - job_name: "junos-exporter"
    static_configs:
      - targets:
          - "192.168.1.1"  # target device
          - "192.168.1.2"
        labels:
          __meta_credential: "vjunos"  # credential name
          __meta_module: "test"  # module name
    relabel_configs:
      - source_labels: [__meta_credential]
        target_label: __param_credential
      - source_labels: [__meta_module]
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

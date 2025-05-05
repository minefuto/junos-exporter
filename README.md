# junos-exporter

![PyPI - Python Version](https://img.shields.io/pypi/pyversions/junos-exporter)
![PyPI](https://img.shields.io/pypi/v/junos-exporter)
![GitHub](https://img.shields.io/github/license/minefuto/junos-exporter)


## Overview

This is a prometheus exporter for Junos devices using [PyEZ](https://github.com/Juniper/py-junos-eznc) Tables and Views.  
PyEZ Tables and Views allows you to extract operational information from Junos devices and map it to python objects using simple YAML definitions.  
`junos-exporter` can convert the python objects created with PyEZ into prometheus metrics.  
As a result, this exporter allows you to flexibly configure the metrics to be scraped using only YAML definitions.

The following configuration is required for Junos devices because PyEZ using netconf over ssh.
```
set system service netconf ssh
```

## Installation

```shell
pip install junos-exporter
```

## Usage
<details>

<summary>Docker(Recommended)</summary>

1. **Download `config.yml`**:
   ```sh
   curl -sO https://raw.githubusercontent.com/minefuto/junos-exporter/refs/heads/main/config.yml
   ```

2. **Edit `config.yml`**:
   ```yaml
   general:
     prefix: junos  # Prefix for the metrics
     timeout: 60    # Request timeout for the exporter

   credentials:
     vjunos: # Credential name
       username: admin  # Junos device's username
       password: admin@123  # Junos device's password
   ```

3. **Update prometheus configuration**:
   ```yaml
   scrape_configs:
     - job_name: "junos-exporter"
       static_configs:
         - targets:
             - "192.168.1.1"  # Target device
             - "192.168.1.2"
           labels:
             __params_credential: "vjunos"  # Credential name
             __params_module: "router"
       relabel_configs:
         - source_labels: [__address__]
           target_label: __param_target
         - source_labels: [__param_target]
           target_label: instance
         - target_label: __address__
           replacement: 127.0.0.1:9326
   ```

4. **Run the exporter using Docker**:
   ```sh
   docker run -v config.yml:/app/config.yml ghcr.io/minefuto/junos-exporter
   ```
</details>
<details>

<summary>Pip</summary>

1. **Download `config.yml`**:
   ```sh
   curl -s -o ~/.junos-exporter/config.yml --create-dirs https://raw.githubusercontent.com/minefuto/junos-exporter/refs/heads/main/config.yml
   ```

2. **Edit `config.yml`**:
   ```yaml
   general:
     prefix: junos  # Prefix for the metrics
     timeout: 60    # Request timeout for the exporter

   credentials:
     vjunos: # Credential name
       username: admin  # Junos device's username
       password: admin@123  # Junos device's password
   ```

3. **Download `op/tables.yml`**:
   ```sh
   curl -s -o ~/.junos-exporter/op/tables.yml --create-dirs https://raw.githubusercontent.com/minefuto/junos-exporter/refs/heads/main/op/tables.yml
   ```

4. **Update prometheus configuration**:
   ```yaml
   scrape_configs:
     - job_name: "junos-exporter"
       static_configs:
         - targets:
             - "192.168.1.1"  # Target device
             - "192.168.1.2"
           labels:
             __params_credential: "vjunos"  # Credential name
             __params_module: "router"
       relabel_configs:
         - source_labels: [__address__]
           target_label: __param_target
         - source_labels: [__param_target]
           target_label: instance
         - target_label: __address__
           replacement: 127.0.0.1:9326
   ```

5. **Run the exporter**:
   ```sh
   junos-exporter
   ```
</details>

## Options

`junos-exporter` has several `uvicorn` options:

```
usage: junos-exporter [-h] [--host HOST] [--log-level {critical,error,warning,info,debug,trace}]
                      [--no-access-log] [--port PORT] [--reload] [--root-path ROOT_PATH] [--workers WORKERS]

options:
  -h, --help            Show this help message and exit
  --host HOST           Listen address [default: 0.0.0.0]
  --log-level           Log level [default: info]
  --no-access-log       Disable access log
  --port PORT           Listen port [default: 9326]
  --reload              Enable auto reload
  --root-path ROOT_PATH 
                        Root path [default: ""]
  --workers WORKERS     Number of worker processes [default: 1]
```

## Metrics

This exporter allows you configure the metrics to be scraped per Junos device.  
Add the module_name defined in the `modules` section of `config.yml` to the query parameter when scraping.  
e.g. http://localhost:9326/metrics?module=router&target=192.168.10.12&credential=vjunos


### Predefined Metrics

A default module named `router` is predefined, providing metrics such as:

- alarm information from `show system alarm/show chassis alarm`
- fpc status and cpu/memory utilization from `show chassis fpc`
- module information/status from `show chassis hardware/show chassis environment`
- routing engine status and cpu/memory utilization from `show chassis routing-engine`
- storage utilization from `show system storage`
- interface status/error/drop/statistics from `show interface extensive`
- interface tx/rx power from `show interface diatnostics optics`
- lldp status from `show lldp neighbor`
- lacp status from `show lacp interface`
- route count from `show route summary`
- arp information from `show arp expiration-time`
- ospf status/cost from `show ospf neighbor extensive/show ospf interface`
- bgp status/prefix count from `show bgp summary`
- vrrp status from `show vrrp`
- bfd status from `show bfd session`

By setting `__params_module` to `router` in the prometheus configuration, these metrics will be scraped.


### Custom Metrics

You can extract python objects from any command on Junos device and convert it into prometheus metrics by defining YAML configurations.  
Here is an example to create custom metrics using the `show interface extensive` command output.

1. **Define PyEZ Tables and Views**

   Place the YAML file for PyEZ Tables and Views in the following directories:

   - PyEZ structured & unstructured tables and views configuration files:
     - Docker: `/app/op/`
     - Pip: `~/.junos-exporter/op/`
   - TextFSM template files:
     - Docker: `/app/textfsm/`
     - Pip: `~/.junos-exporter/textfsm/`

   Below is the mapping information from the `show interface extensive` command to python objects:
   ```yaml
   PhysicalInterfaceStatus:
     rpc: get-interface-information
     args:
       extensive: True
       interface_name: '[afgxe][et]-*'
     key: name
     item: physical-interface
     view: PhysicalInterfaceStatusView

   PhysicalInterfaceStatusView:
     groups:
       traffic_statistics: traffic-statistics
       input_error_list: input-error-list
       output_error_list: output-error-list
       ethernet_pcs_statistics: ethernet-pcs-statistics
     fields:
       oper_status: oper-status
       admin_status: admin-status
       description: description
       speed: speed
       mtu: mtu
       link_mode: link-mode
       interface_flapped: interface-flapped
     fields_traffic_statistics:
       input_bytes: input-bytes
       input_packets: input-packets
       output_bytes: output-bytes
       output_packets: output-packets
     fields_input_error_list:
       input_errors: input-errors
       input_drops: input-drops
       framing_errors: framing-errors
       input_runts: input-runts
       input_discards: input-discards
       input_l3_incompletes: input-l3-incompletes
       input_l2_channel_errors: input-l2-channel-errors
       input_l2_mismatch_timeouts: input-l2-mismatch-timeouts
       input_fifo_errors: input-fifo-errors
       input_resource_errors: input-resource-errors
     fields_output_error_list:
       carrier_transitions: carrier-transitions
       output_errors: output-errors
       output_drops: output-drops
       collisions: output-collisions
       aged_packets: aged-packets
       mtu_errors: mtu-errors
       hs_link_crc_errors: hs-link-crc-errors
       output_fifo_errors: output-fifo-errors
       output_resource_errors: output-resource-errors
     fields_ethernet_pcs_statistics:
       bit_error_seconds: bit-error-seconds
       errored_blocks_seconds: errored-blocks-seconds
   ```

   Refer to Juniper's documentation for more details:
   - [Parsing Structured Output](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/task/junos-pyez-tables-op-defining.html)
   - [Parsing Unstructured Output](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/topic-map/junos-pyez-tables-op-unstructured-output-defining.html)
     - [Using TextFSM Templates](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/concept/junos-pyez-tables-op-using-textfsm-templates.html)

   **Currently unsupported features:**
   - Parsing unstructured output for `Target FPC`
   - Nested table definitions, such as [PyEZ LacpPortTable](https://github.com/Juniper/py-junos-eznc/blob/master/lib/jnpr/junos/op/lacp.yml)


2. **Map python objects to metrics**

   Edit the `optables` section in `config.yml`:
   ```yaml
   optables:
     PhysicalInterfaceStatus:  # PyEZ table name
       metrics:
         - name: interface_speed  # Metric name
           value: speed           # Metric value
           type: gauge            # Metric type (gauge, count, or untyped)
           help: Speed of show interfaces extensive  # Metric description
           value_transform:       # (Optional) Transform string values into floats
             100mbps: 100000000
             1000mbps: 1000000000
             1Gbps: 1000000000
             10Gbps: 10000000000
             100Gbps: 100000000000
             _: 0  # (Optional) Fallback value for unknown strings (default: NaN)
         - name: interface_lastflap_seconds
           value: interface_flapped
           type: counter
           help: Last flapped of show interfaces extensive
           to_unixtime: True  # Convert timestamps to Unix time
       labels:
         - name: interface  # (Optional) Label name
           value: name      # Label value
         - name: description
           value: description
   ```

   With the above configuration, the following metrics can be scraped:
   ```
   # HELP junos_interface_speed Speed of show interfaces extensive
   # TYPE junos_interface_speed gauge
   junos_interface_speed{interface="ge-0/0/0",description="description example"} 1000000000.0
   
   # HELP junos_interface_lastflap_seconds_total Last flapped of show interfaces extensive
   # TYPE junos_interface_lastflap_seconds_total counter
   junos_interface_lastflap_seconds_total{interface="ge-0/0/0",description="description example"} 1745734677000.0
   ```

   **Additional Notes:**
   - PyEZ Table key are automatically mapped to `key` and `name` and can be used in `metrics value` or `label value`.
     - op/tables.yml
       ```yaml
       RoutingEngineStatus:
         rpc: get-route-engine-information
         item: route-engine
         key: slot  <- !!
         view: RoutingEngineStatusView
       ```
     - config.yml
       ```yaml
         RoutingEngineStatus:
           metrics:
           -snip-
           labels:
             - name: slot
               value: key
       ```
   - If there are multiple keys, they will be assigned as `key.0`, `key.1`, etc.
     - op/tables.yml
     ```yaml
     LldpStatus:
       rpc: get-lldp-neighbors-information
       item: lldp-neighbor-information
       key:
         - lldp-local-port-id   # key.0, name.0
         - lldp-remote-port-id  # key.1, name.1
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
           value: key.0
         - name: remote_interface
           value: key.1
     ```

   - You can assign fixed values for the metric:
     - config.yml
     ```yaml
       HardwareStatus:
         metrics:
           - name: hardware_info
             value: 1  <- !!
             type: gauge
             help: Information of show chassis hardware
     ```

3. **Create a Module**

   Add the table name to the `modules` section in `config.yml`:
   ```yaml
   modules:
     custom_module:
       tables:
         - PhysicalInterfaceStatus
   ```

   Update prometheus configuration to use the new module:
   ```yaml
   scrape_configs:
     - job_name: "junos-exporter"
       static_configs:
         - targets:
             - "192.168.1.1"  # Target device
             - "192.168.1.2"
           labels:
             __params_credential: "vjunos"  # Credential name
             __params_module: "custom_module"
       relabel_configs:
         - source_labels: [__address__]
           target_label: __param_target
         - source_labels: [__param_target]
           target_label: instance
         - target_label: __address__
           replacement: 127.0.0.1:9326
   ```

## License

MIT

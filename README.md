# junos-exporter

![PyPI - Python Version](https://img.shields.io/pypi/pyversions/junos-exporter)
![PyPI](https://img.shields.io/pypi/v/junos-exporter)
![GitHub](https://img.shields.io/github/license/minefuto/junos-exporter)

## Overview

This exporter is designed to leverage the [PyEZ](https://github.com/Juniper/py-junos-eznc) framework to efficiently parse Junos device information into Prometheus metrics. By utilizing PyEZ Table and View, it seamlessly handles Junos-specific XML/RPC responses, ensuring accurate and structured data collection without the need for complex manual parsing. This architecture allows for high extensibility, making it simple to add custom metrics as your monitoring requirements evolve.

To allow `junos-exporter` connectivity via NETCONF over SSH, ensure the following configuration is applied to your Junos devices.
```
set system service netconf ssh
```

## Installation

```shell
pip install junos-exporter
```

## Usage

1. Setup the `config.yml`

   ```sh
   curl -s -o ~/.junos-exporter/config.yml --create-dirs https://raw.githubusercontent.com/minefuto/junos-exporter/refs/heads/main/config.yml
   ```

2. Configure the `config.yml`

   ```yaml
   general:
     prefix: junos          # Prefix prepended to all exported metric names
     timeout: 60            # Total timeout for Junos RPC execution and data collection
     timeout_socket: 15     # Timeout for establishing the initial NETCONF SSH connection
   
   credentials:
     default:
       username: admin      # Junos device login username
       password: admin@123  # Junos device login password
   ```

3. Configure the Prometheus

   ```yaml
   scrape_configs:
     - job_name: "junos-exporter"
       static_configs:
         - targets:
             - "192.168.1.1"  # Target device
             - "192.168.1.2,192.168.1.3"  # Multiple Target device such as dual RE
       relabel_configs:
         - source_labels: [__address__]
           target_label: __param_target
         - source_labels: [__param_target]
           regex: '^([^,]+).*'
           replacement: '$1'
           target_label: instance
         - target_label: __address__
           replacement: 127.0.0.1:9326
   ```
   When multiple targets are provided in a comma-separated list, if the first target is unreachable, it proceeds to the next one in the sequence.

4. Run the exporter

   ```sh
   junos-exporter
   ```

   For Docker users, use the following command
   ```sh
   docker run -d \
     -p 9326:9326 \
     -v /path/to/config.yml:/app/config.yml \
     ghcr.io/minefuto/junos-exporter
   ```

## CLI Options

The `junos-exporter` is powered by the uvicorn ASGI server. You can customize the server's behavior using the following command-line options.

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

## Credentials

This exporter allows you to configure specific authentication methods for each Junos device. To select a profile defined in the `credentials` section of your `config.yml`, add the credential query parameter to the scrape URL.  
e.g. http://localhost:9326/metrics?credential=vjunos&target=192.168.10.12
```yaml
credentials:
  default: # password authentication
    username: admin
    password: admin@123

  vjunos: # public key authentication
    username: admin
    private_key: ~/.ssh/id_rsa
    private_key_passphrase: admin@123 # option
```

In your Prometheus configuration, setting `__params_credential` to `vjunos` ensures the corresponding credentials are used.
If the `credential` parameter is omitted, the `default` profile will be used.
```yaml
scrape_configs:
  - job_name: "junos-exporter"
    static_configs:
      - targets:
          - "192.168.1.1"  # Target device using "default" credential
      - targets:
          - "192.168.1.2"  # Target device using "vjunos" credential
        labels:
          __params_credential: "vjunos"
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: 127.0.0.1:9326
```

## Metrics

This exporter allows you to configure which metrics are scraped for each Junos device.
To use a specific profile defined in the `modules` section of your `config.yml`, add the module query parameter to the scrape URL.  
e.g. http://localhost:9326/metrics?module=router&target=192.168.10.12

In your Prometheus configuration, setting `__params_module` to `router` ensures the corresponding modules are used.
If the `module` parameter is omitted, the `default` profile will be used.

### Predefined Metrics

A module named `default` is predefined in `config.yml`, providing metrics such as:

- alarm information from `show system alarm/show chassis alarm`
- fpc status and cpu/memory utilization from `show chassis fpc`
- module information/status from `show chassis hardware/show chassis environment`
- routing engine status and cpu/memory utilization from `show chassis routing-engine`
- storage utilization from `show system storage`
- interface status/error/drop/statistics from `show interface extensive`
- interface tx/rx power from `show interface diagnostics optics`
- lldp status from `show lldp neighbor`
- lacp status from `show lacp interface`
- route count from `show route summary`
- arp information from `show arp expiration-time`
- ospf status/cost from `show ospf neighbor extensive/show ospf interface`
- bgp status/prefix count from `show bgp summary`
- vrrp status from `show vrrp`
- bfd status from `show bfd session`

### Custom Metrics

You can define custom metrics by mapping Python objects extracted via Junos RPC commands to Prometheus metrics. This is done using YAML configurations in three steps.

1. Define PyEZ Tables and Views

   First, create YAML definitions to map Junos RPC outputs to Python objects. Place your YAML and TextFSM files in the designated directories.
   | Environment | PyEZ Tables Path        | TextFSM Template Path       |
   |:---         |:---                     |:---                         |
   |pip(local)   | `~/.junos-exporter/op/` | `~/.junos-exporter/textfsm` |
   |docker       | `/app/op/`              | `/app/textfsm/`             |
   
   Example PyEZ definition(`show interface extensive`)
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
   
   For more details, please refer to the Juniper documentation at the following paths:
   - [Parsing Structured Output](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/task/junos-pyez-tables-op-defining.html)
   - [Parsing Unstructured Output](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/topic-map/junos-pyez-tables-op-unstructured-output-defining.html)
     - [Using TextFSM Templates](https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/concept/junos-pyez-tables-op-using-textfsm-templates.html)
   
   Currently Unsupported Features:
   - The `target` parameter for Parsing Unstructured Output.
   - Parsing nested table, such as [PyEZ LacpPortTable](https://github.com/Juniper/py-junos-eznc/blob/master/lib/jnpr/junos/op/lacp.yml)

2. Map Python Objects to Prometheus Metrics

   Once your PyEZ tables are defined, register them in the `optables` section of your `config.yml`.
   ```yaml
   optables:
     PhysicalInterfaceStatus:  # PyEZ table name
       metrics:
         - name: interface_speed  # Metric name
           value: speed           # Metric value
           type: gauge            # Metric type (gauge, count, or untyped)
           help: Speed of show interfaces extensive  # Metric description
           value_transform:       # (Optional) Transform string values into numeric data
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
         - value: description
   ```
   
   With this configuration, the following metrics will be available for scraping.
   ```
   # HELP junos_interface_speed Speed of show interfaces extensive
   # TYPE junos_interface_speed gauge
   junos_interface_speed{interface="ge-0/0/0",description="description example"} 1000000000.0
   
   # HELP junos_interface_lastflap_seconds_total Last flapped of show interfaces extensive
   # TYPE junos_interface_lastflap_seconds_total counter
   junos_interface_lastflap_seconds_total{interface="ge-0/0/0",description="description example"} 1745734677000.0
   ```
   
3. Create a Module

   Once your tables are defined and mapped, group them into a `modules` section in `config.yml`. This allows you to apply specific sets of metrics to different devices.
   ```yaml
   modules:
     router:
       tables:
         - PhysicalInterfaceStatus
   ```
   Update your Prometheus configuration to instruct the exporter to use your new module by setting the `__params_module` label.
   ```yaml
   scrape_configs:
     - job_name: "junos-exporter"
       static_configs:
         - targets:
             - "192.168.1.1"  # Target device
             - "192.168.1.2"
           labels:
             __params_module: "router"
       relabel_configs:
         - source_labels: [__address__]
           target_label: __param_target
         - source_labels: [__param_target]
           target_label: instance
         - target_label: __address__
           replacement: 127.0.0.1:9326
   ```

### Additional Note on Metric Mapping
   
   Automatic Key Mapping
   - The key defined in your PyEZ Table is automatically mapped to `key` or `name`. You can use these values directly in your `metrics` or `labels` configuration.
   
   - PyEZ Definition(`op/tables.yml`)
     ```yaml
     RoutingEngineStatus:
       rpc: get-route-engine-information
       item: route-engine
       key: slot  # This becomes 'key'
       view: RoutingEngineStatusView
     ```
   
   - Exporter Config(`config.yml`)
     ```yaml
     RoutingEngineStatus:
       labels:
         - name: slot
           value: key  # References the 'slot' from the PyEZ table
     ```
   
   Handling Multiple Keys
   - If a PyEZ Table defines multiple keys (composite keys), they are assigned sequentially as key.0, key.1, etc.
   
   - PyEZ Definition(`op/tables.yml`)
     ```yaml
     LldpStatus:
       rpc: get-lldp-neighbors-information
       key:
         - lldp-local-port-id   # Assigned to key.0
         - lldp-remote-port-id  # Assigned to key.1
     ```
   
   - Exporter Config(`config.yml`)
     ```yaml
     LldpStatus:
       labels:
         - name: interface
           value: key.0
         - name: remote_interface
           value: key.1
     ```
   
   Constant Metric Values
   - You can assign a fixed numeric value to a metric. This is particularly useful for creating "Information" metrics that primarily provide metadata through labels.
     ```yaml
     HardwareStatus:
       metrics:
         - name: hardware_info
           value: 1  # Sets a constant value of 1.0
           type: gauge
           help: Information from 'show chassis hardware'
     ```

## License

MIT

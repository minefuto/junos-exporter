# junos-exporter

![PyPI - Python Version](https://img.shields.io/pypi/pyversions/junos-exporter)
![PyPI](https://img.shields.io/pypi/v/junos-exporter)
![GitHub](https://img.shields.io/github/license/minefuto/junos-exporter)

## Overview

This exporter turns Junos device information into Prometheus metrics. It collects NETCONF RPC replies and parses them with [pygxml](https://github.com/minefuto/pygxml), a streaming XML parser with gjson-style path queries. Everything about a metric -- which RPC to send, how to read records out of the reply, and how to expose them -- is declared in `config.yml`, so adding a metric never requires writing code.

To allow `junos-exporter` connectivity via NETCONF over SSH, ensure the following configuration is applied to your Junos devices.
```
set system service netconf ssh
```

## Installation

```shell
pip install junos-exporter
```

## Usage

1. Write your `config.yml`

   The exporter always loads the [bundled `config.yml`](junos_exporter/config.yml) first, which already defines 20 probes and the `default` module. Your own `config.yml` is layered on top of it, so it only needs to carry what you want to change -- usually just the credentials.

   ```sh
   mkdir -p ~/.junos-exporter && cat > ~/.junos-exporter/config.yml <<'EOF'
   credentials:
     default:
       username: admin      # Junos device login username
       password: admin@123  # Junos device login password
   EOF
   ```

   The exporter looks for `./config.yml` first, then `~/.junos-exporter/config.yml`, and layers the first one it finds.

2. Configure the `config.yml`
   > **Note**: 0.1.0 replaces the PyEZ Table/View format used up to 0.0.12. The RPC and the rules for reading its reply are now written as [pygxml](https://github.com/minefuto/pygxml) paths in the `probes` section of `config.yml` itself.

   Anything under `general` is overridden key by key, while `credentials`, `modules` and `probes` are overridden by the names directly under them -- a name that already exists in the bundled config is replaced whole.

   ```yaml
   general:
     prefix: junos          # Prefix prepended to all exported metric names
     timeout: 60            # Total timeout for Junos RPC execution and data collection
     timeout_socket: 15     # Timeout for establishing the initial NETCONF SSH connection
     # ssh_config: ~/.ssh/config  # SSH config for the NETCONF connection, e.g. to reach devices via a jump host

   credentials:
     default:
       username: admin      # Junos device login username
       password: admin@123  # Junos device login password
   ```

   To customize a probe, copy just that probe from the [bundled `config.yml`](junos_exporter/config.yml) into yours and edit it. Probes you do not copy keep following the bundled definitions as they are updated.

   > **Note**: Adding a probe is not enough on its own -- the bundled `modules.default` only lists the 20 bundled probes, so you also need to redefine the module that should collect it.

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
                      [--no-access-log] [--port PORT] [--reload] [--root-path ROOT_PATH] [--version]
                      [--workers WORKERS]

options:
  -h, --help            Show this help message and exit
  --host HOST           Listen address [default: 0.0.0.0]
  --log-level           Log level [default: info]
  --no-access-log       Disable access log
  --port PORT           Listen port [default: 9326]
  --reload              Enable auto reload
  --root-path ROOT_PATH 
                        Root path [default: ""]
  --version             Show version number and exit
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

### Selecting a module

This exporter allows you to configure which metrics are scraped for each Junos device.
To use a specific profile defined in the `modules` section of your `config.yml`, add the module query parameter to the scrape URL.  
e.g. http://localhost:9326/metrics?module=router&target=192.168.10.12

In your Prometheus configuration, setting `__params_module` to `router` ensures the corresponding modules are used.
If the `module` parameter is omitted, the `default` profile will be used.

### Bundled probes

The bundled [`config.yml`](junos_exporter/config.yml) defines 20 probes covering alarms, chassis, interfaces, LLDP/LACP, routing, ARP, OSPF, BGP, VRRP and BFD -- all of them in the `default` module. They are always loaded, so your own `config.yml` only needs to list the ones you want to change.

| probe | rpc | command |
| --- | --- | --- |
| `system_alarm_status` | `get-system-alarm-information` | `show system alarms` |
| `chassis_alarm_status` | `get-alarm-information` | `show chassis alarms` |
| `fpc_status` | `get-fpc-information` | `show chassis fpc` |
| `hardware_status` | `get-chassis-inventory` | `show chassis hardware` |
| `environment_status` | `get-environment-information` | `show chassis environment` |
| `routing_engine_status` | `get-route-engine-information` | `show chassis routing-engine` |
| `storage_status` | `get-system-storage` | `show system storage` |
| `physical_interface_status` | `get-interface-information` | `show interfaces extensive` |
| `logical_interface_status` | `get-interface-information` | `show interfaces detail` |
| `interface_optic_status` | `get-interface-optics-diagnostics-information` | `show interfaces diagnostics optics` |
| `lldp_status` | `get-lldp-neighbors-information` | `show lldp neighbors` |
| `lacp_status` | `get-lacp-interface-information` | `show lacp interfaces` |
| `route_status` | `get-route-summary-information` | `show route summary` |
| `arp_status` | `get-arp-table-information` | `show arp expiration-time` |
| `ospf_interface_status` | `get-ospf-interface-information` | `show ospf interface detail` |
| `ospf_neighbor_status` | `get-ospf-neighbor-information` | `show ospf neighbor extensive` |
| `bgp_status` | `get-bgp-summary-information` | `show bgp summary` |
| `bgp_route_status` | `get-bgp-summary-information` | `show bgp summary` |
| `vrrp_status` | `get-vrrp-information` | `show vrrp detail` |
| `bfd_status` | `get-bfd-session-information` | `show bfd session` |


### Defining a probe

A probe is one RPC plus the rules for reading its reply. The reply XML is scanned into **records** (a flat `path -> value` mapping), and each record becomes one metric sample.

Records are cut out of the reply like this:

- Each element matching `container` is a starting point, and `item` marks where a record **begins**. Junos often emits records without a wrapping element, so a record is not necessarily the subtree of the `item` element.
- Every `path` written in `metrics` and `labels` is looked up in three places and the **first hit wins**: **(a)** inside the `item` element, **(b)** among the siblings that follow it, up to the next `item`, **(c)** among the ancestor's children that appeared before that `item` -- which is how a parent's values are inherited by its records.
- A path that resolves nowhere is simply left out of the record, and the metric or label using it is not exposed for that record.

For example, `show vrrp detail` returns each VR as a `vrrp-vlan` element followed by loose siblings, and the second VR reports its mode under `active-inherit` instead: 

```xml
<vrrp-information>
  <vrrp-interface>
    <vrrp-vlan>
      <physical-interface>xe-0/0/0</physical-interface>
      <unit>1</unit>
    </vrrp-vlan>
    <interface-state>up</interface-state>
    <group>10</group>
    <vrrp-state>master</vrrp-state>
    <vrrp-mode>active</vrrp-mode>
    <vrrp-vlan>
      <physical-interface>xe-0/0/0</physical-interface>
      <unit>2</unit>
    </vrrp-vlan>
    <interface-state>up</interface-state>
    <group>20</group>
    <vrrp-state>master</vrrp-state>
    <active-inherit>
      <vrrp-mode>inherit</vrrp-mode>
    </active-inherit>
  </vrrp-interface>
</vrrp-information>
```

The bundled `vrrp_status` probe reads it as follows. `physical-interface` and `unit` are found by rule (a), the rest by rule (b), and the `mode` label lists two paths so that the second record falls back to the nested one.

```yaml
probes:
  vrrp_status:
    rpc: get-vrrp-information
    container: vrrp-interface
    item: vrrp-vlan
    metrics:
      - name: vrrp_state
        path: vrrp-state
        type: gauge
        help: "VR State of show vrrp detail(master: 5, backup: 4, transition: 3, bringup: 2, init: 1, idle: 0)"
        value_transform:
          "master": 5
          "backup": 4
          "transition": 3
          "bringup": 2
          "init": 1
          "idle": 0
    labels:
      - name: interface
        path: physical-interface
      - name: unit
        path: unit
      - name: mode
        path:
          - vrrp-mode
          - active-inherit.vrrp-mode
      - name: group
        path: group
```

Which is exposed as:

```
# HELP junos_vrrp_state VR State of show vrrp detail(master: 5, backup: 4, transition: 3, bringup: 2, init: 1, idle: 0)
# TYPE junos_vrrp_state gauge
junos_vrrp_state{interface="xe-0/0/0",unit="1",mode="active",group="10"} 5.0
junos_vrrp_state{interface="xe-0/0/0",unit="2",mode="inherit",group="20"} 5.0
```

#### probes

| key | type | default | description |
| --- | --- | --- | --- |
| `rpc` | str | required | RPC name, sent as `<rpc-name format="xml-minified">` |
| `args` | dict | `{}` | RPC arguments. `_` in a key becomes `-`. A value of `true` emits an empty element, anything else emits `<key>value</key>` |
| `container` | str | `""` | Path to the parent of the records, `.` separated and relative to the child level of the reply root. Every match is used |
| `item` | str \| list[str] | required | Element name that begins a record |
| `recursive` | bool | `false` | Also look for `item` further down the tree, for replies nested to an arbitrary depth such as `show chassis hardware` |
| `metrics` | list | `[]` | Metric definitions, see below |
| `labels` | list | `[]` | Label definitions applied to every metric of the probe, see below |

#### paths

Both `metrics` and `labels` read their value through a `path` relative to the record, which takes one of these forms:

```yaml
      - name: peer_state
        path: peer-state                        # child element
      - name: input_bytes
        path: traffic-statistics.input-bytes    # nested element
      - name: config_flags
        path: if-config-flags.@flag             # attribute
      - name: mode
        path:                                   # first path that resolves wins
          - vrrp-mode
          - active-inherit.vrrp-mode
      - name: iff_up
        path: if-config-flags.iff-up            # presence instead of value
        exists: True
```

A path with `exists: True` yields the string `"true"` or `"false"` rather than the element value, so it always resolves. It cannot be combined with a list of paths.

#### metrics

| key | type | default | description |
| --- | --- | --- | --- |
| `name` | str | required | Exposed as `<prefix>_<name>`, with `_total` appended when `type` is `counter` |
| `path` | str \| list[str] | required unless `value` | Path to the value, see above |
| `value` | float | required unless `path` | Constant to expose instead of reading the reply |
| `exists` | bool | `false` | Expose whether `path` resolved, as `"true"` or `"false"` |
| `type` | str | `untyped` | `untyped`, `counter` or `gauge` |
| `help` | str | `""` | Text of the HELP line |
| `regex` | str | none | Applied to the value. Capture group 1 is used if present, otherwise the whole match. A value that does not match is dropped |
| `value_transform` | dict | none | Maps a string value to a number. The `_` key sets a default, otherwise an unlisted value becomes `NaN` |
| `to_unixtime` | bool | `false` | Reads the value as `YYYY-MM-DD HH:MM:SS`, `<w>w<d>d HH:MM:SS`, `<d>d HH:MM:SS` or `HH:MM:SS` and converts it to unix time in milliseconds. Anything else becomes `0` |

#### labels

| key | type | default | description |
| --- | --- | --- | --- |
| `name` | str | required | Label name |
| `path` | str \| list[str] | required | Path to the label value, see above |
| `exists` | bool | `false` | Expose whether `path` resolved, as `"true"` or `"false"` |
| `regex` | str | none | Applied to the label value. Capture group 1 is used, and the label is omitted when it does not match |

A label whose path is missing from the record is omitted. Splitting `xe-0/0/0.1` into a physical interface and a unit:

```yaml
    labels:
      - name: interface
        path: name
        regex: ([^\.]*).*
      - name: unit
        path: name
        regex: .*\.(\d+)
```

### Checking a probe definition

Get the RPC reply from the device itself to work out what to write.

```
# The RPC name to put in `rpc:`
show vrrp detail | display xml rpc

# The reply XML, to work out container / item / path
show vrrp detail | display xml
```

Then `/debug` shows the records the current definition extracts from that reply.

```sh
curl 'localhost:9326/debug?target=192.168.1.1&probe=vrrp_status'
```

## License

MIT

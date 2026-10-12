class ExporterError(Exception):
    """Raised when the exporter itself is at fault.

    Undefined modules and credentials. These surface as HTTP 500 so that the
    scrape fails and `up` becomes 0.
    """


class DeviceError(Exception):
    """Raised when the scrape session against the target could not complete.

    The device was unreachable, refused the connection, or dropped it mid
    scrape. These surface as HTTP 200 with `<prefix>_up 0`.
    """


class RpcError(Exception):
    """Raised when the device gave no usable answer to a single rpc.

    An rpc-error, or a missing or empty rpc-reply. The session itself is
    healthy and only that probe's `<prefix>_rpc_success` becomes 0.
    """

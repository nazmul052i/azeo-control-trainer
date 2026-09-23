"""Field I/O transports: the wire between the store and real equipment.

Nothing here models a process. A transport moves values between
`SharedDataStore` and something outside the controller — a Modbus device, an
OPC UA server, real I/O — and that is the whole of its job. It sits exactly
where `plant/driver.py` sits, on the far side of the store, which is what
lets the same control modules drive a simulation or a field device without
knowing which they have.
"""

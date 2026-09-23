"""Product identity.

One place for the names a user or a connected client sees, so a rename means
editing this file rather than hunting through the UI, the drawings and the OPC
UA server.

The OPC UA *address space* root is deliberately not here. That is ``PLANT_SIM``,
fixed by the tag workbook, and it is a contract with the DCS rather than
branding: renaming the product must not move a node a controller database is bound
to. See :mod:`azeoplant.opc.server`.
"""

from __future__ import annotations

PRODUCT_NAME = "AzeoPlant"
PRODUCT_TITLE = "AzeoPlant Closed Loop Process Simulator"

#: Advertised as the OPC UA server name to a connecting client.
OPC_SERVER_NAME = "AzeoPlant Closed Loop Process Simulator"

# ===========
# pysap - Python library for crafting SAP's network protocols packets
#
# Copyright (C) 2014 Core Security Technologies
#
# The library was designed and developed by Martin Gallo from the Security
# Consulting Services team of Core Security Technologies.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# ==============

# Standard imports
import re
import logging
# External imports
from scapy.layers.inet import TCP
from scapy.packet import Packet, bind_layers
from scapy.supersocket import socket, StreamSocket
from scapy.fields import (ByteField, ShortField, ConditionalField, StrField,
    IntField, StrNullField, PacketListField, FieldLenField, FieldListField,
    SignedIntEnumField, StrFixedLenField, PacketField)
# Custom imports
from pysap.SAPSNC import SAPSNCFrame
from pysap.SAPNI import SAPNI, SAPNIStreamSocket
from pysap.utils import PacketNoPadded, ByteEnumKeysField, StrNullFixedLenField


# Create a logger for the SAPRouter layer
log_saprouter = logging.getLogger("pysap.saprouter")


# Router Opcode values
router_control_opcodes = {
    0: "Error information",
    1: "Version request",
    2: "Version response",
    5: "Send Handle (5)",            # TODO: Check this opcodes
    6: "Send Handle (6)",            # TODO: Check this opcodes
    8: "Send Handle (8)",            # TODO: Check this opcodes
    70: "SNC request",
    71: "SNC handshake complete",
}
"""Router Opcode values"""


# Router Return Code values (as per SAP Note 63342 http://service.sap.com/sap/support/notes/63342)
router_return_codes = {
    -1: "NI-internal error (NIEINTERN)",
    -2: "Host name unknown (NIEHOST_UNKNOWN)",
    -3: "Service unknown (NIESERV_UNKNOWN)",
    -4: "Service already used (NIESERV_USED)",
    -5: "Time limit reached (NIETIMEOUT)",
    -6: "Connection to partner broken (NIECONN_BROKEN)",
    -7: "Data range too small (NIETOO_SMALL)",
    -8: "Invalid parameters (NIEINVAL)",
    -9: "Wake-Up (without data) (NIEWAKEUP)",
    -10: "Connection setup failed (NIECONN_REFUSED)",
    -11: "PING/PONG signal received (NIEPING)",
    -12: "Connection to partner via NiRouter not yet set up (NIECONN_PENDING)",
    -13: "Invalid version (NIEVERSION)",
    -14: "Local hostname cannot be found (NIEMYHOSTNAME)",
    -15: "No free port in range (NIENOFREEPORT)",
    -16: "Local hostname invalid (NIEMYHOST_VERIFY)",
    -17: "Error in the SNC shift in the saprouter ==> (NIESNC_FAILURE)",
    -18: "Opcode received (NIEOPCODE)",
    -19: "queue limit reached, next package not accepted (NIEQUE_FULL)",
    -20: "Requested package too large (NIETOO_BIG)",
    -90: "Host name unknown (NIEROUT_HOST_UNKNOWN)",
    -91: "Service unknown (NIEROUT_SERV_UNKNOWN)",
    -92: "Connection setup failed (NIEROUT_CONN_REFUSED)",
    -93: "NI-internal errors (NIEROUT_INTERN)",
    -94: "Connect from source to destination not allowed (NIEROUT_PERM_DENIED)",
    -95: "Connection terminated (NIEROUT_CONN_BROKEN)",
    -96: "Invalid client version (NIEROUT_VERSION)",
    -97: "Connection cancelled by administrator (NIEROUT_CANCELED)",
    -98: "saprouter shutdown (NIEROUT_SHUTDOWN)",
    -99: "Information request refused (NIEROUT_INFO_DENIED)",
    -100: "Max. number of clients reached (NIEROUT_OVERFLOW)",
    -101: "Talkmode not allowed (NIEROUT_MODE_DENIED)",
    -102: "Client not available (NIEROUT_NOCLIENT)",
    -103: "Error in external library (NIEROUT_EXTERN)",
    -104: "Error in the SNC shift (NIEROUT_SNC_FAILURE)",
}
"""Router Return Code values"""


# Router Administration Command values
router_adm_commands = {
    2: "Information Request",
    3: "New Route Table Request",
    4: "Toggle Trace Request",
    5: "Stop Request",
    6: "Cancel Route Request",
    7: "Dump Buffers Request",
    8: "Flush Buffers Request",
    9: "Soft Shutdown Request",
    10: "Set Trace Peer",
    11: "Clear Trace Peer",
    12: "Trace Connection",
    13: "Trace Connection",
    14: "Hide Error Information Request",
}
"""Router Administration Command values"""


# Router NI Talk mode values
router_ni_talk_mode_values = {
    0: "NI_MSG_IO",
    1: "NI_RAW_IO",
    2: "NI_ROUT_IO",
}
"""Router NI Talk mode values"""


class SAPRouterRouteHop(PacketNoPadded):
    """SAP Router Protocol Route Hop

    This packet is used to describe a hop in a route using the SAP Router.
    """
    name = "SAP Router Route Hop"
    fields_desc = [
        StrNullField("hostname", None),
        StrNullField("port", None),
        StrNullField("password", None),
    ]

    regex = re.compile(r"""
        (/H/(?P<hostname>[\w\.]+)              # Hostname, FQDN or IP addresss
        (/S/(?P<port>[\w]+))?                  # Optional port/service
        (/[PW]/(?P<password>[\w.]+))?          # Optional password
        )
    """, re.VERBOSE)
    """ @cvar: Regular expression for matching route strings
        @type: regex
    """

    @classmethod
    def from_string(cls, route_string):
        """Build a list of route hops from a route string. The format of a
        route string is:

        (/H/host/S/service/W/pass)*

        or for older versions (<4.0):

        (/H/host/S/service/P/pass)*

        @param route_string: route string
        @type route_string: C{string}

        @return: route hops in the route string
        @rtype: C{list} of L{SAPRouterRouteHop}
        """
        result = []
        for route_hop in [x.groupdict() for x in cls.regex.finditer(route_string)]:
            result.append(cls(hostname=route_hop["hostname"],
                              port=route_hop["port"],
                              password=route_hop["password"]))
        return result

    @classmethod
    def from_hops(cls, route_hops):
        """Build a route string from a list of route hops.

        @param route_hops: route hops
        @type route_hops: C{list} of L{SAPRouterRouteHop}

        @return: route string
        @rtype: C{string}
        """
        result = ""
        for route_hop in route_hops:
            result += "/H/%s" % route_hop.hostname
            if route_hop.port:
                result += "/S/%s" % route_hop.port
            if route_hop.password:
                result += "/W/%s" % route_hop.password
        return result


class SAPRouterError(PacketNoPadded):
    """SAP Router Protocol Error Text

    This packet is used to describe an error returned by SAP Router.
    """
    name = "SAP Router Error Text"
    fields_desc = [
        StrNullField("eyecatcher", "*ERR*"),
        StrNullField("counter", "1"),
        StrNullField("error", ""),
        StrNullField("return_code", ""),
        StrNullField("component", "NI (network interface)"),
        StrNullField("release", ""),
        StrNullField("version", ""),
        StrNullField("module", "nirout.cpp"),
        StrNullField("line", ""),
        StrNullField("detail", ""),
        StrNullField("error_time", ""),
        StrNullField("XXX1", ""),
        StrNullField("XXX2", ""),
        StrNullField("XXX3", ""),
        StrNullField("XXX4", ""),
        StrNullField("location", ""),
        StrNullField("XXX5", ""),
        StrNullField("XXX6", ""),
        StrNullField("XXX7", ""),
        StrNullField("XXX8", ""),
        StrNullField("eyecatcher", "*ERR*"),
    ]

    time_format = "%a %b %d %H:%M:%S %Y"
    """ @cvar: Format to use when building the time field
        @type: C{string}
    """


def router_is_route(pkt):
    """Returns if the packet is a Route packet.

    @param pkt: packet to look at
    @type pkt: L{SAPRouter}

    @return: if the type of the packet is Route
    @rtype: C{bool}
    """
    return pkt.type == SAPRouter.SAPROUTER_ROUTE


def router_is_admin(pkt):
    """Returns if the packet is a Admin packet.

    @param pkt: packet to look at
    @type pkt: L{SAPRouter}

    @return: if the type of the packet is Admin
    @rtype: C{bool}
    """
    return pkt.type == SAPRouter.SAPROUTER_ADMIN


def router_is_error(pkt):
    """Returns if the packet is a Error Information packet.

    @param pkt: packet to look at
    @type pkt: L{SAPRouter}

    @return: if the type of the packet is Error
    @rtype: C{bool}
    """
    return pkt.type == SAPRouter.SAPROUTER_ERROR


def router_is_control(pkt):
    """Returns if the packet is a Control packet.

    @param pkt: packet to look at
    @type pkt: L{SAPRouter}

    @return: if the type of the packet is Control
    @rtype: C{bool}
    """
    return pkt.type == SAPRouter.SAPROUTER_CONTROL


def router_is_pong(pkt):
    """Returns if the packet is a Pong (route accepted) packet.

    @param pkt: packet to look at
    @type pkt: L{SAPRouter}

    @return: if the type of the packet is Pong
    @rtype: C{bool}
    """
    return pkt.type == SAPRouter.SAPROUTER_PONG


def router_is_known_type(pkt):
    """Returns if the packet is of a known type (Admin, Route, Error or Pong).

    @param pkt: packet to look at
    @type pkt: L{SAPRouter}

    @return: if the type of the packet is known
    @rtype: C{bool}

    """
    return pkt.type in SAPRouter.router_type_values


class SAPRouter(Packet):
    """SAP Router packet

    This packet is used for general SAP Router packets. There are (at least) five
    types of SAP Router packets:

        1. Route packets. For requesting the routing of a connection to a remote
        hosts. The packet contains some general information and a connection string
        with a list of routing hops (L{SAPRouterRouteHop}).

        2. Administration packets. This packet is used for the SAP Router to send
        administrative commands. It's suppose to be used only from the hosts
        running the SAP Router or when an specific route is included in the
        routing table. Generally administration packets are not accepted from the
        external binding.

        3. Error Information packets. Packets sent when an error occurred.

        4. Control Message packets. Used to perform some control activities, like
        retrieving the current SAPRouter version or to perform the SNC handshake.
        They have the same structure that error information packets.

        5. Route accepted packet. Used to acknowledge a route request ("NI_PONG").


    Routed packets and some responses doesn't fill in these five packet
    types. For identifying those cases, you should check the type using the
    function L{router_is_known_type}.

    NI Versions found (unconfirmed):
        - 36: Release <6.20
        - 38: Release 7.00/7.10
        - 40: Release 7.20/7.21
    """

    # Constants for router types
    SAPROUTER_ROUTE = "NI_ROUTE"
    """ @cvar: Constant for route packets
        @type: C{string} """

    SAPROUTER_ADMIN = "ROUTER_ADM"
    """ @cvar: Constant for administration packets
        @type: C{string} """

    SAPROUTER_ERROR = "NI_RTERR"
    """ @cvar: Constant for error information packets
        @type: C{string} """

    SAPROUTER_CONTROL = "NI_RTERR"
    """ @cvar: Constant for control messages packets
        @type: C{string} """

    SAPROUTER_PONG = "NI_PONG"
    """ @cvar: Constant for route accepted packets
        @type: C{string} """

    router_type_values = [
        SAPROUTER_ADMIN,
        SAPROUTER_ERROR,
        SAPROUTER_CONTROL,
        SAPROUTER_ROUTE,
        SAPROUTER_PONG,
    ]
    """ @cvar: List of known packet types
        @type: C{list} of C{string} """

    name = "SAP Router"
    fields_desc = [
        # General fields present in all SAP Router packets
        StrNullField("type", SAPROUTER_ROUTE),

        ConditionalField(ByteField("version", 0x02), lambda pkt:router_is_known_type(pkt) and not router_is_pong(pkt)),

        # Route packets
        ConditionalField(ByteField("route_ni_version", 0x28), router_is_route),
        ConditionalField(ByteField("route_entries", 0), router_is_route),
        ConditionalField(ByteEnumKeysField("route_talk_mode", 0, router_ni_talk_mode_values), router_is_route),
        ConditionalField(ShortField("route_padd", 0), router_is_route),
        ConditionalField(ByteField("route_rest_nodes", 0), router_is_route),
        ConditionalField(FieldLenField("route_length", 0, length_of="route_string", fmt="I"), router_is_route),
        ConditionalField(IntField("route_offset", 0), router_is_route),
        ConditionalField(PacketListField("route_string", None, SAPRouterRouteHop,
                                         length_from=lambda pkt:pkt.route_length), router_is_route),

        # Admin packets
        ConditionalField(ByteEnumKeysField("adm_command", 0x02, router_adm_commands), router_is_admin),
        ConditionalField(ShortField("adm_unused", 0x00), lambda pkt:router_is_admin(pkt) and pkt.adm_command not in [10, 11, 12, 13]),

        # Info Request fields
        ConditionalField(StrNullFixedLenField("adm_password", "", 19), lambda pkt:router_is_admin(pkt) and pkt.adm_command in [2]),

        # Cancel Route fields
        ConditionalField(FieldLenField("adm_client_count", None, count_of="adm_client_ids", fmt="H"), lambda pkt:router_is_admin(pkt) and pkt.adm_command in [6]),
        ConditionalField(FieldListField("adm_client_ids", [0x00], IntField("", 0), count_from=lambda pkt:pkt.adm_client_count), lambda pkt:router_is_admin(pkt) and pkt.adm_command in [6]),

        # Trace Connection fields
        ConditionalField(FieldLenField("adm_client_count", None, count_of="adm_client_ids", fmt="I"), lambda pkt:router_is_admin(pkt) and pkt.adm_command in [12, 13]),
        ConditionalField(FieldListField("adm_client_ids", [0x00], IntField("", 0), count_from=lambda pkt:pkt.adm_client_count), lambda pkt:router_is_admin(pkt) and pkt.adm_command in [12, 13]),

        # Set/Clear Peer Trace fields  # TODO: Check whether this field should be a IPv6 address or another proper field
        ConditionalField(StrFixedLenField("adm_address_mask", "", 32), lambda pkt:router_is_admin(pkt) and pkt.adm_command in [10, 11]),

        # Error Information/Control Messages fields
        ConditionalField(ByteEnumKeysField("opcode", 0, router_control_opcodes), lambda pkt: router_is_error(pkt) or router_is_control(pkt)),
        ConditionalField(ByteField("opcode_padd", 0), lambda pkt: router_is_error(pkt) or router_is_control(pkt)),
        ConditionalField(SignedIntEnumField("return_code", 0, router_return_codes), lambda pkt: router_is_error(pkt) or router_is_control(pkt)),

        # Error Information fields
        ConditionalField(FieldLenField("err_text_length", None, length_of="err_text_value", fmt="!I"), lambda pkt: router_is_error(pkt) and pkt.opcode == 0),
        ConditionalField(PacketListField("err_text_value", SAPRouterError(), SAPRouterError,
                                         length_from=lambda pkt:pkt.err_text_length), lambda pkt: router_is_error(pkt) and pkt.opcode == 0),

        # Control Message fields
        ConditionalField(IntField("control_text_length", 0), lambda pkt: router_is_control(pkt) and pkt.opcode != 0),
        ConditionalField(StrField("control_text_value", "*ERR"), lambda pkt: router_is_control(pkt) and pkt.opcode != 0),

        # SNC Frame fields
        ConditionalField(PacketField("snc_frame", None, SAPSNCFrame), lambda pkt: router_is_control(pkt) and pkt.opcode in [70, 71])
    ]


# Retrieve the version of the remote SAP Router
def get_router_version(connection):
    """Helper function to retrieve the version of a remote SAP Router.

    @param connection: connection with the SAP Router
    @type connection: L{SAPNIStreamSocket}

    @return: version or None
    """
    response = connection.sr(SAPRouter(type=SAPRouter.SAPROUTER_CONTROL,
                                       version=40,
                                       opcode=1))
    response.decode_payload_as(SAPRouter)
    if router_is_control(response) and response.opcode == 2:
        return response.version
    else:
        return None


class SAPRouteException(Exception):
    """Exception for SAP Router routing errors"""


class SAPRoutedStreamSocket(SAPNIStreamSocket):
    """Stream socket implementation for a connection routed through a SAP
    Router server. It works by wrapping a L{SAPNIStreamSocket} and connecting
    first to the SAP Router given a route string or list of L{SAPRouterRouteHop}.
    """

    desc = "NI Stream socket routed trough a SAP Router"

    def __init__(self, sock, route, talk_mode=None, router_version=None,
                 keep_alive=True, base_cls=None):
        """Initialize the routed stream socket. It should receive a socket
        connected with the SAP Router, and a route to specified to it. After
        initialization all calls to send() and recv() would be made to the
        target host/service and to the SAP Router itself.

        @param sock: a socket connected to the SAP Router
        @type sock: C{socket}

        @param route: a route to specify to the SAP Router
        @type route: C{list} of L{SAPRouterRouteHop}

        @param talk_mode: the talk mode to use when routing
        @type talk_mode: C{int}

        @param router_version: the router version to use for requesting the route
        @type router_version: C{int}

        @param keep_alive: if true, the socket will automatically respond to
            keep-alive request messages. Otherwise, the keep-alive messages
            are passed to the caller in L{recv} and L{sr} calls.
        @type keep_alive: C{bool}

        @param base_cls: the base class to use when receiving packets, it uses
            SAPNI as default if no class specified
        @type base_cls: L{Packet} class

        """
        self.routed = False
        self.talk_mode = talk_mode
        self.router_version = router_version
        # Connect to the SAP Router
        SAPNIStreamSocket.__init__(self, sock, keep_alive=keep_alive,
                                   base_cls=base_cls)
        # Now that we've a NIStreamSocket, retrieve the router version if
        # was not specified
        if self.router_version is None:
            self.router_version = get_router_version(self)
        self.route_to(route, talk_mode)

    def route_to(self, route, talk_mode):
        """Make the route request to the target host/service.

        @param route: a route to specify to the SAP Router
        @type route: C{list} of L{SAPRouterRouteHop}

        @param talk_mode: the talk mode to use when routing
        @type talk_mode: C{int}
        """
        # Build the route request packet
        talk_mode = talk_mode or 0
        router_strings = list(map(str, route))
        target = "%s:%d" % (route[-1].hostname, int(route[-1].port))
        router_strings_lens = list(map(len, router_strings))
        route_request = SAPRouter(type=SAPRouter.SAPROUTER_ROUTE,
                                  route_ni_version=self.router_version,
                                  route_entries=len(route),
                                  route_talk_mode=talk_mode,
                                  route_rest_nodes=1,
                                  route_length=sum(router_strings_lens),
                                  route_offset=router_strings_lens[0],
                                  route_string=route)
        log_saprouter.debug("Requesting route to %s using mode %d (%s)",
                            target, talk_mode, router_ni_talk_mode_values[talk_mode])
        # Send the request and grab the response
        response = self.sr(route_request)
        response.decode_payload_as(SAPRouter)
        if SAPRouter in response:
            response = response[SAPRouter]
            if router_is_pong(response):
                self.routed = True
                log_saprouter.debug("Route to %s accepted", target)
            elif router_is_error(response) and response.return_code == -94:
                log_saprouter.debug("Route to %s denied", target)
                raise SAPRouteException("Route request not accepted")
            else:
                log_saprouter.warning("Error requesting route to %s", target)
                raise Exception("Router error:", response.err_text_value)
        else:
            log_saprouter.warning("Error requesting route to %s", target)
            raise Exception("Wrong response received")

    def recv(self):
        """Receive a packet from the target host. If the talk mode in use is
        native and we've already set the route, the packet received is a raw
        packet. Otherwise, the packet received is a NI layer packet in the same
        way the L{SAPNIStreamSocket} works.
        """
        # If we're working on native mode and the route was accepted, we don't
        # need the NI layer anymore. Just use the plain socket inside the
        # NIStreamSockets.
        if self.routed:
            if self.talk_mode == 1:
                return StreamSocket.recv(self)
        # If the route was not accepted yet or we're working on non-native talk
        # mode, we need the NI layer.
        return SAPNIStreamSocket.recv(self)

    @classmethod
    def get_nisocket(cls, host=None, port=None, route=None, password=None,
                     talk_mode=None, router_version=None, **kwargs):
        """Helper function to obtain a L{SAPRoutedStreamSocket}.

        @param host: target host to connect to if not specified in the route
        @type host: C{string}

        @param port: target port to connect to if not specified in the route
        @type port: C{int}

        @param route: route to use for determining the SAP Router to connect
        @type route: C{string} or C{list} of L{SAPRouterRouteHop}

        @param password: target password if not specified in the route
        @type password: C{string}

        @param talk_mode: the talk mode to use for requesting the route
        @type talk_mode: C{int}

        @param router_version: the router version to use for requesting the route
        @type router_version: C{int}

        @keyword kwargs: arguments to pass to L{SAPRoutedStreamSocket} constructor

        @return: connected socket through the specified route
        @rtype: L{SAPRoutedStreamSocket}
        """
        # If no route was provided, use the standard SAPNIStreamSocket
        # get_nisocket method
        if route is None:
            return SAPNIStreamSocket.get_nisocket(host, port, **kwargs)

        # If the route was provided using a route string, convert it to a
        # list of hops
        if isinstance(route, str):
            route = SAPRouterRouteHop.from_string(route)

        # If the host and port were specified, we need to add a new hop to
        # the route
        if host is not None and port is not None:
            route.append(SAPRouterRouteHop(hostname=host,
                                           port=port,
                                           password=password))

        # Connect to the first hop in the route (it should be the SAP Router)
        sock = socket.socket()
        sock.connect((route[0].hostname, int(route[0].port)))

        # Create a SAPRoutedStreamSocket instance specifying the route
        return cls(sock, route, talk_mode, router_version, **kwargs)


# Bind SAP NI with the SAP Router port
bind_layers(TCP, SAPNI, dport=3299)

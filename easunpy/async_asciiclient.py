# easunpy/async_asciiclient.py
# Asynchronous client for Voltronic ASCII-based inverters.

"""Asynchronous ASCII client for inverter protocol."""
import asyncio
import logging
from typing import Optional

from .crc_xmodem import crc16_xmodem, adjust_crc_byte

logger = logging.getLogger(__name__)

class AsyncAsciiClient:
    """
    Handles the async communication with an ASCII-based inverter.
    """
    def __init__(self, inverter_ip: str, local_ip: str, port: int = 502):
        self.inverter_ip = inverter_ip
        self.local_ip = local_ip
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._connection_established = asyncio.Event()
        self._server_lock = asyncio.Lock()
        self._cmd_lock = asyncio.Lock()  # Lock to ensure sequential command execution
        self._transaction_id = 0x15a8

    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return self._connection_established.is_set()

    async def _handle_connection(self, reader, writer):
        """Callback to handle a new client connection."""
        if self.is_connected():
            logger.warning("Another connection attempted while one is active. Closing new one.")
            writer.close()
            await writer.wait_closed()
            return

        peername = writer.get_extra_info('peername')
        logger.info(f"Inverter connected from {peername}")
        self._reader = reader
        self._writer = writer
        self._connection_established.set()

        try:
            await writer.wait_closed()
        except Exception as e:
            logger.error(f"Error in wait_closed for {peername}: {e}")
        finally:
            logger.info(f"Connection from {peername} closed.")
            self._connection_established.clear()
            self._reader = None
            self._writer = None

    async def ensure_connection(self):
        """
        Ensures the server is running and sends a discovery packet.
        This method is non-blocking and safe to call on every update.
        """
        async with self._server_lock:
            if self._server is None:
                try:
                    self._server = await asyncio.start_server(
                        self._handle_connection, self.local_ip, self.port
                    )
                    logger.info(f"Listening on {self.local_ip}:{self.port} for inverter connection...")
                except OSError as e:
                    logger.error(f"Failed to start server on {self.local_ip}:{self.port}. Error: {e}")
                    self._server = None
                    return

        try:
            udp_message = f"set>server={self.local_ip}:{self.port};".encode('ascii')
            loop = asyncio.get_event_loop()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: asyncio.DatagramProtocol(),
                remote_addr=(self.inverter_ip, 58899)
            )
            transport.sendto(udp_message)
            transport.close()
            logger.debug(f"Sent discovery packet to {self.inverter_ip}:58899")
        except Exception as e:
            logger.error(f"Failed to send UDP discovery packet: {e}")

    def _build_command_packet(self, command: str) -> bytes:
        """Builds the command packet with wrapper and CRC."""
        trans_id = self._transaction_id
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        command_bytes = command.encode('ascii')

        crc = crc16_xmodem(command_bytes)
        crc_high = adjust_crc_byte((crc >> 8) & 0xFF)
        crc_low = adjust_crc_byte(crc & 0xFF)

        data = command_bytes + bytes([crc_high, crc_low, 0x0D])
        length = len(data) + 2

        packet = bytearray([
            (trans_id >> 8) & 0xFF, trans_id & 0xFF,
            0x00, 0x01,
            (length >> 8) & 0xFF, length & 0xFF,
            0xFF, 0x04
        ]) + data

        return bytes(packet)

    async def send_command(self, command: str) -> str:
        """Sends a command and returns the parsed ASCII response."""
        if not self.is_connected() or self._writer is None or self._reader is None:
            raise ConnectionError("Cannot send command: Not connected.")

        # Ensure only one command is sent at a time
        async with self._cmd_lock:
            packet = self._build_command_packet(command)
            logger.debug(f"Sending command '{command}': {packet.hex()}")

            try:
                self._writer.write(packet)
                await self._writer.drain()

                header = await asyncio.wait_for(self._reader.readexactly(6), timeout=10)
                length = int.from_bytes(header[4:6], 'big')

                response_data = await asyncio.wait_for(self._reader.readexactly(length), timeout=10)

                raw_data_bytes = response_data[2:-3]
                parsed_response = raw_data_bytes.decode('ascii')
                logger.debug(f"Parsed response for '{command}': {parsed_response}")

                return parsed_response
            except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError) as e:
                logger.error(f"Connection error during send_command for '{command}': {e}")
                await self.disconnect()
                raise

    async def disconnect(self):
        """Disconnects and cleans up resources."""
        async with self._server_lock:
            if self._writer:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass

            if self._server:
                self._server.close()
                await self._server.wait_closed()
                self._server = None

            self._connection_established.clear()
            self._reader = None
            self._writer = None
            logger.info("Client disconnected and server stopped.")
